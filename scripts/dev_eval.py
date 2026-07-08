"""Phase-3 harness: evaluate candidate systems on the LOCOMO DEV split only.

Prints evidence-all / evidence-any per question category at budgets 500/1000.
The holdout conversations are never touched here.

Usage: .venv/bin/python scripts/dev_eval.py [system ...]
Systems: policy, hybrid, bm25, plus variants registered in SYSTEMS below.
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memsub.bench import (  # noqa: E402
    HybridSystem,
    PolicySystem,
    load_locomo,
    pack_greedy,
)
from memsub.lexical import BM25Index  # noqa: E402

TUNED = {"recency_weight": 0.999, "recency_floor": 0.75, "importance_floor": 0.0}
BUDGETS = [500, 1000]
CATEGORY_NAMES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}


class BM25System:
    name = "bm25"

    def __init__(self) -> None:
        self._eps = []
        self._bm25 = None

    def ingest(self, episodes) -> None:
        self._eps = sorted(episodes, key=lambda e: e.ts)
        self._bm25 = BM25Index([ep.content for ep in self._eps])

    def inject(self, query: str, budget_tokens: int):
        s = self._bm25.scores(query)
        order = sorted(range(len(self._eps)), key=lambda i: (-s[i], i))
        return pack_greedy([self._eps[i] for i in order], budget_tokens)


SYSTEMS = {
    "policy": lambda: PolicySystem(**TUNED),
    "bm25": lambda: BM25System(),
    "hybrid": lambda: HybridSystem(policy_params=TUNED),
}


def main() -> int:
    names = sys.argv[1:] or ["policy", "hybrid"]
    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]  # DEV ONLY
    t0 = time.time()
    for name in names:
        # acc[(budget, cat)] = [n, all_hit, any_hit]
        acc: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0, 0])
        for conv in convs:
            system = SYSTEMS[name]()
            system.ingest(list(conv.episodes))
            for q in conv.qa:
                if q.category not in CATEGORY_NAMES or not q.evidence:
                    continue
                ev = {f"{conv.conversation_id}:{e}" for e in q.evidence}
                for budget in BUDGETS:
                    packed = {ep.id for ep in system.inject(q.question, budget)}
                    hits = len(ev & packed)
                    cell = acc[(budget, q.category)]
                    cell[0] += 1
                    cell[1] += int(hits == len(ev))
                    cell[2] += int(hits > 0)
        print(f"\n== {name} (dev, n per budget = {sum(acc[(BUDGETS[0], c)][0] for c in CATEGORY_NAMES)}) ==")
        print("| budget | category | evidence-all | evidence-any |")
        for budget in BUDGETS:
            tot = [0, 0, 0]
            for cat in sorted(CATEGORY_NAMES):
                n, a, y = acc[(budget, cat)]
                tot = [tot[0] + n, tot[1] + a, tot[2] + y]
                print(f"| {budget} | {CATEGORY_NAMES[cat]} | {100 * a / n:.1f}% | {100 * y / n:.1f}% |")
            print(f"| {budget} | TOTAL | {100 * tot[1] / tot[0]:.1f}% | {100 * tot[2] / tot[0]:.1f}% |")
    print(f"\nwall {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
