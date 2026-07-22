"""Answer-quality eval for the graph layer (memsub/graph.py, memsub/hybrid.py's
rank_graph/rank_decomposed_graph), following the exact precedent of
scripts/multihop_answer_quality_eval.py (same slice, same machinery, same
metric): reuses ANSWER_SYSTEM, BUDGET, FROZEN, token_f1 unchanged from
scripts/metrics_local.py, so these numbers are internally comparable to the
documented 0.1946 baseline / 0.2029 fusion+decompose row in
docs/multihop-improvements-2026-07-17.md.

Slice: LOCOMO dev conversations 1-3 (never holdout), category-1 (multi-hop)
questions with a non-empty gold answer, n=74 -- the same full multi-hop dev
slice as the precedent script (docs/multihop-improvements-2026-07-17.md
already established the full slice fits comfortably inside the wall-clock
budget).

Three conditions:

  baseline           hybrid v2 (FROZEN params), unmodified -- identical to
                      multihop_answer_quality_eval.py's baseline row.
  graph               same retriever, graph_store() run once per conversation
                      (rule-based, --backend none equivalent: zero LLM calls
                      for extraction/dedup on this data), HybridRetriever
                      .rank_graph for the question (zero further LLM calls,
                      1-hop default).
  graph_decompose      graph_store() plus HybridRetriever.rank_decomposed_graph
                      (one bounded decompose LLM call per question, composed
                      with the graph signal, per report section 4's guidance
                      that graph should compose with decomposition rather
                      than replace it, since decomposition is the current
                      best measured config).

Streams one JSONL record per (condition, conversation, question) to
results/graph-layer-eval-2026-07-18.jsonl, flushed immediately, resumable
(records already on disk are skipped on restart).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memsub.backends import OllamaBackend, ollama_available  # noqa: E402
from memsub.bench import HybridSystem, load_locomo, pack_greedy  # noqa: E402
from memsub.graph import graph_store  # noqa: E402
from memsub.store import EpisodicStore  # noqa: E402
from scripts.metrics_local import ANSWER_SYSTEM, BUDGET, FROZEN, token_f1  # noqa: E402

RESULTS_PATH = ROOT / "results" / "graph-layer-eval-2026-07-18.jsonl"
CONDITIONS = ["baseline", "graph", "graph_decompose"]
MAX_WALL_SECONDS = 90 * 60  # same cap as the precedent script


def _multihop_questions(convs):
    out = []
    for ci, conv in enumerate(convs):
        for q in conv.qa:
            if q.category == 1 and q.answer:
                out.append((ci, q))
    return out


def _load_existing_keys() -> set[tuple]:
    if not RESULTS_PATH.exists():
        return set()
    keys = set()
    for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("record_type") == "question":
            keys.add((rec["condition"], rec["conversation_id"], rec["question"]))
    return keys


def _write_record(fh, record: dict) -> None:
    fh.write(json.dumps(record) + "\n")
    fh.flush()


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


def _build_graph_system(episodes, store):
    """One EpisodicStore + graph_store() pass, and a HybridSystem ingesting
    the same (id-stable) episodes so rank_graph's store lookups and the
    retriever's in-memory index agree on episode ids."""
    written = [store.append(ep) for ep in episodes]
    graph_store(store)  # rule-based only: no backend given, zero LLM calls here
    system = HybridSystem(**FROZEN)
    system.ingest(written)
    return system


def main() -> int:
    if not ollama_available():
        print("ollama unreachable; aborting. See WORKLOG.md for the fallback note.")
        return 1

    backend = OllamaBackend(model="qwen3:8b", think=False)
    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]  # DEV ONLY
    sample = _multihop_questions(convs)

    existing = _load_existing_keys()
    t0 = time.time()
    truncated = False

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as fh:
        for condition in CONDITIONS:
            systems_by_conv: dict[int, object] = {}
            stores_by_conv: dict[int, EpisodicStore] = {}

            for ci, q in sample:
                conv = convs[ci]
                key = (condition, conv.conversation_id, q.question)
                if key in existing:
                    continue
                if time.time() - t0 > MAX_WALL_SECONDS:
                    truncated = True
                    break

                if ci not in systems_by_conv:
                    if condition == "baseline":
                        system = HybridSystem(**FROZEN)
                        system.ingest(list(conv.episodes))
                        systems_by_conv[ci] = system
                    else:
                        store = EpisodicStore(":memory:")
                        stores_by_conv[ci] = store
                        systems_by_conv[ci] = _build_graph_system(conv.episodes, store)
                system = systems_by_conv[ci]

                if condition == "baseline":
                    packed = system.inject(q.question, BUDGET)
                elif condition == "graph":
                    order = system.rank_graph(q.question, stores_by_conv[ci])
                    packed = pack_greedy([system.episodes[i] for i in order], BUDGET)
                else:  # graph_decompose
                    order = system.rank_decomposed_graph(
                        q.question, backend, stores_by_conv[ci], max_subqueries=2
                    )
                    packed = pack_greedy([system.episodes[i] for i in order], BUDGET)

                ctx = "\n".join(ep.content for ep in packed)
                prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"

                t1 = time.perf_counter()
                answer = backend.complete(ANSWER_SYSTEM, [{"role": "user", "content": prompt}])
                latency = time.perf_counter() - t1
                f1 = token_f1(answer, q.answer)
                exact = int(q.answer.lower() in answer.lower())

                record = {
                    "record_type": "question",
                    "condition": condition,
                    "conversation_id": conv.conversation_id,
                    "question": q.question,
                    "gold_answer": q.answer,
                    "prediction": answer,
                    "category": q.category,
                    "budget": BUDGET,
                    "f1": round(f1, 4),
                    "exact_substring": bool(exact),
                    "latency_s": round(latency, 3),
                }
                _write_record(fh, record)
                print(f"  [{condition}] {conv.conversation_id} f1={f1:.3f} "
                      f"exact={exact} ({time.time() - t0:.0f}s)")

            for store in stores_by_conv.values():
                store.close()

            if truncated:
                print("WALL-CLOCK CAP HIT: remaining questions/conditions skipped, "
                      "see WORKLOG.md")

            rows = _all_question_records(condition)
            n = len(rows)
            summary = {
                "record_type": "summary",
                "condition": condition,
                "budget": BUDGET,
                "answerer_model": backend.model,
                "truncated": truncated,
                "n": n,
                "mean_f1": round(mean(r["f1"] for r in rows), 4) if rows else None,
                "exact_substring_rate": round(mean(r["exact_substring"] for r in rows), 4)
                if rows else None,
                "mean_answer_latency_s": round(mean(r["latency_s"] for r in rows), 3)
                if rows else None,
            }
            _write_record(fh, summary)
            print(f"== {condition} done: n={n} "
                  f"mean_f1={summary['mean_f1']} exact={summary['exact_substring_rate']} "
                  f"({time.time() - t0:.0f}s elapsed) ==")
            if truncated:
                break

    print(f"\nwrote {RESULTS_PATH}, wall {time.time() - t0:.1f}s, truncated={truncated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
