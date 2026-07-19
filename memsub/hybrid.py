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

import numpy as np

from .backends import LLMBackend
from .embed import Embedder, HashingEmbedder
from .graph import graph_episode_order
from .lexical import BM25Index, rrf_fuse, tokenize
from .store import Episode, EpisodicStore

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
