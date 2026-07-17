"""Fair-test follow-up to scripts/multihop_improvements_eval.py: that eval's own
verdict named the flaw in evidence-recall grading -- a fused entry contains the right
information but is never the original evidence turn, so a turn-level evidence metric
can only ever cost fusion budget, never credit it. This script measures ANSWER QUALITY
instead: does the local model actually answer multi-hop questions better with fused
context, with or without query-time decomposition?

Reuses the repo's existing answer-quality machinery exactly (per instructions: do not
build a new harness) -- ANSWER_SYSTEM, BUDGET, FROZEN (the frozen hybrid v2 params),
and token_f1 all imported unchanged from scripts/metrics_local.py, the same
judge-free deterministic token-F1 protocol that produced the documented 0.152 holdout
number, so this run's numbers are internally comparable to that one.

Multi-hop slice: LOCOMO dev conversations 1-3 (never holdout), category 1 questions
with a non-empty gold answer, n=74 (pre-registered: this is the full multi-hop dev
slice, already >= the 60-question minimum, so no random subset was needed; see
docs/multihop-improvements-2026-07-17.md for the timing check that established this
fits well inside the ~90 minute budget).

Three conditions, same fusion isolation as multihop_improvements_eval.py (Assumption 7,
WORKLOG.md): contradiction and decay disabled in the eval's ConsolidationConfig so only
dedup (unchanged, shipped) and the new fusion phase affect the ingested episode set.

  baseline          hybrid v2 (FROZEN params), unmodified.
  fusion            same retriever, episodes pre-processed once per conversation through
                     consolidate()'s new fusion phase before ingest.
  fusion_decompose  same fused episode set, HybridRetriever.rank_decomposed for the
                     question (one bounded LLM call per question).

Streams one JSONL record per (condition, conversation, question) to
results/multihop-answer-quality-2026-07-17.jsonl, flushed immediately, resumable
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
from memsub.consolidate import ConsolidationConfig, consolidate  # noqa: E402
from memsub.store import EpisodicStore  # noqa: E402
from scripts.metrics_local import ANSWER_SYSTEM, BUDGET, FROZEN, token_f1  # noqa: E402

RESULTS_PATH = ROOT / "results" / "multihop-answer-quality-2026-07-17.jsonl"
CONDITIONS = ["baseline", "fusion", "fusion_decompose"]
MAX_WALL_SECONDS = 90 * 60  # coordinator's explicit cap for this run
SUBSET_SEED = 2026  # pre-registered fallback seed, only used if the full slice
MIN_SUBSET = 60  # would exceed MAX_WALL_SECONDS (see module docstring / WORKLOG.md)


def _fuse_episodes(episodes, backend):
    """Same isolation as multihop_improvements_eval.py: contradiction and decay
    disabled so only dedup (unchanged) and fusion affect the ingested set."""
    store = EpisodicStore(":memory:")
    for ep in episodes:
        store.append(ep)
    cfg = ConsolidationConfig(contradiction_min_sim=1.1, decay_value_threshold=0.0)
    consolidate(store, backend=backend, config=cfg)
    fused = store.active()
    store.close()
    return fused


def _multihop_questions(convs):
    """(conv_index, question) pairs: category 1, non-empty gold answer, dev split."""
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
    """Every question record for `condition` currently on disk, this run's plus any
    from an earlier partial run: makes the summary correct even after a resume."""
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


def main() -> int:
    if not ollama_available():
        print("ollama unreachable; aborting. See WORKLOG.md for the fallback note.")
        return 1

    backend = OllamaBackend(model="qwen3:8b", think=False)
    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]  # DEV ONLY
    sample = _multihop_questions(convs)

    if len(sample) < MIN_SUBSET:
        print(f"WARNING: only {len(sample)} multi-hop questions with a gold answer, "
              f"below the {MIN_SUBSET} minimum; running the full available set anyway.")

    existing = _load_existing_keys()
    t0 = time.time()
    truncated = False

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as fh:
        for condition in CONDITIONS:
            rows: list[dict] = []
            systems_by_conv: dict[int, object] = {}

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
                        eps = list(conv.episodes)
                    else:
                        eps = _fuse_episodes(conv.episodes, backend)
                    system = HybridSystem(**FROZEN)
                    system.ingest(eps)
                    systems_by_conv[ci] = system
                system = systems_by_conv[ci]

                if condition == "fusion_decompose":
                    order = system.rank_decomposed(q.question, backend, max_subqueries=2)
                    packed = pack_greedy([system.episodes[i] for i in order], BUDGET)
                else:
                    packed = system.inject(q.question, BUDGET)
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
                rows.append(record)
                print(f"  [{condition}] {conv.conversation_id} f1={f1:.3f} "
                      f"exact={exact} ({time.time() - t0:.0f}s)")

            if truncated:
                print("WALL-CLOCK CAP HIT: remaining questions/conditions skipped, "
                      "see WORKLOG.md")

            rows = _all_question_records(condition)  # this run's + any earlier partial run
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
