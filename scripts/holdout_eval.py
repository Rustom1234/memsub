"""PHASE 4: the one holdout run for retrieval v2.

Evaluates the dev-frozen stack on the held-out LOCOMO conversations (4-10),
which no selection decision ever touched, against the previous system (tuned
policy) and naive top-k. Budgets 500 and 1000, per question category.
Appends the final table to docs/benchmark.md and writes
results/benchmark-v2.json.
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memsub.bench import HybridSystem, NaiveTopKSystem, PolicySystem, load_locomo  # noqa: E402

TUNED = {"recency_weight": 0.999, "recency_floor": 0.75, "importance_floor": 0.0}
FROZEN = dict(
    policy_params=TUNED, dense_weight=0.5, bm25_weight=1.0, rrf_k=60,
    temporal=True, temporal_weight=1.0, speaker_boost=1.0, session_weight=3.0,
)
BUDGETS = [500, 1000]
CATS = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
SYSTEMS = {
    "policy (before)": lambda: PolicySystem(**TUNED),
    "naive-topk": lambda: NaiveTopKSystem(),
    "hybrid-v2 (after)": lambda: HybridSystem(**FROZEN),
}


def main() -> int:
    convs = load_locomo(ROOT / "data" / "locomo10.json")[3:]  # HOLDOUT
    results = {}
    for name, factory in SYSTEMS.items():
        acc = defaultdict(lambda: [0, 0, 0])  # (budget, cat) -> n, all, any
        for conv in convs:
            system = factory()
            system.ingest(list(conv.episodes))
            for q in conv.qa:
                if q.category not in CATS or not q.evidence:
                    continue
                ev = {f"{conv.conversation_id}:{e}" for e in q.evidence}
                for budget in BUDGETS:
                    packed = {ep.id for ep in system.inject(q.question, budget)}
                    hits = len(ev & packed)
                    c = acc[(budget, q.category)]
                    c[0] += 1
                    c[1] += int(hits == len(ev))
                    c[2] += int(hits > 0)
        results[name] = {f"{b}:{c}": list(acc[(b, c)]) for b in BUDGETS for c in CATS}

    lines = [
        "",
        "## Retrieval v2: FINAL HOLDOUT (one run, conversations 4-10, never used in selection)",
        "",
        f"Executed {time.strftime('%Y-%m-%d %H:%M')} by `scripts/holdout_eval.py`. "
        "Stack frozen on dev before this run; no parameter was changed afterwards.",
        "",
        "| budget | category | n | policy (before) all/any | naive-topk all/any | hybrid-v2 (after) all/any |",
        "|---|---|---|---|---|---|",
    ]
    payload = {"frozen_params": {k: v for k, v in FROZEN.items() if k != "policy_params"},
               "policy_params": TUNED, "rows": []}
    for budget in BUDGETS:
        tot = {name: [0, 0, 0] for name in SYSTEMS}
        for cat in sorted(CATS):
            row_cells = []
            n_cat = 0
            for name in SYSTEMS:
                n, a, y = results[name][f"{budget}:{cat}"]
                n_cat = n
                tot[name] = [tot[name][0] + n, tot[name][1] + a, tot[name][2] + y]
                row_cells.append(f"{100 * a / n:.1f}% / {100 * y / n:.1f}%")
                payload["rows"].append({"budget": budget, "category": CATS[cat], "system": name,
                                        "n": n, "all": a, "any": y})
            lines.append(f"| {budget} | {CATS[cat]} | {n_cat} | " + " | ".join(row_cells) + " |")
        cells = []
        for name in SYSTEMS:
            n, a, y = tot[name]
            cells.append(f"**{100 * a / n:.1f}% / {100 * y / n:.1f}%**")
            payload["rows"].append({"budget": budget, "category": "TOTAL", "system": name,
                                    "n": n, "all": a, "any": y})
        lines.append(f"| {budget} | **TOTAL** | {tot[list(SYSTEMS)[0]][0]} | " + " | ".join(cells) + " |")

    with open(ROOT / "docs" / "benchmark.md", "a") as f:
        f.write("\n".join(lines) + "\n")
    (ROOT / "results" / "benchmark-v2.json").write_text(json.dumps(payload, indent=2))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
