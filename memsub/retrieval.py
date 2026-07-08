"""Semantic retrieval over the episodic store.

Top-k cosine similarity with recency and importance weighting:

    score = cosine(query, episode) * recency_weight ** age_rank * importance

where age_rank is 0 for the newest indexed episode, 1 for the next, and so on.
Rank-based recency (rather than wall-clock decay) keeps scoring deterministic
in tests and in fast experiment runs where all episodes are written within
milliseconds of each other.

The index is in-memory numpy and is rebuilt from the store on load; for the
scale of these experiments (tens to hundreds of episodes) this is instant.
The interface (add / search) is the slot where a Qdrant collection would go.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from memsub.embed import Embedder, HashingEmbedder
from memsub.store import Episode


@dataclass(frozen=True)
class ScoredEpisode:
    episode: Episode
    score: float


class SemanticIndex:
    def __init__(
        self,
        embedder: Embedder | None = None,
        recency_weight: float = 0.98,
        recency_floor: float = 0.0,
        importance_floor: float = 0.0,
    ) -> None:
        """recency_weight in (0, 1]: 1.0 disables recency preference.

        recency_floor / importance_floor in [0, 1): bound how much recency and
        importance can down-weight a relevant memory. With floor f the factor
        is f + (1 - f) * raw, so f=0 reproduces the raw multiplicative weight
        (v1) and f close to 1 makes the factor a mild tiebreaker (v2). The
        benchmark (docs/benchmark.md) showed raw multiplicative recency
        collapses recall on long histories, which is why the floors exist.
        """
        if not 0 < recency_weight <= 1:
            raise ValueError("recency_weight must be in (0, 1]")
        if not 0 <= recency_floor < 1 or not 0 <= importance_floor < 1:
            raise ValueError("floors must be in [0, 1)")
        self.embedder = embedder or HashingEmbedder()
        self.recency_weight = recency_weight
        self.recency_floor = recency_floor
        self.importance_floor = importance_floor
        self._episodes: list[Episode] = []
        self._vectors: list[np.ndarray] = []

    def add(self, episode: Episode) -> None:
        self._episodes.append(episode)
        self._vectors.append(self.embedder.embed(episode.content))

    def __len__(self) -> int:
        return len(self._episodes)

    def search(self, query: str, k: int = 5) -> list[ScoredEpisode]:
        if not self._episodes:
            return []
        qvec = self.embedder.embed(query)
        mat = np.stack(self._vectors)
        sims = mat @ qvec
        n = len(self._episodes)
        # age_rank: newest episode gets 0
        ranks = np.arange(n - 1, -1, -1, dtype=np.float64)
        recency = self.recency_floor + (1 - self.recency_floor) * self.recency_weight**ranks
        raw_importance = np.array([ep.importance for ep in self._episodes])
        importance = self.importance_floor + (1 - self.importance_floor) * raw_importance
        scores = sims * recency * importance
        order = np.argsort(-scores, kind="stable")[:k]
        return [ScoredEpisode(self._episodes[i], float(scores[i])) for i in order]
