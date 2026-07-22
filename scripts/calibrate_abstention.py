"""Task 1 (2026-07-20): calibrate the abstention policy.

Dev-slice run (LOCOMO dev conversations, categories 1-4, stratified sample,
seed 42, mirroring scripts/metrics_local.sample_stratified's convention),
"best prior config" retrieval (HybridSystem FROZEN params,
rank_decomposed_graph -- one local qwen3:8b decompose call per question,
zero cloud calls -- + format_episode(with_dates=True), budget 2000, per
docs/temporal-v1-2026-07-20.md).

Labels each question "answerable-from-retrieved" (1) or not (0) purely by
gold-token overlap with the packed retrieved context (the task's own
literal wording) -- coverage = |gold_tokens ∩ context_tokens| / |gold_tokens|,
answerable iff coverage >= LABEL_COVERAGE_THRESHOLD. This label doubles as
the calibration's proxy ground truth for "would the answerer have gotten
this right" (documented as a proxy, not a real judged accuracy number --
see docs/rerank-abstain-2026-07-20.md). No answerer LLM call is needed to
build this label at all.

For each question, also computes memsub.answer_policy.evidence_features()
(gold-blind, query/retrieval-only) via the SAME retrieval order. Trains a
tiny logistic model (memsub.rerank.train_logistic, reused from Task 2)
mapping the 4 evidence features to P(answerable), then sweeps a threshold
grid on a held-out val split of questions to find the value satisfying this
task's pre-registered success criteria (WORKLOG.md 2026-07-20):

  >= 70% of abstained (below-threshold) questions are ones that were
     "would-have-been-wrong" (label == 0)
  <= 5% of "would-have-been-right" (label == 1) questions get abstained on

Streams one row per question to results/abstain-calibration-2026-07-20.jsonl
(durable, resumable). Saves results/abstain-weights-2026-07-20.json and
prints the full threshold sweep table.

Usage: .venv/bin/python scripts/calibrate_abstention.py [--limit N]
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from memsub.answer_policy import FEATURE_NAMES, evidence_features, evidence_vector  # noqa: E402
from memsub.backends import OllamaBackend  # noqa: E402
from memsub.bench import HybridSystem, pack_greedy, load_locomo  # noqa: E402
from memsub.graph import graph_store  # noqa: E402
from memsub.rerank import apply_logistic, save_weights, train_logistic  # noqa: E402
from memsub.store import EpisodicStore, format_episode  # noqa: E402
from scripts.metrics_local import CATS, FROZEN  # noqa: E402

RESULTS_PATH = ROOT / "results" / "abstain-calibration-2026-07-20.jsonl"
WEIGHTS_PATH = ROOT / "results" / "abstain-weights-2026-07-20.json"
BUDGET = 2000
PER_CATEGORY = 30  # caps total near 120, stratified across categories 1-4 (dev availability caps some)
SEED = 42
VAL_FRACTION = 0.3
LABEL_COVERAGE_THRESHOLD = 0.5  # "answerable" iff >=half the gold answer's tokens are in context
MAX_WALL_SECONDS = 3 * 3600

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def gold_coverage(gold: str, context: str) -> float:
    gold_toks = _tokens(gold)
    if not gold_toks:
        return 0.0
    ctx_toks = _tokens(context)
    return len(gold_toks & ctx_toks) / len(gold_toks)


def sample_dev_slice(per_category: int, seed: int) -> list[tuple[int, object]]:
    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]  # DEV split only
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
    return picked, convs


def _load_existing_keys() -> set[tuple]:
    if not RESULTS_PATH.exists():
        return set()
    keys = set()
    for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("record_type") == "question":
            keys.add((rec["conversation_id"], rec["question"]))
    return keys


def _write(fh, record: dict) -> None:
    fh.write(json.dumps(record) + "\n")
    fh.flush()


def build_calibration_set(limit: int | None) -> list[dict]:
    picked, convs = sample_dev_slice(PER_CATEGORY, SEED)
    if limit is not None:
        picked = picked[:limit]
    print(f"dev-slice questions sampled: {len(picked)}", flush=True)

    existing = _load_existing_keys()
    backend = OllamaBackend(model="qwen3:8b", timeout=240.0)
    systems: dict[int, tuple] = {}
    t0 = time.time()
    truncated = False

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as fh:
        for ci, q in picked:
            conv = convs[ci]
            if (conv.conversation_id, q.question) in existing:
                continue
            if time.time() - t0 > MAX_WALL_SECONDS:
                truncated = True
                break
            if ci not in systems:
                system = HybridSystem(**FROZEN)
                store = EpisodicStore(":memory:")
                written = [store.append(ep) for ep in conv.episodes]
                graph_store(store)  # backend=None: zero LLM calls
                system.ingest(written)
                systems[ci] = (system, store)
            system, store = systems[ci]

            try:
                order = system.rank_decomposed_graph(q.question, backend, store)
                packed = pack_greedy([system.episodes[i] for i in order], BUDGET)
                context = "\n".join(format_episode(ep, with_dates=True) for ep in packed)
                coverage = gold_coverage(q.answer, context)
                feats = evidence_features(system, q.question, store=store)
            except Exception as exc:  # noqa: BLE001 - a local ollama hiccup must not kill the run
                print(f"  {conv.conversation_id} FAILED: {str(exc)[:150]}", flush=True)
                continue

            record = {
                "record_type": "question",
                "conversation_id": conv.conversation_id,
                "question": q.question,
                "category": q.category,
                "category_name": CATS.get(q.category, "?"),
                "gold": q.answer,
                "gold_coverage": round(coverage, 4),
                "answerable": int(coverage >= LABEL_COVERAGE_THRESHOLD),
                "features": feats,
            }
            _write(fh, record)
            print(f"  {conv.conversation_id} answerable={record['answerable']} "
                  f"coverage={coverage:.2f} ({time.time() - t0:.0f}s)", flush=True)

    for _, store in systems.values():
        store.close()
    if truncated:
        print("WALL-CLOCK CAP HIT: remaining questions skipped.", flush=True)

    return [json.loads(line) for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def sweep_thresholds(weights: dict, val_rows: list[dict]) -> list[dict]:
    scored = [
        (r, apply_logistic(weights, evidence_vector(r["features"])), r["answerable"])
        for r in val_rows
    ]
    n_right = sum(1 for _, _, label in scored if label == 1)
    n_wrong = sum(1 for _, _, label in scored if label == 0)
    rows = []
    for t in [round(0.05 * i, 2) for i in range(1, 20)]:
        abstained = [(r, s, label) for r, s, label in scored if s < t]
        n_abstained = len(abstained)
        n_abstained_wrong = sum(1 for _, _, label in abstained if label == 0)
        n_abstained_right = sum(1 for _, _, label in abstained if label == 1)
        precision_wrong = n_abstained_wrong / n_abstained if n_abstained else None
        sacrifice_rate = n_abstained_right / n_right if n_right else None
        rows.append({
            "threshold": t,
            "n_abstained": n_abstained,
            "precision_abstained_wrong": round(precision_wrong, 4) if precision_wrong is not None else None,
            "sacrifice_rate_right": round(sacrifice_rate, 4) if sacrifice_rate is not None else None,
            "meets_criteria": bool(
                precision_wrong is not None and precision_wrong >= 0.70
                and sacrifice_rate is not None and sacrifice_rate <= 0.05
            ),
        })
    return rows, n_right, n_wrong


def main() -> int:
    limit = None
    args = sys.argv[1:]
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])

    rows = build_calibration_set(limit)
    print(f"total calibration rows on disk: {len(rows)}")

    questions = sorted({(r["conversation_id"], r["question"]) for r in rows})
    rng = random.Random(SEED)
    rng.shuffle(questions)
    n_val = max(1, int(len(questions) * VAL_FRACTION))
    val_qs = set(questions[:n_val])
    train_rows = [r for r in rows if (r["conversation_id"], r["question"]) not in val_qs]
    val_rows = [r for r in rows if (r["conversation_id"], r["question"]) in val_qs]
    print(f"train questions: {len(train_rows)}, val questions: {len(val_rows)}")

    X = np.array([evidence_vector(r["features"]) for r in train_rows])
    y = np.array([float(r["answerable"]) for r in train_rows])
    print(f"answerable rate (train): {y.mean():.4f}")

    weights = train_logistic(X, y, epochs=500)
    save_weights(weights, WEIGHTS_PATH)
    print(f"wrote {WEIGHTS_PATH}")

    sweep, n_right, n_wrong = sweep_thresholds(weights, val_rows)
    print(f"val split: {len(val_rows)} questions, {n_right} would-have-been-right, {n_wrong} would-have-been-wrong")
    print(f"{'threshold':>10} {'n_abstained':>12} {'precision(wrong)':>18} {'sacrifice(right)':>18} {'meets':>6}")
    for row in sweep:
        print(f"{row['threshold']:>10} {row['n_abstained']:>12} "
              f"{str(row['precision_abstained_wrong']):>18} {str(row['sacrifice_rate_right']):>18} "
              f"{str(row['meets_criteria']):>6}")

    meeting = [r for r in sweep if r["meets_criteria"]]
    chosen = max(meeting, key=lambda r: r["n_abstained"]) if meeting else None
    summary = {
        "feature_names": list(FEATURE_NAMES),
        "n_train_questions": len(train_rows),
        "n_val_questions": len(val_rows),
        "n_val_right": n_right,
        "n_val_wrong": n_wrong,
        "sweep": sweep,
        "chosen_threshold": chosen["threshold"] if chosen else None,
        "criteria_met": chosen is not None,
    }
    (ROOT / "results" / "abstain-threshold-sweep-2026-07-20.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
