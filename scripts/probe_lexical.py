"""Diagnostic probe (dev split only): how far would pure lexical ranking go?

Ranks turns by BM25 (implemented dependency-free) for each dev question and
measures evidence recall under the same greedy packing and budget as the main
benchmark. This is NOT an integrated technique yet; it bounds the gain a
hybrid lexical+dense retriever could deliver and is recorded in the error
analysis doc.
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memsub.bench import PolicySystem, load_locomo, pack_greedy  # noqa: E402

BUDGET = 1000
PARAMS = {"recency_weight": 0.999, "recency_floor": 0.75, "importance_floor": 0.0}
CATEGORY_NAMES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = set(
    "a an the and or but of to in on at for with is are was were be been do does did "
    "what when where who whom which how why not no yes it its his her their this that "
    "these those i you he she they we me him them my your s t d ll re ve".split()
)


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.doc_tokens = [tokens(d) for d in docs]
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.avg_len = sum(self.doc_len) / max(len(docs), 1)
        self.tf = [Counter(t) for t in self.doc_tokens]
        df: Counter = Counter()
        for t in self.doc_tokens:
            df.update(set(t))
        n = len(docs)
        self.idf = {w: math.log((n - d + 0.5) / (d + 0.5) + 1.0) for w, d in df.items()}

    def scores(self, query: str) -> list[float]:
        q = tokens(query)
        out = []
        for i in range(len(self.doc_tokens)):
            s = 0.0
            for w in q:
                f = self.tf[i].get(w, 0)
                if not f:
                    continue
                idf = self.idf.get(w, 0.0)
                denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avg_len)
                s += idf * f * (self.k1 + 1) / denom
            out.append(s)
        return out


def main() -> int:
    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]
    stats: dict[str, dict[int, list[int]]] = {m: defaultdict(lambda: [0, 0]) for m in ("dense", "bm25")}
    for conv in convs:
        system = PolicySystem(**PARAMS)
        system.ingest(list(conv.episodes))
        eps = system._index.episodes
        bm25 = BM25([ep.content for ep in eps])
        for q in conv.qa:
            if q.category not in CATEGORY_NAMES or not q.evidence:
                continue
            ev_ids = {f"{conv.conversation_id}:{e}" for e in q.evidence}
            packed_dense = {ep.id for ep in system.inject(q.question, BUDGET)}
            order = sorted(range(len(eps)), key=lambda i: -bm25.scores(q.question)[i])
            packed_bm = {ep.id for ep in pack_greedy([eps[i] for i in order], BUDGET)}
            for name, packed in (("dense", packed_dense), ("bm25", packed_bm)):
                cell = stats[name][q.category]
                cell[0] += 1
                cell[1] += int(ev_ids <= packed)
    print("evidence-all @1000 on dev, by ranking signal:")
    print("| category | dense (current) | bm25 (probe) |")
    for cat in sorted(CATEGORY_NAMES):
        d, b = stats["dense"][cat], stats["bm25"][cat]
        print(f"| {CATEGORY_NAMES[cat]} | {100 * d[1] / d[0]:.1f}% | {100 * b[1] / b[0]:.1f}% |")
    td = [sum(x) for x in zip(*stats["dense"].values())]
    tb = [sum(x) for x in zip(*stats["bm25"].values())]
    print(f"| TOTAL | {100 * td[1] / td[0]:.1f}% | {100 * tb[1] / tb[0]:.1f}% |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
