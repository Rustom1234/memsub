"""Local, deterministic text embeddings.

No external services are available on this machine, so the default embedder is
a hashed bag-of-features model: word unigrams plus character trigrams hashed
into a fixed-dimensional vector, l2-normalized. It is deterministic across
processes (stable hash), cheap, and good enough for the ranking behavior the
experiments need (matching session summaries against a query about tasks and
reports). The Embedder protocol lets a real embedding API or a Qdrant-backed
index replace it later without touching callers.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> np.ndarray: ...


def _stable_hash(token: str) -> int:
    """Process-independent hash (Python's hash() is salted per process)."""
    return int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:4], "little")


class HashingEmbedder:
    """Deterministic hashed bag-of-features embedder."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float64)
        lowered = text.lower()
        words = _TOKEN_RE.findall(lowered)
        features: list[str] = []
        features.extend("w:" + w for w in words)
        for w in words:
            padded = f"^{w}$"
            features.extend("c:" + padded[i : i + 3] for i in range(len(padded) - 2))
        for feat in features:
            h = _stable_hash(feat)
            idx = h % self.dim
            sign = 1.0 if (h >> 31) & 1 else -1.0
            vec[idx] += sign
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec
