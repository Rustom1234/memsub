"""LongMemEval_S: 5-question dry timing run (qwen3:8b), BEFORE any frozen run.

Purpose per the external-benchmark plan: estimate per-question wall-clock
cost across the three conditions (no_memory, naive_topk, memsub_hybrid_v2)
so a sample size n and per-question timeout can be pre-registered in
docs/benchmark-external.md BEFORE the frozen evaluation runs. This script
produces no accuracy numbers worth trusting (n=5, not a held-out selection
protocol) -- it is a timing probe only.

Usage: .venv/bin/python scripts/longmemeval_dry_run.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.metrics_local import ANSWER_SYSTEM, BUDGET, token_f1  # noqa: E402

from memsub.backends import OllamaBackend  # noqa: E402
from memsub.bench import HybridSystem, NaiveTopKSystem  # noqa: E402
from memsub.longmemeval import load_longmemeval  # noqa: E402

DATA = ROOT / "data" / "longmemeval_s_cleaned.json"
FROZEN = dict(
    policy_params={"recency_weight": 0.999, "recency_floor": 0.75, "importance_floor": 0.0},
    dense_weight=0.5, bm25_weight=1.0, rrf_k=60,
    temporal=True, temporal_weight=1.0, speaker_boost=1.0, session_weight=3.0,
)
CONDITIONS = ["no_memory", "naive_topk", "memsub_hybrid_v2"]
N_DRY = 5


def build_system(cond: str, item):
    if cond == "no_memory":
        return None
    if cond == "naive_topk":
        s = NaiveTopKSystem()
        s.ingest(item.episodes)
        return s
    s = HybridSystem(**FROZEN)
    s.ingest(item.episodes)
    return s


def main() -> int:
    backend = OllamaBackend(model="qwen3:8b")
    items = load_longmemeval(DATA, limit=N_DRY)
    print(f"loaded {len(items)} items, mean haystack episodes = "
          f"{mean(len(i.episodes) for i in items):.0f}")

    rows = []
    for cond in CONDITIONS:
        for item in items:
            t0 = time.perf_counter()
            system = build_system(cond, item)
            t_ingest = time.perf_counter() - t0
            t1 = time.perf_counter()
            ctx = "" if system is None else "\n".join(
                e.content for e in system.inject(item.question, BUDGET)
            )
            t_retrieve = time.perf_counter() - t1
            prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {item.question}"
            t2 = time.perf_counter()
            answer = backend.complete(ANSWER_SYSTEM, [{"role": "user", "content": prompt}])
            t_answer = time.perf_counter() - t2
            row = {
                "condition": cond, "question_id": item.question_id,
                "question_type": item.question_type, "n_episodes": len(item.episodes),
                "t_ingest_s": round(t_ingest, 3), "t_retrieve_s": round(t_retrieve, 3),
                "t_answer_s": round(t_answer, 3),
                "t_total_s": round(t_ingest + t_retrieve + t_answer, 3),
                "f1": round(token_f1(answer, item.answer), 4),
            }
            rows.append(row)
            print(f"  {cond:16s} {item.question_id:10s} total={row['t_total_s']:6.2f}s "
                  f"(ingest={t_ingest:.2f} retrieve={t_retrieve:.3f} answer={t_answer:.2f}) "
                  f"f1={row['f1']:.3f}", flush=True)

    by_cond = {}
    for cond in CONDITIONS:
        rs = [r for r in rows if r["condition"] == cond]
        by_cond[cond] = {
            "mean_total_s": round(mean(r["t_total_s"] for r in rs), 3),
            "max_total_s": round(max(r["t_total_s"] for r in rs), 3),
        }
    out = {"n": N_DRY, "rows": rows, "by_condition": by_cond}
    (ROOT / "results" / "longmemeval-dry-run.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(by_cond, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
