"""Temporal upgrade, step 5a: dev-slice answer-quality check, temporal
category only, local qwen3:8b answerer, NO judge (deterministic token-F1).

Compares two conditions on LOCOMO dev conversations' (convs[:3], never the
holdout split) temporal-category (category 2) questions:

  baseline   graph_decompose retrieval (HybridSystem.rank_decomposed_graph,
             FROZEN v2 params) + with_dates=True context formatting
             (memsub.store.format_episode), budget 2000.
  temporal   the same retrieval, then memsub.hybrid.HybridRetriever's new
             rank_decomposed_graph_temporal boost/reorder on top (memsub/
             temporal.py's query-time intent classification), same
             with_dates=True formatting, same budget.

Purpose: cheap, judge-free, local-only sanity check per the task's "iterate
cheaply until it beats the with_dates baseline there" instruction, gating
whether the expensive n=20 Sonnet-judged holdout run (scripts/
temporal_v1_holdout_2026-07-20.py) is worth spending judge calls on.

Streams one JSONL record per (condition, conversation_id, question) to
results/temporal-dev-eval-2026-07-20.jsonl, flushed immediately (durable
output rule: never only in chat). Resumable: a key already recorded is
skipped on restart.

Usage: .venv/bin/python scripts/temporal_dev_eval.py [--limit N]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.metrics_local import FROZEN, token_f1  # noqa: E402

from memsub.backends import OllamaBackend  # noqa: E402
from memsub.bench import HybridSystem, load_locomo, pack_greedy  # noqa: E402
from memsub.graph import graph_store  # noqa: E402
from memsub.store import EpisodicStore, format_episode  # noqa: E402

RESULTS_PATH = ROOT / "results" / "temporal-dev-eval-2026-07-20.jsonl"
BUDGET = 2000
CONDITIONS = ["baseline", "temporal"]
# First pass (metrics_local.ANSWER_SYSTEM, unchanged, used by history) showed
# temporal *losing* on token-F1 (0.2688 vs baseline 0.2914) even though a
# by-hand read of the differing answers showed temporal was often MORE date-
# correct (e.g. "18 July" vs baseline's wrong "20 July") -- token-F1's
# precision term just punishes the extra "this is inferred from..." framing
# that richer temporal context sometimes invites. Same fix pattern as
# docs/locomo-frontier-v2-2026-07-18.md Task 2 (a local, eval-only stricter
# prompt, metrics_local.ANSWER_SYSTEM itself untouched): force terse answers
# for both conditions so the metric measures date correctness, not verbosity.
ANSWER_SYSTEM = (
    "Answer the question with the shortest possible phrase (usually just a "
    "date) using ONLY the information in CONTEXT. Do not explain your "
    "reasoning or cite the source message. If CONTEXT does not contain the "
    "answer, say you don't know."
)


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


def _write(fh, record: dict) -> None:
    fh.write(json.dumps(record) + "\n")
    fh.flush()


def temporal_dev_questions(convs) -> list[tuple[int, object]]:
    out = []
    for ci, conv in enumerate(convs):
        for q in conv.qa:
            if q.category == 2 and q.answer:
                out.append((ci, q))
    return out


def _build_system(conv):
    system = HybridSystem(**FROZEN)
    store = EpisodicStore(":memory:")
    written = [store.append(ep) for ep in conv.episodes]
    graph_store(store)
    system.ingest(written)
    return system, store


def _context_from_order(system, order: list[int]) -> str:
    packed = pack_greedy([system.episodes[i] for i in order], BUDGET)
    return "\n".join(format_episode(ep, with_dates=True) for ep in packed)


def _orders_for(system, store, question: str, backend) -> dict[str, list[int]]:
    """Both conditions' rankings for one question, sharing ONE
    rank_decomposed_graph call as the base for both. rank_decomposed_graph's
    decompose LLM call is not deterministic (a local model's sampling
    varies), so calling it independently per condition would compare two
    different decompositions instead of isolating the temporal variable
    (found during this task's dev-slice iteration -- see WORKLOG.md
    2026-07-20 and memsub.hybrid.HybridRetriever.rank_decomposed_graph_
    temporal's base_order parameter, added for exactly this)."""
    base_order = system.rank_decomposed_graph(question, backend, store)
    temporal_order = system.rank_decomposed_graph_temporal(
        question, backend, store, temporal_backend=backend, base_order=base_order,
    )
    return {"baseline": base_order, "temporal": temporal_order}


def main() -> int:
    limit = None
    args = sys.argv[1:]
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])

    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]  # DEV split only
    picked = temporal_dev_questions(convs)
    if limit is not None:
        picked = picked[:limit]
    print(f"dev temporal-category questions: {len(picked)}", flush=True)

    backend = OllamaBackend(model="qwen3:8b")
    existing = _load_existing_keys()

    systems: dict[int, tuple] = {}
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as fh:
        for ci, q in picked:
            keys_needed = [
                (condition, convs[ci].conversation_id, q.question)
                for condition in CONDITIONS
            ]
            if all(k in existing for k in keys_needed):
                continue
            if ci not in systems:
                systems[ci] = _build_system(convs[ci])
            system, store = systems[ci]
            try:
                orders = _orders_for(system, store, q.question, backend)
            except Exception as exc:  # noqa: BLE001 - a local ollama hiccup must not kill the run
                print(f"  {convs[ci].conversation_id} SKIPPED (retrieval error): {str(exc)[:150]}", flush=True)
                continue
            for condition in CONDITIONS:
                key = (condition, convs[ci].conversation_id, q.question)
                if key in existing:
                    continue
                try:
                    ctx = _context_from_order(system, orders[condition])
                    prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"
                    answer = backend.complete(ANSWER_SYSTEM, [{"role": "user", "content": prompt}])
                except Exception as exc:  # noqa: BLE001 - a local ollama hiccup must not kill the run
                    print(f"  [{condition}] {convs[ci].conversation_id} SKIPPED (error): {str(exc)[:150]}", flush=True)
                    continue
                f1 = token_f1(answer, q.answer)
                record = {
                    "record_type": "question",
                    "condition": condition,
                    "conversation_id": convs[ci].conversation_id,
                    "question": q.question,
                    "gold": q.answer,
                    "answer": answer,
                    "f1": round(f1, 4),
                }
                _write(fh, record)
                print(f"  [{condition}] {convs[ci].conversation_id} f1={f1:.3f}", flush=True)

        for _, store in systems.values():
            store.close()

    # Report: mean F1 per condition, over every "ok" record on disk (not just
    # this run's), so a resumed run still reports the full picture.
    rows = []
    for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    for condition in CONDITIONS:
        cond_rows = [r for r in rows if r["condition"] == condition]
        if cond_rows:
            print(f"== {condition}: n={len(cond_rows)} mean_f1={mean(r['f1'] for r in cond_rows):.4f} ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
