"""memsub: a small, file-backed episodic memory library for agents.

Four-part decomposition (see docs/memory-design.md):
  - EpisodicStore: append-only episode log (SQLite)
  - SemanticIndex: embedding-based top-k retrieval with recency and importance weighting
  - StructuredStore: queryable facts extracted from episodes (SQLite)
  - MemoryPolicy: what gets written, summarized, retrieved, and injected, under token budgets

Memory levels (concept log levels 1 and 2) behind one interface:
  - NullMemory: no memory (experimental control)
  - ContextStuffingMemory: level 1, recency-ordered stuffing under a budget
  - RetrievalMemory: level 2, semantic + structured retrieval with a policy layer

All storage is local and file-backed (no Docker, no servers). The store
interfaces are small on purpose so a Graphiti or Qdrant backend can be slotted
in later without touching the experiment harness.
"""

from memsub.store import Episode, EpisodicStore
from memsub.embed import Embedder, HashingEmbedder, OllamaEmbedder, build_embedder
from memsub.backends import OllamaBackend, build_backend
from memsub.retrieval import SemanticIndex, ScoredEpisode
from memsub.structured import Fact, StructuredStore
from memsub.policy import MemoryPolicy
from memsub.memory import MemoryBackend, NullMemory, ContextStuffingMemory, RetrievalMemory

__all__ = [
    "Episode",
    "EpisodicStore",
    "Embedder",
    "HashingEmbedder",
    "OllamaEmbedder",
    "build_embedder",
    "OllamaBackend",
    "build_backend",
    "SemanticIndex",
    "ScoredEpisode",
    "Fact",
    "StructuredStore",
    "MemoryPolicy",
    "MemoryBackend",
    "NullMemory",
    "ContextStuffingMemory",
    "RetrievalMemory",
]
