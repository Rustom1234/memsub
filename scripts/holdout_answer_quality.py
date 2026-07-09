"""FINAL HOLDOUT run for end-to-end local-model answer quality.

One run on the held-out LOCOMO conversations (4-10), which no selection
decision ever touched. The stack (hybrid v2 + sleep artifacts) was frozen on
dev (docs/metrics-local.md) before this run. Same protocol as the dev
harness: qwen3:8b as both memory backend and answerer, budget 1000,
stratified across categories 1-4, deterministic token-F1 grading.

Staged so each foreground invocation stays within a shell timeout:

  --warm A B      generate + cache sleep artifacts for holdout convs [A, B)
                  (indices into the holdout list, 0-6)
  --condition X   run one condition (no_memory | naive_topk |
                  memsub_hybrid_v2 | memsub_plus_sleep); saves
                  results/holdout-aq-<X>.json
  --report        merge all four condition files, print the table, append
                  to docs/metrics-local.md, save results/holdout-aq.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.metrics_local import (  # noqa: E402
    ANSWER_SYSTEM,
    BUDGET,
    CATS,
    FROZEN,
    get_sleep_result,
    hardware_line,
    token_f1,
)

from memsub.backends import OllamaBackend  # noqa: E402
from memsub.bench import HybridSystem, NaiveTopKSystem, load_locomo  # noqa: E402

PER_CATEGORY = 20
SEED = 123  # fixed before the run; never tuned
CONDITIONS = ["no_memory", "naive_topk", "memsub_hybrid_v2", "memsub_plus_sleep"]


def holdout_convs():
    return load_locomo(ROOT / "data" / "locomo10.json")[3:]  # HOLDOUT


def sample(convs):
    import random

    by_cat: dict[int, list] = {}
    for ci, conv in enumerate(convs):
        for qi, q in enumerate(conv.qa):
            if q.category not in CATS or not q.answer:
                continue
            by_cat.setdefault(q.category, []).append((ci, qi))
    rng = random.Random(SEED)
    picked = []
    for cat in sorted(by_cat):
        qs = by_cat[cat]
        rng.shuffle(qs)
        picked.extend(qs[:PER_CATEGORY])
    return picked


def build_system(cond: str, conv, backend):
    if cond == "no_memory":
        return None
    if cond == "naive_topk":
        s = NaiveTopKSystem()
        s.ingest(list(conv.episodes))
        return s
    s = HybridSystem(**FROZEN)
    if cond == "memsub_plus_sleep":
        sleep_result = get_sleep_result(conv, backend)
        s.ingest(list(conv.episodes) + sleep_result.all_memories)
    else:
        s.ingest(list(conv.episodes))
    return s


def cmd_warm(a: int, b: int) -> int:
    backend = OllamaBackend(model="qwen3:8b")
    convs = holdout_convs()
    for conv in convs[a:b]:
        t0 = time.time()
        r = get_sleep_result(conv, backend)
        print(f"{conv.conversation_id}: {len(r.summaries)} summaries, "
              f"{len(r.inferences)} inferences, {len(r.links)} links "
              f"({time.time() - t0:.1f}s)", flush=True)
    return 0


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
            "f1": token_f1(answer, q.answer),
            "exact": int(q.answer.lower() in answer.lower()),
            "latency_s": time.perf_counter() - t1,
        })
    out = {"condition": cond, "n": len(rows), "rows": rows,
           "wall_time_s": round(time.time() - t0, 1)}
    (ROOT / "results" / f"holdout-aq-{cond}.json").write_text(json.dumps(out))
    print(f"{cond}: n={len(rows)}, mean F1 "
          f"{mean(r['f1'] for r in rows):.4f}, wall {out['wall_time_s']}s")
    return 0


def cmd_report() -> int:
    merged = {}
    for cond in CONDITIONS:
        p = ROOT / "results" / f"holdout-aq-{cond}.json"
        if not p.exists():
            print(f"missing {p}; run --condition {cond} first", file=sys.stderr)
            return 1
        merged[cond] = json.loads(p.read_text())

    lines = [
        "",
        "## FINAL HOLDOUT: end-to-end local-model answer quality "
        f"(executed {time.strftime('%Y-%m-%d %H:%M')}, `scripts/holdout_answer_quality.py`)",
        "",
        f"One run on held-out LOCOMO conversations 4-10 (never used in any selection "
        f"decision; stack and sampling seed frozen before this run). qwen3:8b as both "
        f"memory backend and answerer, budget {BUDGET}, up to {PER_CATEGORY} questions "
        "per category, deterministic token-F1. Hardware: " + hardware_line() + ".",
        "",
        "| condition | n | mean token-F1 | exact-substring rate |",
        "|---|---|---|---|",
    ]
    for cond, data in merged.items():
        rows = data["rows"]
        lines.append(
            f"| {cond} | {len(rows)} | {mean(r['f1'] for r in rows):.4f} | "
            f"{mean(r['exact'] for r in rows):.4f} |"
        )
    lines += ["", "Per category (mean token-F1):", "",
              "| condition | " + " | ".join(CATS[c] for c in sorted(CATS)) + " |",
              "|---|" + "---|" * len(CATS)]
    for cond, data in merged.items():
        cells = []
        for cat in sorted(CATS):
            rs = [r for r in data["rows"] if r["category"] == cat]
            cells.append(f"{mean(r['f1'] for r in rs):.4f} (n={len(rs)})" if rs else "-")
        lines.append(f"| {cond} | " + " | ".join(cells) + " |")
    report = "\n".join(lines) + "\n"
    with open(ROOT / "docs" / "metrics-local.md", "a") as f:
        f.write(report)
    (ROOT / "results" / "holdout-aq.json").write_text(json.dumps(merged, indent=2))
    print(report)
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--warm"]:
        return cmd_warm(int(args[1]), int(args[2]))
    if args[:1] == ["--condition"]:
        return cmd_condition(args[1])
    if args[:1] == ["--report"]:
        return cmd_report()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
