"""LongMemEval_S: frozen evaluation (qwen3:8b answerer, sonnet CLI judge).

Pre-registered protocol (docs/benchmark-external.md, written BEFORE this
script ran): n=5 non-abstention items per question type (30 total, all 6
LongMemEval_S types), SEED=123 deterministic stratified sample, mirroring
scripts/holdout_answer_quality.py's per-category sampling convention. Same
FROZEN hybrid-v2 params as docs/metrics-local.md (no re-tuning on this
dataset). Conditions: no_memory, naive_topk, memsub_hybrid_v2. Graded with
token-F1 (deterministic) and a Sonnet CLI judge (memsub.judge, semantic
correctness). Per-item timeouts pre-registered at 60s answer / 60s judge
(dry-run max observed: 6.8s answer, single judge-call probe: 11.0s).

Abstention items (question_id ending "_abs") are excluded: this run measures
answer quality on answerable questions only, not abstention behavior, and
that scope is stated here rather than silently applied.

Usage: .venv/bin/python scripts/longmemeval_frozen.py
Writes: results/longmemeval-frozen.json
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.metrics_local import ANSWER_SYSTEM, BUDGET, token_f1  # noqa: E402

from memsub.backends import ClaudeCLIBackend, OllamaBackend  # noqa: E402
from memsub.bench import HybridSystem, NaiveTopKSystem  # noqa: E402
from memsub.judge import judge_answer  # noqa: E402
from memsub.longmemeval import load_longmemeval  # noqa: E402

DATA = ROOT / "data" / "longmemeval_s_cleaned.json"
FROZEN = dict(
    policy_params={"recency_weight": 0.999, "recency_floor": 0.75, "importance_floor": 0.0},
    dense_weight=0.5, bm25_weight=1.0, rrf_k=60,
    temporal=True, temporal_weight=1.0, speaker_boost=1.0, session_weight=3.0,
)
CONDITIONS = ["no_memory", "naive_topk", "memsub_hybrid_v2"]
PER_TYPE = 5
SEED = 123  # fixed before the run; never tuned
ANSWER_TIMEOUT_S = 60.0
JUDGE_TIMEOUT_S = 60.0


def sample_items():
    """Deterministic stratified sample: PER_TYPE non-abstention items per type."""
    all_items = load_longmemeval(DATA)  # limit=None: full-file schema validation
    by_type: dict[str, list] = {}
    for item in all_items:
        if item.is_abstention:
            continue
        by_type.setdefault(item.question_type, []).append(item)
    rng = random.Random(SEED)
    picked = []
    for qtype in sorted(by_type):
        items = by_type[qtype]
        rng.shuffle(items)
        picked.extend(items[:PER_TYPE])
    return picked


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
    judge_backend = ClaudeCLIBackend(model="sonnet")
    items = sample_items()
    print(f"loaded {len(items)} items (pre-registered n={PER_TYPE} per type x "
          f"{len({i.question_type for i in items})} types)")

    rows = []
    judge_log: list[dict] = []
    t_start = time.perf_counter()
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
            t3 = time.perf_counter()
            verdict = judge_answer(item.question, item.answer, answer, judge_backend, judge_log)
            t_judge = time.perf_counter() - t3
            row = {
                "condition": cond, "question_id": item.question_id,
                "question_type": item.question_type, "n_episodes": len(item.episodes),
                "question": item.question, "gold": item.answer, "answer": answer,
                "t_ingest_s": round(t_ingest, 3), "t_retrieve_s": round(t_retrieve, 3),
                "t_answer_s": round(t_answer, 3), "t_judge_s": round(t_judge, 3),
                "f1": round(token_f1(answer, item.answer), 4),
                "judge_correct": verdict["correct"], "judge_status": verdict["status"],
            }
            rows.append(row)
            print(f"  {cond:16s} {item.question_id:14s} f1={row['f1']:.3f} "
                  f"judge={verdict['correct']} ({verdict['status']}) "
                  f"t_answer={t_answer:.1f}s t_judge={t_judge:.1f}s", flush=True)

    wall_s = time.perf_counter() - t_start
    by_cond = {}
    for cond in CONDITIONS:
        rs = [r for r in rows if r["condition"] == cond]
        judged = [r["judge_correct"] for r in rs if r["judge_correct"] is not None]
        by_cond[cond] = {
            "n": len(rs),
            "mean_f1": round(mean(r["f1"] for r in rs), 4),
            "judge_accuracy": round(mean(judged), 4) if judged else None,
            "n_judge_malformed": sum(1 for r in rs if r["judge_correct"] is None),
        }
    out = {
        "n_per_type": PER_TYPE, "seed": SEED, "wall_s": round(wall_s, 1),
        "conditions": CONDITIONS, "rows": rows, "by_condition": by_cond,
        "judge_log": judge_log,
    }
    (ROOT / "results" / "longmemeval-frozen.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(by_cond, indent=2))
    print(f"wall time: {wall_s:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
