"""Local text embeddings.

Two embedders behind one Embedder protocol:

  HashingEmbedder  - the zero-dependency, zero-service default: word unigrams
    plus character trigrams hashed into a fixed-dimensional vector,
    l2-normalized. Deterministic across processes, no network, always
    available. Good enough for ranking and what every test uses.

  OllamaEmbedder   - real embeddings from a local ollama daemon
    (nomic-embed-text by default). Opt-in: nothing switches to it silently,
    callers ask for it explicitly (build_embedder(prefer="ollama"/"auto")).

Callers that want a real embedding when one is available, falling back
cleanly when it is not, should use build_embedder("auto").
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from typing import Protocol

import numpy as np

from .backends import OLLAMA_HOST, ollama_available

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


class OllamaEmbedder:
    """Real embeddings from a local ollama daemon (default: nomic-embed-text)."""

    def __init__(
        self, model: str = "nomic-embed-text", host: str = OLLAMA_HOST,
        dim: int = 768, timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.host = host
        self.dim = dim
        self.timeout = timeout

    def embed(self, text: str) -> np.ndarray:
        req = urllib.request.Request(
            f"{self.host}/api/embeddings",
            data=json.dumps({"model": self.model, "prompt": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ollama embeddings request failed: {exc}") from exc
        vec = np.array(data.get("embedding", []), dtype=np.float64)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec


def build_embedder(prefer: str = "auto", model: str | None = None) -> "Embedder":
    """Select an embedder.

    prefer:
      "auto"    - OllamaEmbedder if a local ollama daemon is reachable,
                  else HashingEmbedder.
      "ollama"  - force OllamaEmbedder (raises if unreachable).
      "hashing" - force HashingEmbedder (default; zero-dependency, no network).
    """
    if prefer == "hashing":
        return HashingEmbedder()
    if prefer == "ollama" or (prefer == "auto" and ollama_available()):
        if not ollama_available():
            raise RuntimeError(f"ollama not reachable at {OLLAMA_HOST}")
        return OllamaEmbedder(model=model or "nomic-embed-text")
    return HashingEmbedder()
