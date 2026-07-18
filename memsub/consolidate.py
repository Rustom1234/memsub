"""Consolidation: an explicit offline pass over the active memory set.

Zero. Quarantine: every active episode's write-channel signature
(memsub.provenance) is verified first. A row whose signature fails
verification (tampered, or claims a channel it cannot prove) is quarantined:
excluded from every phase below, from retrieval (store.active() no longer
returns it once the event is logged), and from winning any supersede
decision -- never silently deleted, always flagged in the audit trail.

1. Dedup: near-identical active memories (cosine similarity over the local
   embedder above a threshold) are collapsed. The newest copy survives, the
   older ones get a "superseded" event with reason "duplicate" -- EXCEPT
   when the two sides carry different channel trust rank (see
   memsub.provenance and _trust_rank below): the higher-trust side always
   survives regardless of recency. This is the fix for the SMSR blind spot
   (arXiv:2606.12703): trust is decided by a memory's cryptographically
   verified write channel, never by what its content or kind claims about
   itself.
2. Contradiction: same-kind pairs in a similarity band below the dedup
   threshold are judged by the LLM backend. When two memories contradict,
   the newer one supersedes the older (new fact beats stale fact), with the
   same channel-trust override as phase 1. Requires a backend; skipped
   cleanly without one.
3. Fusion (MIRIX-style write-time consolidation, arXiv:2507.07957): memories
   about the same entity, scattered across different sessions, are grouped
   (memsub.sleep._extract_entities finds the entity candidates; the dedup
   phase's own cosine-similarity matrix picks the most mutually-coherent
   members when a group is larger than the cap) and judged by the LLM
   backend, one call per group. A group the model judges worth fusing
   becomes a new "fusion" episode linked to its sources via
   provenance["fused_from"]; sources are never deleted or superseded, they
   just gain a non-terminal "fused_into" event. Requires a backend; skipped
   cleanly without one, same as contradiction. Idempotent: the fused
   episode's id is a deterministic hash of (namespace, entity, sorted source
   ids), so re-running against an unchanged store finds the row already
   there and makes no new LLM call.
4. Decay: low-value memories are forgotten. Value is importance discounted by
   age (half-life) and boosted by how often the memory was actually retrieved.
   Only memories older than a minimum age are eligible. Episodes carrying a
   "confirmed" event (memsub.vault.apply_vault, a human pin) are skipped by
   decay unconditionally.

Nothing is ever deleted: supersede, forget, and quarantine are events in the
store's event log, so every decision is auditable and reversible in principle.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from . import graph, provenance
from .backends import LLMBackend
from .embed import Embedder, HashingEmbedder
from .sleep import _extract_entities
from .store import Episode, EpisodicStore

CONTRADICTION_SYSTEM = """You judge whether two remembered statements contradict each other.

Two memories contradict when both cannot be true at the same time (a changed
value, a reversed decision, an updated preference). Related-but-compatible
statements do NOT contradict.

