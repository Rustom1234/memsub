"""Round 2, candidate 1: opt-in local LLM rerank evaluation.

Pre-registered in docs/extensions.md ("Round 2 pre-registration") before any
number below was produced. Compares `memsub_rerank`
(`memsub.bench.RerankSystem`: hybrid-v2 top-25 candidates reordered by one
batched qwen3:8b call) against the already-frozen `memsub_hybrid_v2` numbers
in docs/metrics-local.md (0.157 dev / 0.1515 holdout) without re-running that
baseline -- its code and number are unchanged by this round.

  --dev N       directional go/no-go check, N questions per category on dev
                conversations 1-3 (default 8/category, n<=32)
  --holdout     the official keep/revert-decision run: 20 questions per
                category (n=80), same sampling seed (123) and LOCOMO
                conversations 4-10 as scripts/holdout_answer_quality.py

Every rerank call's audit entry (query, backend, model, status, order) is
saved to results/rerank-log-<mode>.jsonl; that log is the point of the
"opt-in with an audit trail" design, not just the F1 number.
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

from scripts.metrics_local import ANSWER_SYSTEM, BUDGET, CATS, FROZEN, token_f1  # noqa: E402

from memsub.backends import OllamaBackend  # noqa: E402
from memsub.bench import RerankSystem, load_locomo  # noqa: E402

RERANK_K = 25
HOLDOUT_PER_CATEGORY = 20
HOLDOUT_SEED = 123  # matches scripts/holdout_answer_quality.py, fixed before this round


def pick_questions(convs, per_category: int, seed: int) -> list[tuple[int, object]]:
    by_cat: dict[int, list[tuple[int, object]]] = {}
    for ci, conv in enumerate(convs):
        for q in conv.qa:
            if q.category not in CATS or not q.answer:
                continue
            by_cat.setdefault(q.category, []).append((ci, q))
    rng = random.Random(seed)
    picked: list[tuple[int, object]] = []
    for cat in sorted(by_cat):
        qs = by_cat[cat]
        rng.shuffle(qs)
        picked.extend(qs[:per_category])
    return picked


def run(mode: str, convs, picked, backend) -> dict:
    systems: dict[int, RerankSystem] = {}
    rows = []
    audit_log: list[dict] = []
    t_start = time.time()
    for ci, q in picked:
        conv = convs[ci]
        if ci not in systems:
            s = RerankSystem(backend=backend, rerank_k=RERANK_K, **FROZEN)
            s.ingest(list(conv.episodes))
            systems[ci] = s
        system = systems[ci]
        t0 = time.perf_counter()
        packed = system.inject(q.question, BUDGET)
        ctx = "\n".join(e.content for e in packed)
        prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"
        answer = backend.complete(ANSWER_SYSTEM, [{"role": "user", "content": prompt}])
        latency = time.perf_counter() - t0
        rows.append({
            "category": q.category,
            "f1": token_f1(answer, q.answer),
            "exact": int(q.answer.lower() in answer.lower()),
            "latency_s": latency,
            "rerank_status": system.rerank_log[-1]["status"] if system.rerank_log else None,
        })
        if system.rerank_log:
            audit_log.append(system.rerank_log[-1])
        print(f"  [{len(rows)}/{len(picked)}] cat={q.category} f1={rows[-1]['f1']:.3f} "
              f"status={rows[-1]['rerank_status']} ({latency:.1f}s)", flush=True)

    wall = round(time.time() - t_start, 1)
    (ROOT / "results" / f"rerank-log-{mode}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in audit_log) + "\n"
    )
    status_counts: dict[str, int] = {}
    for r in rows:
        status_counts[r["rerank_status"]] = status_counts.get(r["rerank_status"], 0) + 1

    by_cat = {}
    for cat in sorted(CATS):
        rs = [r for r in rows if r["category"] == cat]
        if rs:
            by_cat[CATS[cat]] = {"n": len(rs), "mean_f1": round(mean(r["f1"] for r in rs), 4)}

    out = {
        "mode": mode, "n": len(rows), "budget": BUDGET, "rerank_k": RERANK_K,
        "mean_f1": round(mean(r["f1"] for r in rows), 4) if rows else 0.0,
        "exact_substring_rate": round(mean(r["exact"] for r in rows), 4) if rows else 0.0,
        "mean_latency_s": round(mean(r["latency_s"] for r in rows), 3) if rows else 0.0,
        "rerank_status_counts": status_counts,
        "by_category": by_cat,
        "wall_time_s": wall,
        "rows": rows,
    }
    (ROOT / "results" / f"rerank-eval-{mode}.json").write_text(json.dumps(out, indent=2))
    return out


def main() -> int:
    args = sys.argv[1:]
    backend = OllamaBackend(model="qwen3:8b")
    all_convs = load_locomo(ROOT / "data" / "locomo10.json")

    if args[:1] == ["--dev"]:
        per_cat = int(args[1]) if len(args) > 1 else 8
        convs = all_convs[:3]  # DEV split
        picked = pick_questions(convs, per_category=per_cat, seed=42)
        print(f"dev smoke test: n={len(picked)} ({per_cat}/category), rerank_k={RERANK_K}")
        out = run("dev", convs, picked, backend)
    elif args[:1] == ["--holdout"]:
        convs = all_convs[3:]  # HOLDOUT, never used in selection
        picked = pick_questions(convs, per_category=HOLDOUT_PER_CATEGORY, seed=HOLDOUT_SEED)
        print(f"HOLDOUT run: n={len(picked)} ({HOLDOUT_PER_CATEGORY}/category), rerank_k={RERANK_K}")
        out = run("holdout", convs, picked, backend)
    else:
        print(__doc__)
        return 1

    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
