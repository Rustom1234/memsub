"""Graph layer: a bipartite entity-episode index over the episodic store.

Design source: memory-sublayer-research/oss-source-dive/report-2026-07-18.md
section 4 (Synthesis), itself grounded in a read of mem0/Graphiti/cognee's
actual source. Two SQLite tables (memsub/store.py: entities, entity_links),
no external graph DB, no embedding index for entities -- SQL joins are enough
at this scale. Rule-based extraction is the default and common case; an LLM
is spent only on the rare ambiguous entity-dedup pair, never per episode.

Everything here is opt-in: nothing in this module runs unless a caller
explicitly invokes graph_store() (memsub.consolidate's enable_graph flag, or
`memsub graph backfill`) or HybridRetriever.rank_graph/search_graph
(memsub/hybrid.py). The default consolidate()/rank()/search() paths never
import or touch this module's write path.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field

from .backends import LLMBackend
from .sleep import _extract_entities
from .store import Entity, Episode, EpisodicStore

GRAPHED_EVENT = "graphed"  # mirrors memsub.sleep's "slept" idempotency marker

# difflib.SequenceMatcher.ratio() threshold for "this pair is ambiguous
# enough to be worth one LLM call", tuned by inspection (catches
# "Melanie"/"Melany", "Lisbon trip"/"the Lisbon trip"), not against a
# labeled set -- see WORKLOG.md assumption 3.
NEAR_MISS_THRESHOLD = 0.82

RESOLUTION_SYSTEM = """You judge whether two short entity names refer to the same \
real-world person, place, or topic (e.g. a typo, nickname, or partial mention of the \
same thing), or are genuinely different entities.