You get numbered pairs. Output ONLY a JSON array, one element per pair:
{"pair": <n>, "contradicts": true|false}"""


@dataclass(frozen=True)
class ConsolidationConfig:
    dedup_threshold: float = 0.92
    contradiction_min_sim: float = 0.45
    max_llm_pairs: int = 40
    decay_half_life_days: float = 30.0
    decay_min_age_days: float = 14.0
    decay_value_threshold: float = 0.2
    fusion_min_sessions: int = 2  # an entity must span >=N sessions to be fused
    fusion_max_group_size: int = 6  # cap on sources per fusion LLM call
    max_fusion_groups: int = 15  # cap on fusion LLM calls per namespace per run


@dataclass(frozen=True)
class Action:
    """One consolidation decision, applied as an event unless dry-run."""

    action: str  # "supersede" | "forget" | "fuse" | "quarantine"
    episode_id: str
    reason: str  # "duplicate" | "contradiction" | "decay" | "fusion" | "signature_invalid"
    detail: dict = field(default_factory=dict)


# action -> the event name logged for it (fuse is handled separately: it
# appends a new episode rather than changing an existing one's status).
_ACTION_EVENT = {"supersede": "superseded", "forget": "forgotten", "quarantine": "quarantined"}


def _is_inferred(ep: Episode) -> bool:
    """True for memsub-sleep-generated inferences (memsub.sleep): conclusions
    the model drew, not things directly observed. Kept as the fallback signal
    for episodes with no channel signature (unsigned-legacy, written before
    memsub.provenance existed); _trust_rank below is the real trust ordering
    for everything else."""
    return ep.kind == "inference" or bool(ep.provenance.get("inferred", False))


# Trust ranks: higher wins a tie-break regardless of recency. Quarantined
# (-1) always loses. A signed user_stated row (2) can never be outranked,
# including by an unsigned-legacy row (max rank 1) -- the grandfather
# guarantee promised in docs/poisoning-provenance-2026-07-17.md. Legacy rows
# fall back to the pre-provenance heuristic (_is_inferred) so existing data's
# relative ordering is unchanged.
_RANK_QUARANTINED = -1
_RANK_LEGACY_INFERRED = 0
_RANK_LEGACY_OBSERVED = 1
_RANK_VERIFIED_CHANNEL_ONLY = 1  # verified llm_inferred or imported
_RANK_VERIFIED_USER_STATED = 2


def _trust_rank(ep: Episode, key: bytes | None) -> int:
    """A memory's trust rank for the observed-beats-inferred rule, decided by
    its cryptographically verified write channel (memsub.provenance), never
    by its self-described content or kind. This is the fix for SMSR's proven
    blind spot (arXiv:2606.12703): a provenance-free heuristic cannot tell a
    poison disguised as an observed fact from a real one; a channel bound at
    write time by code the content never controls, can."""
    status, channel = provenance.channel_status(ep, key)
    if status == provenance.STATUS_QUARANTINED:
        return _RANK_QUARANTINED
    if status == provenance.STATUS_VERIFIED:
        if channel == provenance.CHANNEL_USER_STATED:
            return _RANK_VERIFIED_USER_STATED
        return _RANK_VERIFIED_CHANNEL_ONLY
    return _RANK_LEGACY_INFERRED if _is_inferred(ep) else _RANK_LEGACY_OBSERVED


def _decay_value(ep: Episode, retrieved_count: int, now: float, cfg: ConsolidationConfig) -> float:
    age_days = max(now - ep.ts, 0.0) / 86400.0
    value = ep.importance * 0.5 ** (age_days / cfg.decay_half_life_days)
    return value * (1.0 + retrieved_count)


_JUDGE_OBJ_RE = re.compile(r"\{[^{}]*\}")


def _parse_judge_items(response: str) -> list[dict]:
    """Pull the verdict objects out of a judge response.

    The prompt asks for a JSON array, and a compliant backend returns one; that
    path is tried first and unchanged. But a smaller local model sometimes
    returns a bare object (a single `{"pair": 0, "contradicts": true}`) or a
    loose sequence of objects instead of an array. Fall back to scanning for
    individual `{...}` objects so a single-pair contradiction is not silently
    dropped just because the model omitted the brackets.
    """
    start, end = response.find("["), response.rfind("]")
    if start != -1 and end > start:
        try:
            data = json.loads(response[start : end + 1])
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except json.JSONDecodeError:
            pass
    items: list[dict] = []
    for m in _JUDGE_OBJ_RE.finditer(response):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "pair" in obj:
            items.append(obj)
    return items


def _judge_contradictions(
    pairs: list[tuple[Episode, Episode]], backend: LLMBackend
) -> list[bool]:
    """Ask the backend which (older, newer) pairs contradict. One call."""
    lines = []
    for i, (old, new) in enumerate(pairs):
        lines.append(f"PAIR {i}:\nA (older): {old.content}\nB (newer): {new.content}")
    response = backend.complete(
        CONTRADICTION_SYSTEM, [{"role": "user", "content": "\n\n".join(lines)}]
    )
    verdicts = [False] * len(pairs)
    for item in _parse_judge_items(response):
        try:
            idx = int(item.get("pair", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(pairs):
            verdicts[idx] = bool(item.get("contradicts", False))
    return verdicts


FUSION_SYSTEM = """You fuse memories about the same entity, gathered from different \
conversation sessions, into one consolidated fact.

