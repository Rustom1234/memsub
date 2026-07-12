"""Re-capture frozen-holdout answers WITH text, for LLM-judge scoring.

The original frozen holdout run (scripts/holdout_answer_quality.py,
results/holdout-aq-*.json, headline numbers in docs/metrics-local.md) only
persisted numeric fields (category, f1, exact, latency_s) per row -- no
question/gold/model-answer text survived, so there is nothing for a judge to
read. Per the external-benchmark plan: if per-item text was not persisted,
re-run the frozen protocol once to regenerate it, and note this clearly.

This script reuses the exact same protocol as scripts/holdout_answer_quality.py
(same SEED=123, same sampling function, same three conditions this round
needs, same qwen3:8b backend, same budget) and re-runs it once, saving
question/gold/answer text this time, to SEPARATE files
(results/holdout-judge-capture-<condition>.json) -- the original frozen
numeric artifacts in results/holdout-aq-*.json are untouched.

CAVEAT: OllamaBackend does not pin temperature/seed, so qwen3:8b generation
is not guaranteed identical across runs; this re-run's own token-F1 may
differ slightly from the original frozen docs/metrics-local.md numbers.
Both are reported in docs/benchmark-external.md; the judge scores THIS run's
captured text.

Only the three conditions the LLM-judge step needs: no_memory, naive_topk,
memsub_hybrid_v2 ("memsub full"). memsub_plus_sleep (the sleep extension) is
out of scope for this round.

Usage: .venv/bin/python scripts/judge_capture_holdout.py --condition X
       (X in no_memory | naive_topk | memsub_hybrid_v2)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.holdout_answer_quality import build_system, holdout_convs, sample  # noqa: E402
from scripts.metrics_local import ANSWER_SYSTEM, BUDGET, token_f1  # noqa: E402

from memsub.backends import OllamaBackend  # noqa: E402

CONDITIONS = ["no_memory", "naive_topk", "memsub_hybrid_v2"]


def cmd_condition(cond: str) -> int:
    assert cond in CONDITIONS, cond
    backend = OllamaBackend(model="qwen3:8b")
    convs = holdout_convs()
    picked = sample(convs)
    systems: dict[int, object] = {}
    rows = []
    t0 = time.time()
    for ci, qi in picked:
        conv = convs[ci]
        if ci not in systems:
            systems[ci] = build_system(cond, conv, backend)
        system = systems[ci]
        q = conv.qa[qi]
        ctx = "" if system is None else "\n".join(
            e.content for e in system.inject(q.question, BUDGET)
        )
        prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"
        t1 = time.perf_counter()
        answer = backend.complete(ANSWER_SYSTEM, [{"role": "user", "content": prompt}])
        rows.append({
            "category": q.category,
            "question": q.question,
            "gold": q.answer,
            "answer": answer,
            "f1": token_f1(answer, q.answer),
            "exact": int(q.answer.lower() in answer.lower()),
            "latency_s": time.perf_counter() - t1,
        })
    out = {"condition": cond, "n": len(rows), "rows": rows,
           "wall_time_s": round(time.time() - t0, 1)}
    (ROOT / "results" / f"holdout-judge-capture-{cond}.json").write_text(json.dumps(out, indent=2))
    print(f"{cond}: n={len(rows)}, mean F1 "
          f"{mean(r['f1'] for r in rows):.4f}, wall {out['wall_time_s']}s")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--condition"]:
        return cmd_condition(args[1])
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
