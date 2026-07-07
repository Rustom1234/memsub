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
    ) -> None:
        """recency_weight in (0, 1]: 1.0 disables recency preference."""
        if not 0 < recency_weight <= 1:
            raise ValueError("recency_weight must be in (0, 1]")
        self.embedder = embedder or HashingEmbedder()
        self.recency_weight = recency_weight
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
        recency = self.recency_weight**ranks
        importance = np.array([ep.importance for ep in self._episodes])
        scores = sims * recency * importance
        order = np.argsort(-scores, kind="stable")[:k]
        return [ScoredEpisode(self._episodes[i], float(scores[i])) for i in order]