Given numbered memories about one entity, decide whether they add up to a genuine \
synthesis (a fused statement combining what is scattered across sessions) or are too \
unrelated or redundant to combine into one sentence. Do not invent details that are not \
in the memories.

Output ONLY a JSON object:
{"fuse": true|false, "content": "<one self-contained fused sentence, empty if fuse is false>"}"""


def _fusion_id(namespace: str, entity: str, source_ids: list[str]) -> str:
    """Deterministic id for a fused episode, keyed on its exact source set.

    Re-running fusion over an unchanged group of sources always produces the
    same id, which is how idempotency is enforced (the caller checks
    store.get(this id) before doing any work, LLM call included)."""
    key = f"{namespace}|{entity}|{'|'.join(sorted(source_ids))}"
    return "fusion:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _fusion_candidates(
    episodes: list[Episode], removed: set[str], cfg: ConsolidationConfig
) -> list[tuple[str, list[Episode]]]:
    """Group surviving (non-removed, non-fusion) episodes by entity, keeping only
    entities that appear in >= cfg.fusion_min_sessions distinct sessions.

    Most cross-session entities first, capped at cfg.max_fusion_groups so the
    number of LLM calls stays bounded regardless of store size."""
    pool = [ep for ep in episodes if ep.id not in removed and ep.kind != "fusion"]
    entity_eps: dict[str, list[Episode]] = defaultdict(list)
    for ep in pool:
        for entity in _extract_entities(ep.content):
            entity_eps[entity].append(ep)

    groups = [
        (entity, eps)
        for entity, eps in entity_eps.items()
        if len({ep.session_id for ep in eps}) >= cfg.fusion_min_sessions
    ]
    groups.sort(key=lambda kv: (-len({ep.session_id for ep in kv[1]}), kv[0]))
    return groups[: cfg.max_fusion_groups]


def _select_group_members(
    members: list[Episode], id_to_idx: dict[str, int], sims: np.ndarray, max_size: int
) -> list[Episode]:
    """Trim an oversized fusion group to max_size, keeping the most mutually
    similar members (reuses the embedding-similarity matrix consolidate()
    already computed for dedup, per "use the existing retrieval/embedding
    machinery to find candidates")."""
    if len(members) <= max_size:
        return sorted(members, key=lambda e: e.ts)
    idxs = [id_to_idx[e.id] for e in members]
    coherence = sims[np.ix_(idxs, idxs)].sum(axis=1)
    order = np.argsort(-coherence)
    chosen = [members[i] for i in order[:max_size]]
    return sorted(chosen, key=lambda e: e.ts)


def _parse_fusion_response(response: str) -> dict:
    start, end = response.find("{"), response.rfind("}")
    if start == -1 or end <= start:
        return {"fuse": False, "content": ""}
    try:
        data = json.loads(response[start : end + 1])
    except json.JSONDecodeError:
        return {"fuse": False, "content": ""}
    if not isinstance(data, dict):
        return {"fuse": False, "content": ""}
    return {"fuse": bool(data.get("fuse", False)), "content": str(data.get("content", "")).strip()}


def _judge_fusion(entity: str, members: list[Episode], backend: LLMBackend) -> dict:
    """Ask the backend whether this entity's cross-session memories fuse into
    one statement. One call per group."""
    lines = [f"Entity: {entity}"]
    for i, ep in enumerate(members):
        lines.append(f"[{i}] (session {ep.session_id}): {ep.content}")
    response = backend.complete(FUSION_SYSTEM, [{"role": "user", "content": "\n".join(lines)}])
    return _parse_fusion_response(response)


def consolidate(
    store: EpisodicStore,
    backend: LLMBackend | None = None,
    config: ConsolidationConfig | None = None,
    embedder: Embedder | None = None,
    run_id: str | None = None,
    namespace: str | None = None,
    now: float | None = None,
    dry_run: bool = False,
    enable_graph: bool = False,
) -> list[Action]:
    """Run the full consolidation pass. Returns the actions taken (or proposed).

    Consolidation never crosses namespace boundaries when namespace is given;
    with namespace=None it treats each namespace separately.

    enable_graph: opt-in, off by default (byte-identical to the pre-graph-layer
    behavior when omitted). When True, runs memsub.graph.graph_store after the
    phases below to upsert entities/entity_links for any not-yet-graph-indexed
    active episode. Same backend-optional contract as fusion/contradiction:
    rule-based extraction always runs; the LLM is only spent on genuinely
    ambiguous entity-dedup pairs, and only if a backend is given.
    """
    cfg = config or ConsolidationConfig()
    emb = embedder or HashingEmbedder()
    now = now if now is not None else time.time()

    if namespace is None:
        namespaces = sorted({ep.namespace for ep in store.active(run_id=run_id)})
        actions: list[Action] = []
        for ns in namespaces:
            actions.extend(
                consolidate(
                    store, backend=backend, config=cfg, embedder=emb,
                    run_id=run_id, namespace=ns, now=now, dry_run=dry_run,
                    enable_graph=enable_graph,
                )
            )
        return actions

    episodes = store.active(run_id=run_id, namespace=namespace)
    actions: list[Action] = []
    if not episodes:
        return actions

    # -- phase 0: quarantine (verify every row's write-channel signature) --- #
    # Computed once per episode and reused for the trust-ranked tie-breaks in
    # phases 1 and 2 below, so a channel is only ever cryptographically
    # checked once per consolidation pass. A quarantined row is excluded from
    # every phase that follows (added to `removed` immediately) and, once the
    # quarantine event is logged (non-dry-run), from store.active() itself --
    # this is what keeps it out of retrieval without ever deleting it.
    key = provenance.load_key()
    trust: dict[str, int] = {}
    removed: set[str] = set()
    for ep in episodes:
        rank = _trust_rank(ep, key)
        trust[ep.id] = rank
        if rank == _RANK_QUARANTINED:
            claimed = ep.provenance.get("channel")
            actions.append(
                Action(
                    action="quarantine",
                    episode_id=ep.id,
                    reason="signature_invalid",
                    detail={"claimed_channel": claimed} if claimed else {},
                )
            )
            removed.add(ep.id)

    vectors = np.stack([emb.embed(ep.content) for ep in episodes])
    sims = vectors @ vectors.T

    # -- phase 1: dedup (newest copy survives) ------------------------------ #
    contradiction_pairs: list[tuple[Episode, Episode]] = []
    for i in range(len(episodes)):
        for j in range(i + 1, len(episodes)):
            a, b = episodes[i], episodes[j]  # a older (store is ts-ordered)
            if a.id in removed or b.id in removed:
                continue
            sim = float(sims[i, j])
            if sim >= cfg.dedup_threshold:
                # newest survives, EXCEPT when the two sides have different
                # channel trust rank: the higher-trust side survives
                # regardless of recency. Without this, a newer low-trust
                # write that restates a value near-identically would
                # silently supersede a higher-trust memory here, before the
                # contradiction judge ever saw the pair.
                loser, winner = a, b
                if trust[b.id] < trust[a.id]:
                    loser, winner = b, a
                actions.append(
                    Action(
                        action="supersede",
                        episode_id=loser.id,
                        reason="duplicate",
                        detail={"by": winner.id, "similarity": round(sim, 4)},
                    )
                )
                removed.add(loser.id)
            elif (
                sim >= cfg.contradiction_min_sim
                and (a.kind == b.kind or "inference" in (a.kind, b.kind))
                and len(contradiction_pairs) < cfg.max_llm_pairs
            ):
                # same-kind pairs, or a sleep-generated inference vs anything it
                # might conflict with: inferences must be checkable against the
                # observed facts they were drawn from, not just other inferences.
                contradiction_pairs.append((a, b))

    # -- phase 2: contradictions (needs an LLM backend) ---------------------- #
    contradiction_pairs = [
        (a, b) for a, b in contradiction_pairs if a.id not in removed and b.id not in removed
    ]
    if backend is not None and contradiction_pairs:
        verdicts = _judge_contradictions(contradiction_pairs, backend)
        for (old, new), contradicts in zip(contradiction_pairs, verdicts):
            if not contradicts or old.id in removed or new.id in removed:
                continue
            # newer wins by default, EXCEPT: a lower channel-trust rank never
            # overwrites a higher one (see _trust_rank), regardless of which
            # is newer. A verified llm_inferred memory proposes, it never
            # silently supersedes a verified user_stated one.
            loser, winner = old, new
            if trust[new.id] < trust[old.id]:
                loser, winner = new, old
            actions.append(
                Action(
                    action="supersede",
                    episode_id=loser.id,
                    reason="contradiction",
                    detail={"by": winner.id, "judge_model": backend.model},
                )
            )
            removed.add(loser.id)

    # -- phase 3: fusion (needs an LLM backend) ------------------------------ #
    if backend is not None:
        id_to_idx = {ep.id: i for i, ep in enumerate(episodes)}
        for entity, members in _fusion_candidates(episodes, removed, cfg):
            chosen = _select_group_members(members, id_to_idx, sims, cfg.fusion_max_group_size)
            source_ids = sorted(ep.id for ep in chosen)
            fused_id = _fusion_id(namespace, entity, source_ids)
            if store.get(fused_id) is not None:
                continue  # idempotent: this exact group was already fused
            verdict = _judge_fusion(entity, chosen, backend)
            if not verdict["fuse"] or not verdict["content"]:
                continue
            actions.append(
                Action(
                    action="fuse",
                    episode_id=fused_id,
                    reason="fusion",
                    detail={"entity": entity, "fused_from": source_ids, "n_sources": len(chosen)},
                )
            )
            if not dry_run:
                fused_ep = Episode(
                    content=verdict["content"],
                    run_id=chosen[0].run_id,
                    session_id="consolidate:fusion",
                    kind="fusion",
                    importance=max(ep.importance for ep in chosen),
                    provenance={
                        "origin": "consolidation_fusion",
                        "entity": entity,
                        "fused_from": source_ids,
                        "backend": backend.name,
                        "model": backend.model,
                        "generated_at": now,
                    },
                    namespace=namespace,
                    ts=max(ep.ts for ep in chosen),
                    id=fused_id,
                )
                store.append(fused_ep)
                for ep in chosen:
                    store.log_event(ep.id, "fused_into", {"by": fused_id, "entity": entity})

    # -- phase 4: decay ------------------------------------------------------ #
    for ep in episodes:
        if ep.id in removed:
            continue
        if store.events(episode_id=ep.id, event="confirmed"):
            # human pinned this memory (memsub vault apply, verdict: confirm):
            # protected from decay indefinitely, regardless of age or value.
            continue
        age_days = max(now - ep.ts, 0.0) / 86400.0
        if age_days < cfg.decay_min_age_days:
            continue
        retrieved = len(store.events(episode_id=ep.id, event="retrieved"))
        value = _decay_value(ep, retrieved, now, cfg)
        if value < cfg.decay_value_threshold:
            actions.append(
                Action(
                    action="forget",
                    episode_id=ep.id,
                    reason="decay",
                    detail={
                        "value": round(value, 4),
                        "age_days": round(age_days, 2),
                        "retrieved_count": retrieved,
                    },
                )
            )
            removed.add(ep.id)

    if not dry_run:
        for act in actions:
            if act.action == "fuse":
                continue  # already applied inline above: creates a new episode
                # plus a "fused_into" event on each source, not a status change
                # on an existing episode_id like supersede/forget/quarantine below.
            store.log_event(act.episode_id, _ACTION_EVENT[act.action], {"reason": act.reason, **act.detail})

    # -- phase 5: graph (opt-in, off by default) ----------------------------- #
    # Runs after supersede/forget/quarantine events are applied above so a
    # dry_run pass reports Actions only, exactly like phases 0-4; graph_store
    # itself is namespace-aware (WORKLOG.md assumption 5/6), so this single
    # call, scoped to `namespace`, is enough even though consolidate()'s own
    # namespace=None recursion already fans this call out per namespace.
    if enable_graph:
        graph.graph_store(store, backend=backend, run_id=run_id, namespace=namespace, dry_run=dry_run)

    return actions
