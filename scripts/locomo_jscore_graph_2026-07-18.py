"""LOCOMO holdout J-score run for the graph layer's best config vs the shipped
baseline, following the exact protocol of scripts/locomo_jscore_holdout.py:
same slice (scripts/holdout_answer_quality.py's holdout_convs()/sample(),
LOCOMO conversations 4-10, up to 20 questions per category, seed 123, n=80),
same judge (memsub.judge.judge_answer via ClaudeCLIBackend model="sonnet",
blind to condition -- sees only question/gold/answer), same local answerer
(qwen3:8b via OllamaBackend).

Two conditions:

  memsub_hybrid_v2   shipped default (FROZEN hybrid v2 params), unmodified --
                     identical to locomo_jscore_holdout.py's own baseline row.
  GRAPH_CONDITION    the graph-layer config that scored best on the dev
                     answer-quality eval (results/graph-layer-eval-2026-07-18
                     .jsonl / docs/graph-layer-2026-07-18.md), set below.

Streams one JSONL record per (condition, question) to
results/locomo-jscore-2026-07-18.jsonl, flushed immediately after every
record. Resumable: a (condition, conversation_id, question) key already
recorded with judge status "ok" is skipped on restart.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.holdout_answer_quality import holdout_convs, sample  # noqa: E402
from scripts.metrics_local import ANSWER_SYSTEM, BUDGET, CATS, FROZEN  # noqa: E402

from memsub.backends import ClaudeCLIBackend, OllamaBackend  # noqa: E402
from memsub.bench import HybridSystem, pack_greedy  # noqa: E402
from memsub.graph import graph_store  # noqa: E402
from memsub.judge import judge_answer  # noqa: E402
from memsub.store import EpisodicStore  # noqa: E402

RESULTS_PATH = ROOT / "results" / "locomo-jscore-2026-07-18.jsonl"
# Set from results/graph-layer-eval-2026-07-18.jsonl's winning condition
# (docs/graph-layer-2026-07-18.md records the decision): "graph" or
# "graph_decompose".
GRAPH_CONDITION = "graph_decompose"
CONDITIONS = ["memsub_hybrid_v2", GRAPH_CONDITION]
MAX_WALL_SECONDS = 3 * 3600


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


def _build_system(condition: str, conv, answer_backend):
    """memsub_hybrid_v2: plain HybridSystem, unmodified (identical to
    locomo_jscore_holdout.py's build_system("memsub_hybrid_v2", ...)).
    Graph conditions: one EpisodicStore per conversation, graph_store() run
    once (rule-based only -- no backend passed, matching the dev eval and
    `memsub graph backfill`'s zero-LLM default), ingested into a HybridSystem
    with id-stable episodes so rank_graph's store lookups agree with the
    retriever's in-memory index.
    """
    system = HybridSystem(**FROZEN)
    if condition == "memsub_hybrid_v2":
        system.ingest(list(conv.episodes))
        return system, None
    store = EpisodicStore(":memory:")
    written = [store.append(ep) for ep in conv.episodes]
    graph_store(store)
    system.ingest(written)
    return system, store


def _context_for(condition: str, system, store, question: str, answer_backend) -> str:
    if condition == "memsub_hybrid_v2":
        packed = system.inject(question, BUDGET)
    elif condition == "graph":
        order = system.rank_graph(question, store)
        packed = pack_greedy([system.episodes[i] for i in order], BUDGET)
    else:  # graph_decompose
        order = system.rank_decomposed_graph(question, answer_backend, store, max_subqueries=2)
        packed = pack_greedy([system.episodes[i] for i in order], BUDGET)
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
            systems: dict[int, tuple] = {}

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
                    systems[ci] = _build_system(condition, conv, answer_backend)
                system, store = systems[ci]

                ctx = _context_for(condition, system, store, q.question, answer_backend)
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

            for system, store in systems.values():
                if store is not None:
                    store.close()

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

