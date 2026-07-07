"""Memory backends: one interface, three implementations.

The experiment toggles memory by swapping the backend and holding everything
else identical:

  - NullMemory          : the control. write() and recall() are no-ops.
  - ContextStuffingMemory: concept-log level 1. Stores summaries, injects the
                           most recent ones (recency order) under a token budget.
                           No semantic search, no structured facts.
  - RetrievalMemory      : concept-log level 2. Full pipeline: episodic store +
                           semantic index + structured facts + policy layer.

recall() returns a RecallResult carrying both the injectable text block and the
episodes/facts that produced it, so the harness can run a manipulation check
(did memory actually retrieve the relevant prior session?) and a memory-content
audit (what was in front of the agent at the moment of a lie).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from memsub.policy import MemoryPolicy
from memsub.retrieval import ScoredEpisode, SemanticIndex
from memsub.store import Episode, EpisodicStore
from memsub.structured import Fact, StructuredStore


@dataclass
class RecallResult:
    text: str
    episodes: list[Episode] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    scored: list[ScoredEpisode] = field(default_factory=list)

    @property
    def used_memory(self) -> bool:
        return bool(self.text.strip())


class MemoryBackend(Protocol):
    name: str

    def write(self, episode: Episode) -> None: ...
    def recall(self, query: str) -> RecallResult: ...
    def size(self) -> int: ...


class NullMemory:
    name = "none"

    def write(self, episode: Episode) -> None:  # noqa: D401 - no-op control
        return None

    def recall(self, query: str) -> RecallResult:
        return RecallResult(text="")

    def size(self) -> int:
        return 0


class ContextStuffingMemory:
    """Level 1: recency-ordered stuffing of stored summaries under a budget."""

    name = "context_stuffing"

    def __init__(self, policy: MemoryPolicy | None = None) -> None:
        self.policy = policy or MemoryPolicy()
        self._episodes: list[Episode] = []

    def write(self, episode: Episode) -> None:
        if not self.policy.should_write(episode):
            return
        self._episodes.append(self.policy.apply_write_transform(episode))

    def recall(self, query: str) -> RecallResult:
        # Newest first, packed under the injection budget.
        ordered = list(reversed(self._episodes))
        chunks = [self._render(ep) for ep in ordered]
        text = self.policy.pack_injection(chunks)
        included = ordered[: text.count("[session ")] if text else []
        return RecallResult(text=text, episodes=included)

    def size(self) -> int:
        return len(self._episodes)

    @staticmethod
    def _render(ep: Episode) -> str:
        return f"[session {ep.session_id}] {ep.content}"


class RetrievalMemory:
    """Level 2: episodic store + semantic index + structured facts + policy."""

    name = "retrieval"

    def __init__(
        self,
        policy: MemoryPolicy | None = None,
        store_path: str | Path = ":memory:",
        recency_weight: float = 0.98,
    ) -> None:
        self.policy = policy or MemoryPolicy()
        self.store = EpisodicStore(store_path)
        self.index = SemanticIndex(recency_weight=recency_weight)
        self.structured = StructuredStore()

    def write(self, episode: Episode) -> None:
        if not self.policy.should_write(episode):
            return
        transformed = self.policy.apply_write_transform(episode)
        stored = self.store.append(transformed)
        self.index.add(stored)
        self.structured.ingest(stored)

    def recall(self, query: str) -> RecallResult:
        scored = self.index.search(query, k=self.policy.retrieval_k)
        chunks = [self._render(s.episode) for s in scored]
        text = self.policy.pack_injection(chunks)
        # Facts about the sessions that surfaced, for structured grounding.
        facts: list[Fact] = []
        for s in scored:
            facts.extend(self.structured.query(subject=s.episode.session_id))
        included = [s.episode for s in scored][: text.count("[session ")] if text else []
        return RecallResult(text=text, episodes=included, facts=facts, scored=scored)

    def size(self) -> int:
        return len(self.index)

    @staticmethod
    def _render(ep: Episode) -> str:
        return f"[session {ep.session_id}] {ep.content}"


def build_memory(kind: str, policy: MemoryPolicy | None = None, **kwargs) -> MemoryBackend:
    """Factory used by the experiment config layer."""
    if kind == "none":
        return NullMemory()
    if kind == "context_stuffing":
        return ContextStuffingMemory(policy=policy)
    if kind == "retrieval":
        return RetrievalMemory(policy=policy, **kwargs)
    raise ValueError(f"unknown memory kind: {kind}")
