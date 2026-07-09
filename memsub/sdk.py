"""memsub Python SDK: one class, one SQLite file, zero infrastructure.

    from memsub.sdk import MemoryClient

    client = MemoryClient()                      # ~/.memsub/memory.db
    client.add("This repo disables ROS pytest plugins via pytest.ini")
    print(client.inject("how do we run tests?", budget_tokens=300))

Every search logs retrieval/injection events, so `memsub audit` can always
answer "why did the agent see this?". Namespace ACLs (memsub.namespaces) are
enforced on every call; unregistered namespaces are open by design.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .audit import audit_episode, log_recall
from .embed import Embedder, build_embedder
from .hybrid import HybridRetriever
from .namespaces import NamespaceManager
from .policy import approx_tokens
from .retrieval import SemanticIndex
from .store import Episode, EpisodicStore


def default_db_path() -> Path:
    env = os.environ.get("MEMSUB_DB")
    if env:
        return Path(env)
    return Path.home() / ".memsub" / "memory.db"


@dataclass(frozen=True)
class SearchHit:
    episode: Episode
    score: float


class MemoryClient:
    """Local-first memory for one agent (and any namespaces it may read)."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        agent: str | None = None,
        namespace: str | None = None,
        retrieval: str = "hybrid",
        recency_weight: float = 0.999,
        recency_floor: float = 0.75,
        embedder: Embedder | str | None = None,
    ) -> None:
        """retrieval: "hybrid" (default: the benchmarked v2 stack, stemmed
        BM25 + dense + temporal/speaker/session signals fused with RRF) or
        "dense" (the v1 embedding-only path).

        embedder: an Embedder instance, or one of "hashing" (default: the
        zero-dependency local embedder every benchmark in docs/ uses),
        "ollama" (real embeddings via a local ollama daemon, nomic-embed-text
        by default), or "auto" (ollama if reachable, else hashing). Defaults
        to "hashing" so behavior is unchanged unless a caller opts in."""
        if retrieval not in ("hybrid", "dense"):
            raise ValueError("retrieval must be 'hybrid' or 'dense'")
        if embedder is None or isinstance(embedder, str):
            self.embedder = build_embedder(prefer=embedder or "hashing")
        else:
            self.embedder = embedder
        path = Path(db_path) if db_path else default_db_path()
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.store = EpisodicStore(path)
        self.namespaces = NamespaceManager(self.store)
        self.agent = agent or os.environ.get("MEMSUB_AGENT", "default")
        self.namespace = namespace or os.environ.get("MEMSUB_NAMESPACE", "default")
        self.retrieval = retrieval
        # scoring defaults come from the benchmark (docs/benchmark.md)
        self._recency_weight = recency_weight
        self._recency_floor = recency_floor
        self._index: SemanticIndex | HybridRetriever | None = None
        self._index_ns: str | None = None

    # -- write ---------------------------------------------------------------- #
    def add(
        self,
        content: str,
        kind: str = "fact",
        importance: float = 1.0,
        namespace: str | None = None,
        provenance: dict | None = None,
        session_id: str = "sdk",
    ) -> Episode:
        ns = namespace or self.namespace
        self.namespaces.check(self.agent, "write", ns)
        prov = dict(provenance or {})
        prov.setdefault("origin", "sdk_add")
        prov["written_by"] = self.agent
        ep = Episode(
            content=content,
            run_id="sdk",
            session_id=session_id,
            kind=kind,
            importance=importance,
            provenance=prov,
            namespace=ns,
        )
        stored = self.store.append(ep)
        self._invalidate()
        return stored

    def forget(self, memory_id: str, reason: str = "user_request") -> bool:
        ep = self._resolve(memory_id)
        if ep is None:
            return False
        self.namespaces.check(self.agent, "write", ep.namespace)
        self.store.log_event(ep.id, "forgotten", {"reason": reason, "by_agent": self.agent})
        self._invalidate()
        return True

    # -- read ----------------------------------------------------------------- #
    def search(
        self,
        query: str,
        k: int = 6,
        namespace: str | None = None,
        budget_tokens: int | None = None,
        log: bool = True,
    ) -> list[SearchHit]:
        """Top-k active memories; with budget_tokens, only those that fit."""
        ns = namespace or self.namespace
        self.namespaces.check(self.agent, "read", ns)
        index = self._get_index(ns)
        if isinstance(index, HybridRetriever):
            hits = [SearchHit(ep, score) for ep, score in index.search(query, k=k)]
        else:
            hits = [SearchHit(s.episode, s.score) for s in index.search(query, k=k)]
        packed = hits
        if budget_tokens is not None:
            packed, used = [], 0
            for h in hits:
                cost = approx_tokens(h.episode.content) + 1
                if used + cost > budget_tokens:
                    continue
                packed.append(h)
                used += cost
        if log:
            log_recall(
                self.store,
                query,
                [(h.episode, h.score) for h in hits],
                [h.episode for h in packed],
                budget_tokens=budget_tokens,
            )
        return packed

    def inject(self, query: str, budget_tokens: int = 700, k: int = 24, namespace: str | None = None) -> str:
        """The injection block: best memories that fit the hard budget."""
        hits = self.search(query, k=k, namespace=namespace, budget_tokens=budget_tokens)
        return "\n".join(h.episode.content for h in hits)

    def list(self, namespace: str | None = None, limit: int = 50) -> list[Episode]:
        ns = namespace or self.namespace
        self.namespaces.check(self.agent, "read", ns)
        eps = self.store.active(namespace=ns)
        return eps[-limit:]

    def audit(self, memory_id: str) -> dict | None:
        ep = self._resolve(memory_id)
        if ep is None:
            return None
        self.namespaces.check(self.agent, "read", ep.namespace)
        return audit_episode(self.store, ep.id)

    def close(self) -> None:
        self.store.close()

    # -- internals -------------------------------------------------------------- #
    def _resolve(self, memory_id: str) -> Episode | None:
        ep = self.store.get(memory_id)
        if ep is not None:
            return ep
        matches = [e for e in self.store.all() if e.id.startswith(memory_id)]
        return matches[0] if len(matches) == 1 else None

    def _get_index(self, namespace: str) -> "SemanticIndex | HybridRetriever":
        if self._index is None or self._index_ns != namespace:
            active = self.store.active(namespace=namespace)
            if self.retrieval == "hybrid":
                index: SemanticIndex | HybridRetriever = HybridRetriever(
                    recency_weight=self._recency_weight, recency_floor=self._recency_floor,
                    embedder=self.embedder,
                )
                index.ingest(active)
            else:
                index = SemanticIndex(
                    recency_weight=self._recency_weight, recency_floor=self._recency_floor,
                    embedder=self.embedder,
                )
                for ep in active:
                    index.add(ep)
            self._index, self._index_ns = index, namespace
        return self._index

    def _invalidate(self) -> None:
        self._index = None
        self._index_ns = None
