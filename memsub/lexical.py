"""Dependency-free BM25 and reciprocal rank fusion.

BM25 (Robertson-Sparck Jones lineage; parameters k1/b as in Okapi BM25) over
tokenized episode contents, plus RRF (Cormack, Clarke, Buettcher, SIGIR 2009)
to fuse rankings from incompatible scorers without score normalization.
Pure Python + math, no dependencies, deterministic.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the and or but of to in on at for with is are was were be been am do does did "
    "what when where who whom which how why not no yes it its his her their this that "
    "these those i you he she they we me him them my your our s t d ll re ve".split()
)


def stem(token: str) -> str:
    """Tiny deterministic suffix stripper (s-stemmer + ing/ed/ly).

    Not Porter, deliberately: just enough to unify research/researching,
    camp/camped/camping, activity/activities on both query and corpus sides.
    """
    t = token
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 3 and t.endswith("es") and not t.endswith(("ses", "zes", "ches", "shes")):
        t = t[:-1]
    elif len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        t = t[:-1]
    if len(t) > 5 and t.endswith("ing"):
        t = t[:-3]
    elif len(t) > 4 and t.endswith("ed"):
        t = t[:-2]
    elif len(t) > 5 and t.endswith("ly"):
        t = t[:-2]
    if len(t) > 3 and t[-1] == t[-2] and t[-1] not in "aeiou":
        t = t[:-1]
    return t


def tokenize(text: str, use_stem: bool = True) -> list[str]:
    toks = [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP]
    return [stem(t) for t in toks] if use_stem else toks


class BM25Index:
    """Okapi BM25 over a fixed corpus of documents."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._tf: list[Counter] = []
        self._len: list[int] = []
        df: Counter = Counter()
        for d in docs:
            toks = tokenize(d)
            self._tf.append(Counter(toks))
            self._len.append(len(toks))
            df.update(set(toks))
        n = max(len(docs), 1)
        self._avg_len = (sum(self._len) / n) or 1.0
        self._idf = {w: math.log((n - d + 0.5) / (d + 0.5) + 1.0) for w, d in df.items()}

    def __len__(self) -> int:
        return len(self._tf)

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for tf, dlen in zip(self._tf, self._len):
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * dlen / self._avg_len)
            for w in q:
                f = tf.get(w, 0)
                if f:
                    s += self._idf.get(w, 0.0) * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out


def rrf_fuse(rankings: list[list[int]], k: int = 60, weights: list[float] | None = None) -> list[int]:
    """Fuse rankings (lists of doc indices, best first) by reciprocal rank.

    Returns doc indices sorted by fused score, best first. Docs absent from a
    ranking simply contribute nothing from it.
    """
    weights = weights or [1.0] * len(rankings)
    scores: dict[int, float] = {}
    for ranking, w in zip(rankings, weights):
        for rank, doc in enumerate(ranking):
            scores[doc] = scores.get(doc, 0.0) + w / (k + rank + 1)
    return sorted(scores, key=lambda d: (-scores[d], d))
