"""HybridRetriever: the retrieval-v2 stack, product-grade and dependency-free.

Fuses four ranking signals with reciprocal rank fusion (Cormack et al.,
SIGIR 2009):

  dense     hashed embeddings x bounded recency x bounded importance
  bm25      stemmed Okapi BM25 over memory text (dates prepended for ranking
            only; injected content never pays tokens for metadata)
  speaker   when the query names exactly one known speaker, that speaker's
            memories ("Name: ..." prefix) form an extra ranking
  session   session-as-pseudo-document BM25 (multi-granularity, RAPTOR-lite)

Defaults are the parameters frozen on the LOCOMO dev split and confirmed on
the one holdout run (docs/benchmark.md: 73.8% evidence-all at budget 1000 vs
42.9% for dense-only). Signals degrade to no-ops when their inputs are absent
(no session dates, no speaker prefixes, single session), so the retriever is
safe as the default for arbitrary memories.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime

import numpy as np

from .backends import LLMBackend
from .embed import Embedder, HashingEmbedder
from .graph import graph_episode_order
from .lexical import BM25Index, rrf_fuse, tokenize
from .rerank import rank_by_model
from .sleep import _extract_entities
from .store import Episode, EpisodicStore
from .temporal import classify_intent, episode_matches_date, ordinal_direction, parse_absolute_date, resolve_relative

DECOMPOSE_SYSTEM_TEMPLATE = (
    "Break the QUESTION into up to {n} short standalone sub-questions whose "
    "answers together would help answer it. If the question is already "
    "simple and single-part, return a JSON array containing just the "
    "original question, unchanged. Output ONLY a JSON array of strings, "
    "no more than {n} items, no other text."
)


def _parse_subqueries(response: str, max_subqueries: int) -> list[str]:
    """Never guesses: an unparseable or malformed response yields no
    sub-queries, so the caller degrades to its plain (non-decomposed)
    ranking rather than inventing a sub-query."""
    start, end = response.find("["), response.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(response[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    subs = [s.strip() for s in data if isinstance(s, str) and s.strip()]
    return subs[:max_subqueries]

_MONTHS = "january february march april may june july august september october november december".split()
DATE_RE = re.compile(
    r"(?:(\d{1,2})\s+)?(" + "|".join(m.capitalize() for m in _MONTHS) + r")(?:,?\s+(\d{4}))?",
)


class HybridRetriever:
    """Rank episodes for a query by fusing dense, lexical, speaker, and
    session signals. Defaults are the benchmark-frozen v2 parameters."""

    def __init__(
        self,
        embedder: Embedder | None = None,
        recency_weight: float = 0.999,
        recency_floor: float = 0.75,
        importance_floor: float = 0.0,
        rrf_k: int = 60,
        dense_weight: float = 0.5,
        bm25_weight: float = 1.0,
        temporal: bool = True,
        temporal_weight: float = 1.0,
        speaker_boost: float = 1.0,
        session_weight: float = 3.0,
        prf_weight: float = 0.0,
        prf_docs: int = 10,
        prf_terms: int = 8,
    ) -> None:
        self.embedder = embedder or HashingEmbedder()
        self.recency_weight = recency_weight
        self.recency_floor = recency_floor
        self.importance_floor = importance_floor
        self.rrf_k = rrf_k
        self.weights = [dense_weight, bm25_weight]
        self.temporal = temporal
        self.temporal_weight = temporal_weight
        self.speaker_boost = speaker_boost
        self.session_weight = session_weight
        self.prf_weight = prf_weight
        self.prf_docs = prf_docs
        self.prf_terms = prf_terms
        self.episodes: list[Episode] = []
        self._vectors: list[np.ndarray] = []
        self._bm25: BM25Index | None = None
        self._session_bm25: BM25Index | None = None
        self._session_ids: list[str] = []
        self._speakers: set[str] = set()

    # -- indexing ---------------------------------------------------------- #
    def ingest(self, episodes: list[Episode]) -> None:
        for ep in sorted(episodes, key=lambda e: e.ts):
            self.episodes.append(ep)
            self._vectors.append(self.embedder.embed(ep.content))
        eps = self.episodes
        if self.temporal:
            docs = [f"{ep.provenance.get('session_date', '')} {ep.content}" for ep in eps]
        else:
            docs = [ep.content for ep in eps]
        self._bm25 = BM25Index(docs)
        self._speakers = {
            ep.content.split(":", 1)[0].strip().lower() for ep in eps if ":" in ep.content[:40]
        }
        if self.session_weight > 0:
            by_session: dict[str, list[str]] = {}
            for ep in eps:
                by_session.setdefault(ep.session_id, []).append(ep.content)
            self._session_ids = list(by_session)
            self._session_bm25 = BM25Index([" ".join(v) for v in by_session.values()])

    def __len__(self) -> int:
        return len(self.episodes)

    # -- signals ------------------------------------------------------------ #
    def _dense_order(self, query: str) -> list[int]:
        qvec = self.embedder.embed(query)
        sims = np.stack(self._vectors) @ qvec
        n = len(self.episodes)
        ranks = np.arange(n - 1, -1, -1, dtype=np.float64)  # newest gets rank 0
        recency = self.recency_floor + (1 - self.recency_floor) * self.recency_weight**ranks
        raw_imp = np.array([ep.importance for ep in self.episodes])
        importance = self.importance_floor + (1 - self.importance_floor) * raw_imp
        scores = sims * recency * importance
        return [int(i) for i in np.argsort(-scores, kind="stable")]

    def _date_match_order(self, query: str, base_order: list[int]) -> list[int] | None:
        m = DATE_RE.search(query)
        if not m:
            return None
        wanted = {p.lower() for p in m.groups() if p}
        matching = []
        for i in base_order:
            date_tokens = {
                t.lower() for t in self.episodes[i].provenance.get("session_date", "").split()
            }
            if wanted and wanted <= date_tokens:
                matching.append(i)
        return matching or None

    # -- ranking ------------------------------------------------------------ #
    def rank(self, query: str) -> list[int]:
        """Indices into self.episodes, best first."""
        eps = self.episodes
        if not eps:
            return []
        dense_order = self._dense_order(query)
        bm = self._bm25.scores(query)
        bm25_order = sorted(range(len(eps)), key=lambda i: (-bm[i], i))
        rankings = [dense_order, bm25_order]
        weights = list(self.weights)
        if self.temporal:
            date_order = self._date_match_order(query, dense_order)
            if date_order is not None:
                rankings.append(date_order)
                weights.append(self.temporal_weight)
        if self.speaker_boost > 0:
            mentioned = {s for s in self._speakers if s and s in query.lower()}
            if len(mentioned) == 1:
                name = next(iter(mentioned))
                by_speaker = [
                    i for i in bm25_order if eps[i].content.lower().startswith(name + ":")
                ]
                if by_speaker:
                    rankings.append(by_speaker)
                    weights.append(self.speaker_boost)
        if self.session_weight > 0 and self._session_bm25 is not None and len(self._session_ids) > 1:
            s_scores = self._session_bm25.scores(query)
            pos = {sid: p for p, sid in enumerate(self._session_ids)}
            ordered_sessions = sorted(self._session_ids, key=lambda s: -s_scores[pos[s]])
            session_rank = {sid: r for r, sid in enumerate(ordered_sessions)}
            bm_pos = {i: p for p, i in enumerate(bm25_order)}
            session_order = sorted(
                range(len(eps)), key=lambda i: (session_rank[eps[i].session_id], bm_pos[i])
            )
            rankings.append(session_order)
            weights.append(self.session_weight)
        if self.prf_weight > 0:
            base_order = rrf_fuse(rankings, k=self.rrf_k, weights=weights)
            q_terms = set(tokenize(query))
            term_scores: Counter = Counter()
            for i in base_order[: self.prf_docs]:
                for t in set(tokenize(eps[i].content)):
                    if t not in q_terms:
                        term_scores[t] += self._bm25._idf.get(t, 0.0)
            expansion = [t for t, _ in term_scores.most_common(self.prf_terms)]
            if expansion:
                exp_scores = self._bm25.scores(query + " " + " ".join(expansion))
                exp_order = sorted(range(len(eps)), key=lambda i: (-exp_scores[i], i))
                rankings.append(exp_order)
                weights.append(self.prf_weight)
        return rrf_fuse(rankings, k=self.rrf_k, weights=weights)

    def search(self, query: str, k: int = 6) -> list[tuple[Episode, float]]:
        """Top-k episodes with a rank-derived score in (0, 1], higher is better.

        The score is 1/(rrf_k + position + 1): monotone in the fused ranking,
        comparable within one query, not across queries."""
        order = self.rank(query)
        return [
            (self.episodes[i], 1.0 / (self.rrf_k + pos + 1))
            for pos, i in enumerate(order[:k])
        ]

    # -- query-time decomposition (opt-in, default rank()/search() unchanged) #
    def _decompose(self, query: str, backend: LLMBackend, max_subqueries: int) -> list[str]:
        """One bounded LLM call: splits `query` into up to max_subqueries
        short sub-queries. A backend error or unparseable response degrades
        to no sub-queries (never guessed) -- callers fall back to their
        plain ranking."""
        system = DECOMPOSE_SYSTEM_TEMPLATE.format(n=max_subqueries)
        try:
            response = backend.complete(system, [{"role": "user", "content": query}])
        except Exception:
            return []
        return _parse_subqueries(response, max_subqueries)

    def rank_decomposed(self, query: str, backend: LLMBackend, max_subqueries: int = 2) -> list[int]:
        """Bounded query-time decomposition: one LLM call splits `query`
        into up to max_subqueries sub-queries; the base ranking plus each
        sub-query's own ranking (<=1 + max_subqueries retrieval passes
        total, e.g. <=3 when max_subqueries=2) are RRF-fused together.
        Opt-in -- rank()/search() are unchanged. Degrades to plain rank()
        when decomposition yields nothing."""
        base_order = self.rank(query)
        subqueries = self._decompose(query, backend, max_subqueries)
        if not subqueries:
            return base_order
        rankings = [base_order] + [self.rank(sq) for sq in subqueries]
        return rrf_fuse(rankings, k=self.rrf_k, weights=[1.0] * len(rankings))

    def search_decomposed(
        self, query: str, backend: LLMBackend, k: int = 6, max_subqueries: int = 2
    ) -> list[tuple[Episode, float]]:
        order = self.rank_decomposed(query, backend, max_subqueries=max_subqueries)
        return [
            (self.episodes[i], 1.0 / (self.rrf_k + pos + 1))
            for pos, i in enumerate(order[:k])
        ]

    # -- graph layer query path (opt-in, memsub/graph.py's write path) ----- #
    def rank_graph(self, query: str, store: EpisodicStore, hops: int = 1) -> list[int]:
        """Fold the graph co-mention signal (memsub.graph.graph_episode_order)
        into the base RRF fusion as one more ranked list, mirroring rank()'s
        own pattern of adding a signal + weight. Falls back to plain rank()
        when the store has no populated graph index (graph_store() never
        called), so this is always safe to call."""
        base_order = self.rank(query)
        if store.entity_count() == 0:
            return base_order
        graph_order = graph_episode_order(query, store, self.episodes, hops=hops)
        if not graph_order:
            return base_order
        return rrf_fuse([base_order, graph_order], k=self.rrf_k, weights=[1.0, 1.0])

    def search_graph(
        self, query: str, store: EpisodicStore, k: int = 6, hops: int = 1
    ) -> list[tuple[Episode, float]]:
        order = self.rank_graph(query, store, hops=hops)
        return [
            (self.episodes[i], 1.0 / (self.rrf_k + pos + 1))
            for pos, i in enumerate(order[:k])
        ]

    # -- composition: decompose first, then graph-augment each sub-query --- #
    def rank_decomposed_graph(
        self,
        query: str,
        backend: LLMBackend,
        store: EpisodicStore,
        max_subqueries: int = 2,
        hops: int = 1,
    ) -> list[int]:
        """Step 4 of the graph layer: decompose first, then graph-augment
        each sub-query's retrieval (rank_graph), folding the original
        query's own graph-augmented ranking plus each sub-query's into one
        RRF fusion. Degrades to rank_graph(query) when decomposition yields
        nothing."""
        base_order = self.rank_graph(query, store, hops=hops)
        subqueries = self._decompose(query, backend, max_subqueries)
        if not subqueries:
            return base_order
        rankings = [base_order] + [self.rank_graph(sq, store, hops=hops) for sq in subqueries]
        return rrf_fuse(rankings, k=self.rrf_k, weights=[1.0] * len(rankings))

    def search_decomposed_graph(
        self,
        query: str,
        backend: LLMBackend,
        store: EpisodicStore,
        k: int = 6,
        max_subqueries: int = 2,
        hops: int = 1,
    ) -> list[tuple[Episode, float]]:
        order = self.rank_decomposed_graph(
            query, backend, store, max_subqueries=max_subqueries, hops=hops
        )
        return [
            (self.episodes[i], 1.0 / (self.rrf_k + pos + 1))
            for pos, i in enumerate(order[:k])
        ]

    # -- temporal layer (opt-in, memsub/temporal.py's query-time intent) --- #
    def _now_reference(self) -> date:
        """'Now' for resolving a relative expression inside a query itself:
        the most recent ingested episode's session_date (or its ts), since
        most sources (including LOCOMO) carry no explicit question-asked
        timestamp. A heuristic, not a ground truth -- see WORKLOG.md."""
        if not self.episodes:
            return date.today()
        last = self.episodes[-1]
        session_date = (last.provenance or {}).get("session_date", "")
        if session_date:
            parsed = parse_absolute_date(session_date)
            if parsed is not None:
                return parsed
        return datetime.fromtimestamp(last.ts).date() if last.ts else date.today()

    def _temporal_match_order(self, query: str, ref: date) -> list[int] | None:
        target = parse_absolute_date(query) or resolve_relative(query, ref)
        if target is None:
            return None
        matches = [i for i, ep in enumerate(self.episodes) if episode_matches_date(ep, target)]
        if not matches:
            return None
        return sorted(matches, key=lambda i: -self.episodes[i].ts)

    def _apply_temporal(
        self, base_order: list[int], query: str, intent: str, temporal_weight: float, ordinal_pool: int,
    ) -> list[int]:
        if not base_order:
            return base_order
        if intent == "ordinal":
            direction = ordinal_direction(query)
            pool, rest = base_order[:ordinal_pool], base_order[ordinal_pool:]
            pool_sorted = sorted(
                pool,
                key=lambda i: (self.episodes[i].valid_from or self.episodes[i].ts),
                reverse=(direction == "latest"),
            )
            return pool_sorted + rest
        if intent in ("point_in_time", "range"):
            match_order = self._temporal_match_order(query, self._now_reference())
            if match_order is not None:
                return rrf_fuse([base_order, match_order], k=self.rrf_k, weights=[1.0, temporal_weight])
        return base_order

    def rank_temporal(
        self,
        query: str,
        intent: str | None = None,
        backend: LLMBackend | None = None,
        temporal_weight: float = 2.0,
        ordinal_pool: int = 30,
        base_order: list[int] | None = None,
    ) -> list[int]:
        """Opt-in temporal-aware ranking (docs/temporal-v1-2026-07-20.md).
        Base rank() fused with a valid-window/session-date time-match signal
        for point_in_time/range intents; a pure chronological re-sort of the
        base candidates for ordinal intents. current_state/atemporal degrade
        to plain rank(), unchanged -- rank() itself is never touched.

        base_order: pass a previously computed rank() result to skip
        recomputing it (e.g. an eval comparing "plain" vs "plain+temporal"
        against the exact same base ranking rather than two independent
        calls -- rank() is deterministic so this rarely matters here, unlike
        rank_decomposed_graph_temporal below)."""
        base_order = self.rank(query) if base_order is None else base_order
        intent = intent or classify_intent(query, backend)
        return self._apply_temporal(base_order, query, intent, temporal_weight, ordinal_pool)

    def search_temporal(
        self, query: str, k: int = 6, intent: str | None = None, backend: LLMBackend | None = None,
        temporal_weight: float = 2.0, ordinal_pool: int = 30,
    ) -> list[tuple[Episode, float]]:
        order = self.rank_temporal(
            query, intent=intent, backend=backend, temporal_weight=temporal_weight, ordinal_pool=ordinal_pool
        )
        return [
            (self.episodes[i], 1.0 / (self.rrf_k + pos + 1))
            for pos, i in enumerate(order[:k])
        ]

    # -- composition: graph + decompose, then temporal on top -------------- #
    def rank_decomposed_graph_temporal(
        self,
        query: str,
        backend: LLMBackend,
        store: EpisodicStore,
        intent: str | None = None,
        temporal_backend: LLMBackend | None = None,
        max_subqueries: int = 2,
        hops: int = 1,
        temporal_weight: float = 2.0,
        ordinal_pool: int = 30,
        base_order: list[int] | None = None,
    ) -> list[int]:
        """Step 5 of the temporal layer: rank_decomposed_graph's existing
        decompose+graph composition, then the same temporal boost/reorder
        rank_temporal applies, folded on top. Degrades to
        rank_decomposed_graph(query) unchanged for current_state/atemporal
        intents.

        base_order: pass a previously computed rank_decomposed_graph(...)
        result to skip recomputing it. Matters here specifically because
        rank_decomposed_graph's one decompose LLM call is NOT deterministic
        (a local model's sampling varies run to run) -- an eval comparing
        "baseline" vs "baseline+temporal" that calls rank_decomposed_graph
        independently for each condition is comparing two different
        decompositions, not isolating the temporal variable (found during
        this task's dev-slice iteration, see WORKLOG.md 2026-07-20)."""
        base_order = (
            self.rank_decomposed_graph(query, backend, store, max_subqueries=max_subqueries, hops=hops)
            if base_order is None else base_order
        )
        intent = intent or classify_intent(query, temporal_backend)
        return self._apply_temporal(base_order, query, intent, temporal_weight, ordinal_pool)

    def search_decomposed_graph_temporal(
        self,
        query: str,
        backend: LLMBackend,
        store: EpisodicStore,
        k: int = 6,
        intent: str | None = None,
        temporal_backend: LLMBackend | None = None,
        max_subqueries: int = 2,
        hops: int = 1,
        temporal_weight: float = 2.0,
        ordinal_pool: int = 30,
    ) -> list[tuple[Episode, float]]:
        order = self.rank_decomposed_graph_temporal(
            query, backend, store, intent=intent, temporal_backend=temporal_backend,
            max_subqueries=max_subqueries, hops=hops, temporal_weight=temporal_weight,
            ordinal_pool=ordinal_pool,
        )
        return [
            (self.episodes[i], 1.0 / (self.rrf_k + pos + 1))
            for pos, i in enumerate(order[:k])
        ]

    # -- learned reranker (2026-07-20, opt-in, rank() untouched) ----------- #
    def candidate_features(
        self, query: str, store: EpisodicStore | None = None, pool_size: int = 50, hops: int = 1,
        base_order: list[int] | None = None,
    ) -> list[dict]:
        """Per-candidate feature dicts for the top `pool_size` of `rank(query)`,
        used both to train the tiny learned reranker (memsub/rerank.py) and
        to build the abstention score's aggregate features
        (memsub/answer_policy.py). Needs no LLM call: `rank()` and
        `graph_episode_order` (called with `backend=None`, its documented
        zero-cost default) are both deterministic.

        base_order: pass a previously computed ranking (e.g.
        `rank_decomposed_graph(...)`) to build features over THAT pool
        instead of a fresh plain `rank(query)` call -- same `base_order`
        fairness pattern as `rank_temporal`/`rank_decomposed_graph_temporal`
        (docs/temporal-v1-2026-07-20.md): lets a caller apply the learned
        reranker on top of its actual serving retrieval order rather than a
        separately-recomputed plain-rank pool.

        Each dict: {"index": int (into self.episodes), "bm25_norm",
        "dense_norm" (both min-max normalized within this pool, so they are
        comparable across queries of different length/specificity),
        "rrf_recip" (1/(rrf_k+pos+1), pos = position in `base_order` --
        monotone in whichever fused ranking produced it), "entity_overlap"
        (fraction of the query's rule-based entities found in this
        candidate's content), "recency" (same bounded recency factor
        rank() itself uses), "temporal_match" (1.0 if this episode's valid
        window/session_date matches a date resolved from the query, else
        0.0), "graph_boost" (1.0 if this episode is within `hops` of a
        matched query entity in `store`'s graph index, else 0.0; always 0.0
        when `store` is None or has no entities indexed)}.
        """
        eps = self.episodes
        base_order = self.rank(query) if base_order is None else base_order
        pool = base_order[:pool_size]
        if not pool:
            return []
        bm = self._bm25.scores(query)
        qvec = self.embedder.embed(query)
        dense = {i: float(self._vectors[i] @ qvec) for i in pool}
        bm_pool = {i: bm[i] for i in pool}

        def _norm(v: float, lo: float, hi: float) -> float:
            return (v - lo) / (hi - lo) if hi > lo else 0.5

        bm_lo, bm_hi = min(bm_pool.values()), max(bm_pool.values())
        d_lo, d_hi = min(dense.values()), max(dense.values())

        query_entities = _extract_entities(query)
        target_date = parse_absolute_date(query) or resolve_relative(query, self._now_reference())

        graph_idx: set[int] = set()
        if store is not None and store.entity_count() > 0:
            graph_idx = set(graph_episode_order(query, store, eps, hops=hops))

        n = len(eps)
        feats = []
        for pos, i in enumerate(pool):
            ep = eps[i]
            ep_entities = _extract_entities(ep.content)
            entity_overlap = (
                len(query_entities & ep_entities) / len(query_entities) if query_entities else 0.0
            )
            recency = self.recency_floor + (1 - self.recency_floor) * self.recency_weight ** (n - 1 - i)
            temporal_match = 1.0 if (target_date is not None and episode_matches_date(ep, target_date)) else 0.0
            feats.append(
                {
                    "index": i,
                    "bm25_norm": _norm(bm_pool[i], bm_lo, bm_hi),
                    "dense_norm": _norm(dense[i], d_lo, d_hi),
                    "rrf_recip": 1.0 / (self.rrf_k + pos + 1),
                    "entity_overlap": entity_overlap,
                    "recency": recency,
                    "temporal_match": temporal_match,
                    "graph_boost": 1.0 if i in graph_idx else 0.0,
                }
            )
        return feats

    def rank_reranked(
        self, query: str, weights: dict, store: EpisodicStore | None = None,
        pool_size: int = 50, hops: int = 1, base_order: list[int] | None = None,
    ) -> list[int]:
        """Opt-in: re-order the top `pool_size` of `base_order` (default:
        `rank(query)`) by the tiny learned reranker's P(gold-supporting)
        score (`weights` is the dict `memsub.rerank.train_logistic`/
        `save_weights` produces); the tail (beyond pool_size) is left in its
        original order, appended after. Degrades to plain rank() (or the
        passed-in base_order) when the pool is empty (empty store) -- never
        drops a candidate, only reorders.

        base_order: pass a previously computed ranking (e.g.
        `rank_decomposed_graph(...)`) to rerank THAT pool instead of a
        freshly recomputed plain rank() -- same fairness pattern as
        `rank_temporal`'s own `base_order` parameter."""
        base_order = self.rank(query) if base_order is None else base_order
        feats = self.candidate_features(query, store=store, pool_size=pool_size, hops=hops, base_order=base_order)
        if not feats:
            return base_order
        reordered = rank_by_model(weights, feats)
        return reordered + base_order[pool_size:]

    def search_reranked(
        self, query: str, weights: dict, store: EpisodicStore | None = None,
        k: int = 6, pool_size: int = 50, hops: int = 1, base_order: list[int] | None = None,
    ) -> list[tuple[Episode, float]]:
        order = self.rank_reranked(
            query, weights, store=store, pool_size=pool_size, hops=hops, base_order=base_order,
        )
        return [
            (self.episodes[i], 1.0 / (self.rrf_k + pos + 1))
            for pos, i in enumerate(order[:k])
        ]
