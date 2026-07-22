"""Task 3 (2026-07-20): the ONE bounded judged comparison, per the task's
hard limit-frugality rule (Sonnet CLI judge ONLY, capped at 40 calls total
for this whole round; local qwen3:8b for retrieval-side decompose calls and
answering in both conditions).

LOCOMO HOLDOUT (conversations 4-10, scripts/holdout_answer_quality.py's
holdout_convs(), never touched by Task 1/2's dev-slice calibration/training)
n=20 mixed slice, seed 2026: 10 multi-hop (category 1), 5 open-domain
(category 3), 5 temporal (category 2).

Two conditions, sharing ONE `rank_decomposed_graph` call per question as the
base (its one decompose LLM call is not deterministic -- same base_order
fairness fix as docs/temporal-v1-2026-07-20.md):

  baseline          "best prior config": rank_decomposed_graph (graph_decompose,
                    FROZEN v2 params) + with_dates=True formatting, budget 2000,
                    local qwen3:8b answerer, best-effort prompt
                    (ANSWER_SYSTEM_BEST_EFFORT, docs/locomo-frontier-v2-2026-07-18.md
                    Task 2 -- always attempts an answer, never abstains).
  reranked_abstain  SAME base_order, reordered by the Task 2 learned reranker
                    (results/reranker-weights-2026-07-20.json) over its
                    top-POOL_SIZE pool, same with_dates/budget formatting;
                    before answering, the Task 1 abstention policy
                    (results/abstain-weights-2026-07-20.json, threshold from
                    results/abstain-threshold-sweep-2026-07-20.json) scores
                    the retrieved evidence and answers "Not enough stored
                    context..." instead of calling the answerer at all when
                    below threshold.

Judge: memsub.judge.judge_answer via ClaudeCLIBackend(model="sonnet"), blind
to condition -- same protocol as every other J-score run in this repo,
including on an abstained answer (no special-casing: if the judge credits or
penalizes "Not enough stored context...", that is reported honestly, not
patched around). n=20 x 2 conditions = 40 judge calls total, matching the
task's cap exactly -- no other Sonnet calls anywhere in this script.

Streams one JSONL record per (condition, question) to
results/rerank-abstain-2026-07-20.jsonl, flushed immediately, resumable.

Usage: .venv/bin/python scripts/rerank_abstain_holdout_2026-07-20.py [--limit N]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import random  # noqa: E402

from scripts.holdout_answer_quality import holdout_convs  # noqa: E402
from scripts.metrics_local import CATS, FROZEN  # noqa: E402

from memsub.answer_policy import ABSTAIN_MESSAGE, AbstentionPolicy, evidence_features  # noqa: E402
from memsub.backends import ClaudeCLIBackend, OllamaBackend  # noqa: E402
from memsub.bench import HybridSystem, pack_greedy  # noqa: E402
from memsub.graph import graph_store  # noqa: E402
from memsub.judge import judge_answer  # noqa: E402
from memsub.rerank import load_weights  # noqa: E402
from memsub.store import EpisodicStore, format_episode  # noqa: E402

RESULTS_PATH = ROOT / "results" / "rerank-abstain-2026-07-20.jsonl"
BUDGET = 2000
POOL_SIZE = 50
CONDITIONS = ["baseline", "reranked_abstain"]
SEED = 2026
SLICE_SPEC = [(1, 10), (3, 5), (2, 5)]  # (category, count): 10 multi-hop, 5 open-domain, 5 temporal
MAX_WALL_SECONDS = 3 * 3600

RERANKER_WEIGHTS_PATH = ROOT / "results" / "reranker-weights-2026-07-20.json"
ABSTAIN_WEIGHTS_PATH = ROOT / "results" / "abstain-weights-2026-07-20.json"
ABSTAIN_SWEEP_PATH = ROOT / "results" / "abstain-threshold-sweep-2026-07-20.json"

# Same constant as scripts/locomo_jscore_frontier_2026-07-18.py's
# ANSWER_SYSTEM_BEST_EFFORT (that filename has a hyphen, so it cannot be
# `import`ed as a module -- duplicated here verbatim rather than reached for
# via importlib machinery for one small string constant). Both conditions
# use this SAME best-effort prompt so the comparison isolates retrieval +
# abstention, not answer-prompt conservatism (docs/locomo-frontier-v2-2026-07-18.md
# Task 2's refusal-count finding).
ANSWER_SYSTEM_BEST_EFFORT = (
    "Answer the question in one short sentence using the information in "
    "CONTEXT. Be concise and direct. If CONTEXT does not clearly state the "
    "answer, give your best guess based on what CONTEXT implies rather than "
    "refusing -- but never invent a fact that has no support in CONTEXT."
)


class RetryingBackend:
    """Same retry wrapper as scripts/temporal_v1_holdout_2026-07-20.py: a
    transient claude-CLI hiccup does not lose a question."""

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


def sample_mixed_slice(convs) -> list[tuple[int, int]]:
    by_cat: dict[int, list[tuple[int, int]]] = {}
    for ci, conv in enumerate(convs):
        for qi, q in enumerate(conv.qa):
            if q.category in {1, 2, 3} and q.answer:
                by_cat.setdefault(q.category, []).append((ci, qi))
    rng = random.Random(SEED)
    picked: list[tuple[int, int]] = []
    for cat, count in SLICE_SPEC:
        qs = by_cat.get(cat, [])
        rng.shuffle(qs)
        picked.extend(qs[:count])
    return picked


def load_threshold() -> tuple[float, bool]:
    """Returns (threshold, was_calibrated). Falls back to the module default
    with an explicit flag if the sweep found no threshold meeting Task 1's
    criteria, or the sweep file is missing -- reported honestly either way,
    never silently loosened."""
    if not ABSTAIN_SWEEP_PATH.exists():
        return 0.5, False
    sweep = json.loads(ABSTAIN_SWEEP_PATH.read_text())
    chosen = sweep.get("chosen_threshold")
    if chosen is None:
        return 0.5, False
    return float(chosen), True


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
    graph_store(store)  # backend=None: zero LLM calls
    system.ingest(written)
    return system, store


def _context_from_order(system, order: list[int]) -> str:
    packed = pack_greedy([system.episodes[i] for i in order], BUDGET)
    return "\n".join(format_episode(ep, with_dates=True) for ep in packed)


def main() -> int:
    limit = None
    args = sys.argv[1:]
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])

    convs = holdout_convs()
    picked = sample_mixed_slice(convs)
    if limit is not None:
        picked = picked[:limit]
    print(f"holdout mixed-slice questions: {len(picked)}", flush=True)

    reranker_weights = load_weights(RERANKER_WEIGHTS_PATH)
    threshold, calibrated = load_threshold()
    abstain_weights = load_weights(ABSTAIN_WEIGHTS_PATH)
    abstain_policy = AbstentionPolicy(weights=abstain_weights, threshold=threshold)
    print(f"abstention threshold: {threshold} (from calibration sweep: {calibrated})", flush=True)

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
                base_order = system.rank_decomposed_graph(q.question, local_backend, store)
            except Exception as exc:  # noqa: BLE001 - a local ollama hiccup must not kill the run
                print(f"  {conv.conversation_id} RETRIEVAL FAILED: {str(exc)[:150]}", flush=True)
                continue

            for condition in CONDITIONS:
                key = (condition, conv.conversation_id, q.question)
                if key in existing:
                    continue

                try:
                    if condition == "baseline":
                        ctx = _context_from_order(system, base_order)
                        abstained, score = False, None
                        prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"
                        answer = local_backend.complete(ANSWER_SYSTEM_BEST_EFFORT, [{"role": "user", "content": prompt}])
                    else:
                        reranked_order = system.rank_reranked(
                            q.question, reranker_weights, store=store, pool_size=POOL_SIZE, base_order=base_order,
                        )
                        feats = evidence_features(system, q.question, store=store)
                        score = abstain_policy.score(feats)
                        abstained = score < abstain_policy.threshold
                        if abstained:
                            answer = None  # ABSTAIN_MESSAGE filled in below; no answerer call at all
                        else:
                            ctx = _context_from_order(system, reranked_order)
                            prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"
                            answer = local_backend.complete(ANSWER_SYSTEM_BEST_EFFORT, [{"role": "user", "content": prompt}])
                except Exception as exc:  # noqa: BLE001 - a local ollama hiccup must not kill the run
                    record = {
                        "record_type": "question",
                        "condition": condition,
                        "conversation_id": conv.conversation_id,
                        "question": q.question,
                        "category": q.category,
                        "category_name": CATS.get(q.category, "?"),
                        "gold": q.answer,
                        "answer": None,
                        "correct": None,
                        "judge_status": "answer_error",
                        "answer_error": str(exc)[:300],
                    }
                    _write(fh, record)
                    print(f"  [{condition}] {conv.conversation_id} ANSWER FAILED: {str(exc)[:150]}", flush=True)
                    continue

                if condition == "reranked_abstain" and abstained:
                    answer = ABSTAIN_MESSAGE

                t1 = time.perf_counter()
                verdict = judge_answer(q.question, q.answer, answer, judge_backend)
                judge_latency = time.perf_counter() - t1

                record = {
                    "record_type": "question",
                    "condition": condition,
                    "conversation_id": conv.conversation_id,
                    "question": q.question,
                    "category": q.category,
                    "category_name": CATS.get(q.category, "?"),
                    "gold": q.answer,
                    "answer": answer,
                    "correct": verdict["correct"],
                    "judge_status": verdict["status"],
                    "judge_latency_s": round(judge_latency, 3),
                }
                if condition == "reranked_abstain":
                    record["abstained"] = abstained
                    record["evidence_score"] = round(score, 4) if score is not None else None
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
            if condition == "reranked_abstain" and rows:
                n_abstained = sum(1 for r in rows if r.get("abstained"))
                summary["n_abstained"] = n_abstained
                summary["abstain_rate"] = round(n_abstained / len(rows), 4)
            _write(fh, summary)
            print(f"== {condition} done: n_ok={summary['n_ok']} accuracy={summary['accuracy']} ==", flush=True)

    print(f"\nwrote {RESULTS_PATH}, wall {time.time() - t0:.1f}s, truncated={truncated}")
    return 1 if truncated else 0


if __name__ == "__main__":
    sys.exit(main())
