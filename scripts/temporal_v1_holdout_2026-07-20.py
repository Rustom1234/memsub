"""Temporal upgrade, step 5b: the ONE judged comparison, per the task's
strict limit-frugality rule (local qwen3:8b for everything except judging;
Sonnet CLI judge calls ONLY here, nothing larger).

LOCOMO holdout (conversations 4-10, scripts/holdout_answer_quality.py's
holdout_convs()/sample(), seed 123), temporal category (2) only -- the same
n=20 slice used throughout docs/locomo-frontier-v2-2026-07-18.md. Two
conditions, same local qwen3:8b answerer and retrieval-side decompose/
intent-classification calls for both (only retrieval differs):

  baseline   HybridSystem.rank_decomposed_graph (graph_decompose, FROZEN v2
             params) + with_dates=True formatting, budget 2000.
  temporal   HybridSystem.rank_decomposed_graph_temporal (same base, plus
             memsub/temporal.py's query-time intent boost/reorder), same
             with_dates=True formatting, same budget.

Judge: memsub.judge.judge_answer via ClaudeCLIBackend(model="sonnet"),
blind to condition (question/gold/answer only) -- same judge protocol as
every other J-score run in this repo. n=20 questions x 2 conditions = 40
judge calls total, matching the task's "near 40" budget exactly; the
answerer stays local (qwen3:8b) for both conditions, so no other Sonnet
calls happen anywhere in this run.

Streams one JSONL record per (condition, question) to
results/temporal-v1-2026-07-20.jsonl, flushed immediately. Resumable: a
(condition, conversation_id, question) key already recorded with
judge_status "ok" is skipped on restart.

Usage: .venv/bin/python scripts/temporal_v1_holdout_2026-07-20.py [--limit N]
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
from scripts.metrics_local import FROZEN  # noqa: E402
from scripts.temporal_dev_eval import ANSWER_SYSTEM  # noqa: E402 -- terse prompt, dev-slice iteration 2

from memsub.backends import ClaudeCLIBackend, OllamaBackend  # noqa: E402
from memsub.bench import HybridSystem, pack_greedy  # noqa: E402
from memsub.graph import graph_store  # noqa: E402
from memsub.judge import judge_answer  # noqa: E402
from memsub.store import EpisodicStore, format_episode  # noqa: E402

RESULTS_PATH = ROOT / "results" / "temporal-v1-2026-07-20.jsonl"
BUDGET = 2000
CONDITIONS = ["baseline", "temporal"]
TEMPORAL_CATEGORY = 2
MAX_WALL_SECONDS = 3 * 3600


class RetryingBackend:
    """Same retry wrapper as scripts/locomo_jscore_frontier_2026-07-18.py:
    a transient claude-CLI hiccup does not lose a question."""

    def __init__(self, inner, max_retries: int = 6, base_delay: float = 15.0, label: str = "") -> None:
        self.name = inner.name
        self.model = inner.model
        self._inner = inner
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._label = label

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        delay = self._base_delay
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return self._inner.complete(system, messages)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == self._max_retries:
                    break
                print(f"  [retry] {self._label} attempt {attempt}/{self._max_retries} "
                      f"failed: {str(exc)[:200]}; sleeping {delay:.0f}s", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 300.0)
        raise RuntimeError(f"{self._label} failed after {self._max_retries} attempts: {last_exc}") from last_exc


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
    rank_decomposed_graph call as the base: its decompose LLM call is not
    deterministic (a local model's sampling varies), so calling it
    independently per condition would compare two different decompositions
    instead of isolating the temporal variable (found during this task's
    dev-slice iteration, WORKLOG.md 2026-07-20)."""
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

    convs = holdout_convs()
    picked = [(ci, qi) for ci, qi in sample(convs) if convs[ci].qa[qi].category == TEMPORAL_CATEGORY]
    if limit is not None:
        picked = picked[:limit]
    print(f"holdout temporal-category questions: {len(picked)}", flush=True)

    # Retrieval-side calls (decompose, temporal-intent ambiguous escalation)
    # and the answerer both stay local (qwen3:8b) -- only the judge is
    # Sonnet, per the task's hard frugality rule. timeout=240 (up from the
    # 180s default): the dev-slice run hit one ollama socket-read timeout
    # under load (WORKLOG.md), so this run gives it more headroom rather
    # than losing a question outright.
    local_backend = OllamaBackend(model="qwen3:8b", timeout=240.0)
    judge_backend = RetryingBackend(ClaudeCLIBackend(model="sonnet", timeout=180), label="judge")

    existing = _load_existing_ok_keys()
    t0 = time.time()
    truncated = False
    systems: dict[int, tuple] = {}

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as fh:
        for ci, qi in picked:
            conv = convs[ci]
            q = conv.qa[qi]
            keys_needed = [(condition, conv.conversation_id, q.question) for condition in CONDITIONS]
            if all(k in existing for k in keys_needed):
                continue
            if time.time() - t0 > MAX_WALL_SECONDS:
                truncated = True
                break
            if ci not in systems:
                systems[ci] = _build_system(conv)
            system, store = systems[ci]

            try:
                orders = _orders_for(system, store, q.question, local_backend)
            except Exception as exc:  # noqa: BLE001 - a local ollama hiccup must not kill the run
                print(f"  {conv.conversation_id} RETRIEVAL FAILED: {str(exc)[:150]}", flush=True)
                continue

            for condition in CONDITIONS:
                key = (condition, conv.conversation_id, q.question)
                if key in existing:
                    continue

                try:
                    ctx = _context_from_order(system, orders[condition])
                    prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"
                    answer = local_backend.complete(ANSWER_SYSTEM, [{"role": "user", "content": prompt}])
                except Exception as exc:  # noqa: BLE001 - a local ollama hiccup must not kill the run
                    record = {
                        "record_type": "question",
                        "condition": condition,
                        "conversation_id": conv.conversation_id,
                        "question": q.question,
                        "gold": q.answer,
                        "answer": None,
                        "correct": None,
                        "judge_status": "answer_error",
                        "answer_error": str(exc)[:300],
                    }
                    _write(fh, record)
                    print(f"  [{condition}] {conv.conversation_id} ANSWER FAILED: {str(exc)[:150]}", flush=True)
                    continue

                t1 = time.perf_counter()
                verdict = judge_answer(q.question, q.answer, answer, judge_backend)
                judge_latency = time.perf_counter() - t1

                record = {
                    "record_type": "question",
                    "condition": condition,
                    "conversation_id": conv.conversation_id,
                    "question": q.question,
                    "gold": q.answer,
                    "answer": answer,
                    "correct": verdict["correct"],
                    "judge_status": verdict["status"],
                    "judge_latency_s": round(judge_latency, 3),
                }
                _write(fh, record)
                print(f"  [{condition}] {conv.conversation_id} correct={verdict['correct']} "
                      f"status={verdict['status']} ({time.time() - t0:.0f}s)", flush=True)

        for _, store in systems.values():
            store.close()

        if truncated:
            print("WALL-CLOCK CAP HIT: remaining questions skipped.")

        for condition in CONDITIONS:
            rows = [r for r in _all_question_records(condition) if r["judge_status"] == "ok"]
            summary = {
                "record_type": "summary",
                "condition": condition,
                "n_ok": len(rows),
                "n_total_records": len(_all_question_records(condition)),
                "accuracy": round(mean(r["correct"] for r in rows), 4) if rows else None,
                "truncated": truncated,
            }
            _write(fh, summary)
            print(f"== {condition} done: n_ok={summary['n_ok']} accuracy={summary['accuracy']} ==", flush=True)

    print(f"\nwrote {RESULTS_PATH}, wall {time.time() - t0:.1f}s, truncated={truncated}")
    return 1 if truncated else 0


if __name__ == "__main__":
    sys.exit(main())
