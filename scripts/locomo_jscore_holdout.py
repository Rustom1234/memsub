"""LOCOMO holdout run through the standard LLM-judge (J-score) protocol.

Closes bead vtr and Part A's first fix in docs/improvement-plan-2026-07-17.md: memsub's
headline number (0.152 token-F1, qwen3:8b local, docs/benchmark.md) is not on the same
axis as any competitor number quoted there (mem0, Zep, Dakera, MIRIX all report
LLM-judge accuracy). This script re-runs the same LOCOMO holdout slice already used for
the headline number (`scripts/holdout_eval.py` / `scripts/holdout_answer_quality.py`:
`load_locomo(...)[3:]`, LOCOMO conversations 4-10, up to 20 questions per category,
`random.Random(123)`, n=80) through a Sonnet-CLI judge, same precedent as
`scripts/judge_capture_holdout.py` and `memsub/judge.py`.

Four conditions (extends the judge-capture script's three with today's decomposition
opt-in, docs/multihop-improvements-2026-07-17.md):

  no_memory          no context at all.
  naive_topk         cosine top-k only, no policy.
  memsub_hybrid_v2   shipped default (FROZEN hybrid v2 params).
  memsub_decompose   same shipped system, HybridRetriever.rank_decomposed at query time
                      (one bounded decompose call, <=3 retrieval passes, per
                      memsub/hybrid.py; opt-in, not a change to the shipped default).

Per question: generate the answer locally (qwen3:8b via ollama, same OllamaBackend
default as the documented methodology, no think= override), then grade it with the
Sonnet judge (`memsub.judge.judge_answer`, `claude` CLI in print mode, model=sonnet,
Rustom's Max plan, no API key). The judge only ever sees (question, gold, answer) --
never the condition name -- so it is blind to condition by construction.

Streams one JSONL record per (condition, question) to
results/locomo-jscore-2026-07-17.jsonl, flushed immediately after every record.
Resumable: a (condition, conversation_id, question) key already recorded with judge
status "ok" is skipped on restart; malformed/error rows are retried.

Usage: .venv/bin/python scripts/locomo_jscore_holdout.py [--limit N]
  --limit N   stop after N questions per condition (smoke test only).
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
from scripts.metrics_local import ANSWER_SYSTEM, BUDGET, CATS  # noqa: E402

from memsub.backends import ClaudeCLIBackend, OllamaBackend  # noqa: E402
from memsub.bench import pack_greedy  # noqa: E402
from memsub.judge import judge_answer  # noqa: E402

RESULTS_PATH = ROOT / "results" / "locomo-jscore-2026-07-17.jsonl"
CONDITIONS = ["no_memory", "naive_topk", "memsub_hybrid_v2", "memsub_decompose"]
MAX_WALL_SECONDS = 3 * 3600  # hard safety cap; pre-run estimate was ~1.7h for the full slice


def _load_existing_ok_keys() -> set[tuple]:
    if not RESULTS_PATH.exists():
        return set()
    keys = set()
    for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("record_type") == "question" and rec.get("judge_status") == "ok":
            keys.add((rec["condition"], rec["conversation_id"], rec["question"]))
    return keys


def _all_question_records(condition: str) -> list[dict]:
    if not RESULTS_PATH.exists():
        return []
    out = []
    for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("record_type") == "question" and rec.get("condition") == condition:
            out.append(rec)
    return out


def _write(fh, record: dict) -> None:
    fh.write(json.dumps(record) + "\n")
    fh.flush()


def _context_for(condition: str, system, question: str, answer_backend) -> str:
    if system is None:
        return ""
    if condition == "memsub_decompose":
        order = system.rank_decomposed(question, answer_backend, max_subqueries=2)
        packed = pack_greedy([system.episodes[i] for i in order], BUDGET)
    else:
        packed = system.inject(question, BUDGET)
    return "\n".join(ep.content for ep in packed)


def main() -> int:
    args = sys.argv[1:]
    limit = None
    if args[:1] == ["--limit"]:
        limit = int(args[1])

    answer_backend = OllamaBackend(model="qwen3:8b")
    judge_backend = ClaudeCLIBackend(model="sonnet", timeout=180)

    convs = holdout_convs()
    picked = sample(convs)
    if limit is not None:
        picked = picked[:limit]

    existing = _load_existing_ok_keys()
    t0 = time.time()
    truncated = False

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as fh:
        for condition in CONDITIONS:
            systems: dict[int, object] = {}
            build_cond = "memsub_hybrid_v2" if condition == "memsub_decompose" else condition

            for ci, qi in picked:
                conv = convs[ci]
                q = conv.qa[qi]
                key = (condition, conv.conversation_id, q.question)
                if key in existing:
                    continue
                if time.time() - t0 > MAX_WALL_SECONDS:
                    truncated = True
                    break

                if ci not in systems:
                    systems[ci] = build_system(build_cond, conv, answer_backend)
                system = systems[ci]

                ctx = _context_for(condition, system, q.question, answer_backend)
                prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"

                t1 = time.perf_counter()
                answer = answer_backend.complete(ANSWER_SYSTEM, [{"role": "user", "content": prompt}])
                answer_latency = time.perf_counter() - t1

                t2 = time.perf_counter()
                verdict = judge_answer(q.question, q.answer, answer, judge_backend)
                judge_latency = time.perf_counter() - t2

                record = {
                    "record_type": "question",
                    "condition": condition,
                    "conversation_id": conv.conversation_id,
                    "category": q.category,
                    "category_name": CATS[q.category],
                    "question": q.question,
                    "gold": q.answer,
                    "answer": answer,
                    "correct": verdict["correct"],
                    "judge_status": verdict["status"],
                    "answer_latency_s": round(answer_latency, 3),
                    "judge_latency_s": round(judge_latency, 3),
                }
                _write(fh, record)
                print(
                    f"  [{condition}] {conv.conversation_id} cat={CATS[q.category]} "
                    f"correct={verdict['correct']} status={verdict['status']} "
                    f"({time.time() - t0:.0f}s)",
                    flush=True,
                )

            if truncated:
                print("WALL-CLOCK CAP HIT: remaining questions/conditions skipped.")

            rows = [r for r in _all_question_records(condition) if r["judge_status"] == "ok"]
            n = len(rows)
            by_cat = {}
            for cat, name in CATS.items():
                cat_rows = [r for r in rows if r["category"] == cat]
                if cat_rows:
                    by_cat[name] = {
                        "n": len(cat_rows),
                        "accuracy": round(mean(r["correct"] for r in cat_rows), 4),
                    }
            summary = {
                "record_type": "summary",
                "condition": condition,
                "n_ok": n,
                "n_total_records": len(_all_question_records(condition)),
                "accuracy": round(mean(r["correct"] for r in rows), 4) if rows else None,
                "by_category": by_cat,
                "mean_answer_latency_s": round(mean(r["answer_latency_s"] for r in rows), 3) if rows else None,
                "mean_judge_latency_s": round(mean(r["judge_latency_s"] for r in rows), 3) if rows else None,
                "truncated": truncated,
            }
            _write(fh, summary)
            print(f"== {condition} done: n_ok={n} accuracy={summary['accuracy']} "
                  f"({time.time() - t0:.0f}s elapsed) ==", flush=True)
            if truncated:
                break

    print(f"\nwrote {RESULTS_PATH}, wall {time.time() - t0:.1f}s, truncated={truncated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