Output ONLY a JSON object: {"same": true|false}"""


def normalize_entity(text: str) -> str:
    """Deterministic normalization: lowercase, collapsed whitespace."""
    return " ".join(text.strip().lower().split())


def entity_id(namespace: str, normalized: str) -> str:
    """Namespace-scoped entity id (WORKLOG.md assumption 1: the report's DDL
    sketch uses bare normalized text as the id, which would collide two
    namespaces' same-named entity into one row)."""
    return f"{namespace}::{normalized}"


def _norm_suffix(eid: str) -> str:
    """Recover the normalized text half of an entity_id (inverse of the
    namespace-scoping in entity_id(), used when scanning existing rows)."""
    return eid.split("::", 1)[1] if "::" in eid else eid


def guess_entity_type(is_speaker: bool) -> str:
    """PERSON for a text that was the "Speaker: ..." prefix of its source
    episode, else TOPIC. Rule-based, zero cost (WORKLOG.md assumption 2)."""
    return "PERSON" if is_speaker else "TOPIC"


def extract_episode_entities(episode: Episode) -> set[str]:
    """Rule-based entity candidates for one episode. Reuses
    memsub.sleep._extract_entities unchanged rather than duplicating it."""
    return _extract_entities(episode.content)


def _speaker_of(content: str) -> str | None:
    speaker, sep, _ = content.partition(": ")
    return speaker if sep and speaker and speaker[0].isupper() else None


def _parse_same(response: str) -> bool:
    start, end = response.find("{"), response.rfind("}")
    if start == -1 or end <= start:
        return False
    try:
        data = json.loads(response[start : end + 1])
    except json.JSONDecodeError:
        return False
    return bool(data.get("same", False)) if isinstance(data, dict) else False


@dataclass
class EntityResolver:
    """Staged dedup funnel (Graphiti's pattern, report section 2/4):

      1. exact normalized match against an existing entities row -> reuse,
         zero cost.
      2. near-miss (difflib ratio >= NEAR_MISS_THRESHOLD) against existing
         entities in the same namespace -> the one genuinely ambiguous case:
         resolved with exactly one LLM call, cached so a repeat pair within
         this resolver's lifetime never asks twice. No backend -> degrades
         to "not the same" (a clean miss becomes a new entity) rather than
         guessing.
      3. no near miss -> new entity.

    One instance = one bounded resolution cache; see WORKLOG.md assumption 4
    for why the cache is per-run, not persisted across processes.
    """

    backend: LLMBackend | None = None
    _cache: dict[tuple[str, str], bool] = field(default_factory=dict)
    llm_calls: int = 0

    def resolve(
        self, text: str, namespace: str, store: EpisodicStore, is_speaker: bool = False
    ) -> tuple[str, str]:
        """Return (entity_id, entity_type) for `text` in `namespace`. Does
        not write to the store; the caller upserts."""
        norm = normalize_entity(text)
        if not norm:
            return "", ""
        candidate_id = entity_id(namespace, norm)
        existing = store.get_entity(candidate_id)
        if existing is not None:
            return existing.id, existing.entity_type
        near = self._find_near_miss(norm, namespace, store)
        if near is not None:
            near_ent, near_norm = near
            if self._same(norm, near_norm):
                return near_ent.id, near_ent.entity_type
        return candidate_id, guess_entity_type(is_speaker)

    def _find_near_miss(
        self, norm: str, namespace: str, store: EpisodicStore
    ) -> tuple[Entity, str] | None:
        best: tuple[Entity, str] | None = None
        best_ratio = 0.0
        for ent in store.entities_in_namespace(namespace):
            ent_norm = _norm_suffix(ent.id)
            if ent_norm == norm:
                continue  # exact matches are handled before this is reached
            ratio = difflib.SequenceMatcher(None, norm, ent_norm).ratio()
            if ratio >= NEAR_MISS_THRESHOLD and ratio > best_ratio:
                best, best_ratio = (ent, ent_norm), ratio
        return best

    def _same(self, a: str, b: str) -> bool:
        key = tuple(sorted((a, b)))
        if key in self._cache:
            return self._cache[key]
        if self.backend is None:
            same = False  # never guess without a backend: clean miss
        else:
            response = self.backend.complete(
                RESOLUTION_SYSTEM, [{"role": "user", "content": f'A: "{a}"\nB: "{b}"'}]
            )
            same = _parse_same(response)
            self.llm_calls += 1
        self._cache[key] = same
        return same


@dataclass(frozen=True)
class GraphResult:
    episodes_processed: int
    entities_upserted: int
    links_created: int
    llm_calls: int


def graph_store(
    store: EpisodicStore,
    backend: LLMBackend | None = None,
    run_id: str | None = None,
    namespace: str | None = None,
    dry_run: bool = False,
) -> GraphResult:
    """Incremental, idempotent graph indexing over a real store: only active
    episodes that have never been graph-indexed before (no "graphed" event)
    are processed, exactly mirroring memsub.sleep.sleep_store's "slept"
    pattern. Namespace-aware by construction: each episode is resolved
    against its own ep.namespace, so namespace=None (the default) safely
    indexes every namespace in one pass without crossing entity ids between
    them (memsub/graph.py entity_id() is namespace-scoped).

    This is the one write-path implementation reused by both the
    `memsub graph backfill` CLI command and consolidate()'s opt-in
    enable_graph flag (WORKLOG.md assumption 5).
    """
    already = {e.episode_id for e in store.events(event=GRAPHED_EVENT)}
    candidates = [
        ep
        for ep in store.active(run_id=run_id, namespace=namespace)
        if ep.id not in already
    ]
    resolver = EntityResolver(backend=backend)
    n_entities = 0
    n_links = 0
    for ep in candidates:
        speaker = _speaker_of(ep.content)
        for text in extract_episode_entities(ep):
            eid, etype = resolver.resolve(
                text, ep.namespace, store, is_speaker=(text == speaker)
            )
            if not eid:
                continue
            if not dry_run:
                if store.upsert_entity(eid, ep.namespace, etype, ep.ts):
                    n_entities += 1
                if store.link_entity(eid, ep.id, ep.ts):
                    n_links += 1
        if not dry_run:
            store.log_event(ep.id, GRAPHED_EVENT, {})
    return GraphResult(
        episodes_processed=len(candidates),
        entities_upserted=n_entities,
        links_created=n_links,
        llm_calls=resolver.llm_calls,
    )


def _match_query_entities(query: str, store: EpisodicStore, namespaces: set[str]) -> set[str]:
    """Query-side entity lookup: same rule-based extractor as write time, no
    LLM (exact id match, then difflib fuzzy fallback only)."""
    texts = _extract_entities(query)
    if not texts:
        return set()
    matched: set[str] = set()
    for ns in namespaces:
        by_norm: dict[str, Entity] | None = None
        for text in texts:
            norm = normalize_entity(text)
            ent = store.get_entity(entity_id(ns, norm))
            if ent is None:
                if by_norm is None:
                    by_norm = {_norm_suffix(e.id): e for e in store.entities_in_namespace(ns)}
                best, best_ratio = None, 0.0
                for ent_norm, candidate in by_norm.items():
                    ratio = difflib.SequenceMatcher(None, norm, ent_norm).ratio()
                    if ratio >= NEAR_MISS_THRESHOLD and ratio > best_ratio:
                        best, best_ratio = candidate, ratio
                ent = best
            if ent is not None:
                matched.add(ent.id)
    return matched


def graph_episode_order(
    query: str, store: EpisodicStore, episodes: list[Episode], hops: int = 1
) -> list[int]:
    """Indices into `episodes` reachable within `hops` entity-episode hops of
    query's rule-based entities, newest first (SQL joins carry no relevance
    score of their own; recency is the tie-break, same bias as every other
    HybridRetriever signal). Bounded by construction: hops=1 (default) is one
    join, hops>=2 is exactly one more entity-episode hop pair, never an
    open-ended walk. No LLM calls at query time.
    """
    if not episodes:
        return []
    namespaces = {ep.namespace for ep in episodes}
    matched_ids = _match_query_entities(query, store, namespaces)
    if not matched_ids:
        return []
    episode_ids = set(store.episode_ids_for_entities(matched_ids))
    if hops >= 2 and episode_ids:
        more_entities: set[str] = set()
        for eid in episode_ids:
            more_entities.update(e.id for e in store.entities_for_episode(eid))
        more_entities -= matched_ids
        if more_entities:
            episode_ids.update(store.episode_ids_for_entities(more_entities))
    id_to_idx = {ep.id: i for i, ep in enumerate(episodes)}
    hits = [id_to_idx[eid] for eid in episode_ids if eid in id_to_idx]
    return sorted(hits, key=lambda i: -episodes[i].ts)
