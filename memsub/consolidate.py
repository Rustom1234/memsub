"""Consolidation: an explicit offline pass over the active memory set.

Three phases, in order:

1. Dedup: near-identical active memories (cosine similarity over the local
   embedder above a threshold) are collapsed. The newest copy survives, the
   older ones get a "superseded" event with reason "duplicate".
2. Contradiction: same-kind pairs in a similarity band below the dedup
   threshold are judged by the LLM backend. When two memories contradict,
   the newer one supersedes the older (new fact beats stale fact). Requires
   a backend; skipped cleanly without one.
3. Decay: low-value memories are forgotten. Value is importance discounted by
   age (half-life) and boosted by how often the memory was actually retrieved.
   Only memories older than a minimum age are eligible.

Nothing is ever deleted: supersede and forget are events in the store's event
log, so every decision is auditable and reversible in principle.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import numpy as np

from .backends import LLMBackend
from .embed import Embedder, HashingEmbedder
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


@dataclass(frozen=True)
class Action:
    """One consolidation decision, applied as an event unless dry-run."""

    action: str  # "supersede" | "forget"
    episode_id: str
    reason: str  # "duplicate" | "contradiction" | "decay"
    detail: dict = field(default_factory=dict)


def _decay_value(ep: Episode, retrieved_count: int, now: float, cfg: ConsolidationConfig) -> float:
    age_days = max(now - ep.ts, 0.0) / 86400.0
    value = ep.importance * 0.5 ** (age_days / cfg.decay_half_life_days)
    return value * (1.0 + retrieved_count)


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
    start, end = response.find("["), response.rfind("]")
    if start == -1 or end <= start:
        return verdicts
    try:
        data = json.loads(response[start : end + 1])
    except json.JSONDecodeError:
        return verdicts
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("pair", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(pairs):
            verdicts[idx] = bool(item.get("contradicts", False))
    return verdicts


def consolidate(
    store: EpisodicStore,
    backend: LLMBackend | None = None,
    config: ConsolidationConfig | None = None,
    embedder: Embedder | None = None,
    run_id: str | None = None,
    namespace: str | None = None,
    now: float | None = None,
    dry_run: bool = False,
) -> list[Action]:
    """Run the full consolidation pass. Returns the actions taken (or proposed).

    Consolidation never crosses namespace boundaries when namespace is given;
    with namespace=None it treats each namespace separately.
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
                )
            )
        return actions

    episodes = store.active(run_id=run_id, namespace=namespace)
    actions: list[Action] = []
    if not episodes:
        return actions

    vectors = np.stack([emb.embed(ep.content) for ep in episodes])
    sims = vectors @ vectors.T
    removed: set[str] = set()

    # -- phase 1: dedup (newest copy survives) ------------------------------ #
    contradiction_pairs: list[tuple[Episode, Episode]] = []
    for i in range(len(episodes)):
        for j in range(i + 1, len(episodes)):
            a, b = episodes[i], episodes[j]  # a older (store is ts-ordered)
            if a.id in removed or b.id in removed:
                continue
            sim = float(sims[i, j])
            if sim >= cfg.dedup_threshold:
                actions.append(
                    Action(
                        action="supersede",
                        episode_id=a.id,
                        reason="duplicate",
                        detail={"by": b.id, "similarity": round(sim, 4)},
                    )
                )
                removed.add(a.id)
            elif (
                sim >= cfg.contradiction_min_sim
                and a.kind == b.kind
                and len(contradiction_pairs) < cfg.max_llm_pairs
            ):
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
            actions.append(
                Action(
                    action="supersede",
                    episode_id=old.id,
                    reason="contradiction",
                    detail={"by": new.id, "judge_model": backend.model},
                )
            )
            removed.add(old.id)

    # -- phase 3: decay ------------------------------------------------------ #
    for ep in episodes:
        if ep.id in removed:
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
            event = "superseded" if act.action == "supersede" else "forgotten"
            store.log_event(act.episode_id, event, {"reason": act.reason, **act.detail})
    return actions
