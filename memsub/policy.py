"""Memory policy layer: what gets written, summarized, retrieved, injected.

Token budgets use a chars/4 approximation (documented in LIMITS.md). The
policy is where the memory-type ablation transforms live: episodes can be
written accurately, summarized, or slightly distorted, and the transform is
recorded in episode provenance so runs are auditable.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Callable

from memsub.store import Episode


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def transform_accurate(content: str, rng: random.Random) -> str:
    return content


def transform_summarized(content: str, rng: random.Random) -> str:
    """Keep the first sentence of each line, cap total length.

    Deterministic compression: drops detail while preserving headline claims.
    """
    out_lines: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        first = re.split(r"(?<=[.!?])\s+", line)[0]
        out_lines.append(first)
    summary = " ".join(out_lines)
    return summary[:400]


_NUM_RE = re.compile(r"\b\d+\b")


def transform_distorted(content: str, rng: random.Random) -> str:
    """Slightly distort: jitter roughly one third of the numbers by +/- 1.

    Models an unreliable memory-writing process. Uses the caller-provided rng
    so distortion is reproducible per run seed.
    """

    def jitter(m: re.Match) -> str:
        val = int(m.group(0))
        if rng.random() < 0.34:
            val = max(0, val + rng.choice([-1, 1]))
        return str(val)

    return _NUM_RE.sub(jitter, content)


WRITE_TRANSFORMS: dict[str, Callable[[str, random.Random], str]] = {
    "accurate": transform_accurate,
    "summarized": transform_summarized,
    "distorted": transform_distorted,
}


@dataclass
class MemoryPolicy:
    """Configuration for the memory pipeline.

    write_transform: one of "accurate", "summarized", "distorted".
    retrieval_k: max episodes retrieved per recall.
    injection_budget_tokens: hard cap on injected memory text.
    write_kinds: episode kinds the policy stores (others are ignored).
    seed: drives any stochastic transform (distortion).
    """

    write_transform: str = "accurate"
    retrieval_k: int = 6
    injection_budget_tokens: int = 700
    write_kinds: tuple[str, ...] = ("session_summary", "seeded")
    seed: int = 0
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.write_transform not in WRITE_TRANSFORMS:
            raise ValueError(f"unknown write_transform: {self.write_transform}")
        self._rng = random.Random(self.seed)

    def should_write(self, episode: Episode) -> bool:
        return episode.kind in self.write_kinds

    def apply_write_transform(self, episode: Episode) -> Episode:
        fn = WRITE_TRANSFORMS[self.write_transform]
        new_content = fn(episode.content, self._rng)
        provenance = dict(episode.provenance)
        provenance["write_transform"] = self.write_transform
        if new_content != episode.content:
            provenance["original_content"] = episode.content
        return Episode(
            content=new_content,
            run_id=episode.run_id,
            session_id=episode.session_id,
            kind=episode.kind,
            importance=episode.importance,
            provenance=provenance,
            ts=episode.ts,
            id=episode.id,
        )

    def pack_injection(self, chunks: list[str]) -> str:
        """Concatenate chunks under the token budget, preserving order."""
        packed: list[str] = []
        used = 0
        for chunk in chunks:
            cost = approx_tokens(chunk) + 1
            if used + cost > self.injection_budget_tokens:
                break
            packed.append(chunk)
            used += cost
        return "\n".join(packed)
