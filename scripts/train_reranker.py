"""Task 2 (2026-07-20): train the tiny learned reranker.

Parses the (conversation_id, question) pairs already evaluated in existing
results/ JSONL files, restricted to LOCOMO DEV conversations only
(conv-26/conv-30/conv-41 -- data/locomo10.json indices 0-2; several
results/ files below were themselves run on HOLDOUT conversations and are
deliberately excluded here so Task 3's holdout judged comparison stays
uncontaminated, see WORKLOG.md 2026-07-20 assumption 4):

  results/graph-layer-eval-2026-07-18.jsonl
  results/multihop-answer-quality-2026-07-17.jsonl
  results/temporal-dev-eval-2026-07-20.jsonl

For each unique dev (conversation_id, question) pair, re-runs retrieval
(HybridSystem FROZEN params + graph_store(backend=None), both fully
deterministic, zero LLM calls) to get HybridRetriever.candidate_features()
for the top POOL_SIZE candidates, and labels each candidate gold-supporting
(1) or not (0) using LOCOMO's own `evidence` dia_ids (exact ground truth,
not a text-overlap proxy) -- an episode's id is f"{conversation_id}:{dia_id}"
(memsub.bench.load_locomo), so membership in the evidence set is a direct
lookup, no heuristic involved. Category 5 (adversarial, no evidence) and
any question with empty evidence are skipped, mirroring the
skip_categories=(5,) convention memsub.bench.run_locomo_eval already uses.

Streams one row per (question, candidate) to
results/reranker-train-2026-07-20.jsonl (durable, resumable -- a question
already fully written is skipped on restart). Splits by QUESTION (not by
candidate row, to avoid leaking a question's candidates across train/val)
into train/val, seed 2026-fixed. Trains memsub.rerank.train_logistic on the
train split, saves results/reranker-weights-2026-07-20.json, and reports a
dev-slice eval on the val split: Recall@5 / Recall@10 / MRR of gold-
supporting episodes, baseline (shared base_order/pool, unreordered) vs
reranked (the SAME pool, reordered by the trained model) -- the
base_order-style shared-candidates fairness pattern from
docs/temporal-v1-2026-07-20.md.

Usage: .venv/bin/python scripts/train_reranker.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from memsub.bench import HybridSystem, load_locomo  # noqa: E402
from memsub.graph import graph_store  # noqa: E402
from memsub.rerank import RERANK_FEATURE_NAMES, rank_by_model, rerank_feature_vector, save_weights, train_logistic  # noqa: E402
from memsub.store import EpisodicStore  # noqa: E402
from scripts.metrics_local import FROZEN  # noqa: E402

DEV_CONV_IDS = {"conv-26", "conv-30", "conv-41"}
SOURCE_FILES = [
    "results/graph-layer-eval-2026-07-18.jsonl",
    "results/multihop-answer-quality-2026-07-17.jsonl",
    "results/temporal-dev-eval-2026-07-20.jsonl",
]
TRAIN_ROWS_PATH = ROOT / "results" / "reranker-train-2026-07-20.jsonl"
WEIGHTS_PATH = ROOT / "results" / "reranker-weights-2026-07-20.json"
EVAL_PATH = ROOT / "results" / "reranker-eval-2026-07-20.json"
POOL_SIZE = 50
VAL_FRACTION = 0.2
SEED = 2026


def collect_dev_pairs() -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Unique (conversation_id, question) pairs found in SOURCE_FILES,
    restricted to DEV_CONV_IDS. Returns (pairs, per_file_dev_row_counts) so
    the exact provenance can be logged/reported."""
    pairs: set[tuple[str, str]] = set()
    per_file: dict[str, int] = {}
    for rel in SOURCE_FILES:
        path = ROOT / rel
        n = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict):
                continue
            cid, q = d.get("conversation_id"), d.get("question")
            if cid in DEV_CONV_IDS and q:
                pairs.add((cid, q))
                n += 1
        per_file[rel] = n
    return sorted(pairs), per_file


def _load_existing_questions() -> set[tuple[str, str]]:
    if not TRAIN_ROWS_PATH.exists():
        return set()
    done = set()
    for line in TRAIN_ROWS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        done.add((rec["conversation_id"], rec["question"]))
    return done


def build_training_rows(pairs: list[tuple[str, str]]) -> dict:
    """Streams candidate-level rows to TRAIN_ROWS_PATH, resumable. Returns a
    summary dict (n_questions_processed, n_questions_skipped_no_evidence,
    n_questions_not_found_in_locomo, n_candidate_rows)."""
    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]
    by_conv_id = {c.conversation_id: c for c in convs}

    done_pairs = _load_existing_questions()

    systems: dict[str, tuple] = {}
    summary = {
        "n_questions_total": len(pairs),
        "n_questions_processed": 0,
        "n_questions_already_done": 0,
        "n_questions_skipped_no_evidence": 0,
        "n_questions_not_found_in_locomo": 0,
        "n_candidate_rows": 0,
    }

    TRAIN_ROWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRAIN_ROWS_PATH.open("a", encoding="utf-8") as fh:
        for cid, question in pairs:
            if (cid, question) in done_pairs:
                summary["n_questions_already_done"] += 1
                continue
            conv = by_conv_id.get(cid)
            if conv is None:
                summary["n_questions_not_found_in_locomo"] += 1
                continue
            qa = next((q for q in conv.qa if q.question == question), None)
            if qa is None or not qa.evidence:
                summary["n_questions_skipped_no_evidence"] += 1
                continue

            if cid not in systems:
                system = HybridSystem(**FROZEN)
                store = EpisodicStore(":memory:")
                written = [store.append(ep) for ep in conv.episodes]
                graph_store(store)  # backend=None: zero LLM calls, deterministic
                system.ingest(written)
                systems[cid] = (system, store)
            system, store = systems[cid]

            gold_ids = {f"{cid}:{dia}" for dia in qa.evidence}
            feats = system.candidate_features(question, store=store, pool_size=POOL_SIZE)
            for f in feats:
                ep = system.episodes[f["index"]]
                row = {
                    "conversation_id": cid,
                    "question": question,
                    "category": qa.category,
                    "episode_id": ep.id,
                    "label": 1 if ep.id in gold_ids else 0,
                    "features": {name: f[name] for name in RERANK_FEATURE_NAMES},
                }
                fh.write(json.dumps(row) + "\n")
                summary["n_candidate_rows"] += 1
            fh.flush()
            summary["n_questions_processed"] += 1

    for _, store in systems.values():
        store.close()
    return summary


def _load_all_rows() -> list[dict]:
    rows = []
    for line in TRAIN_ROWS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def train_val_split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    questions = sorted({(r["conversation_id"], r["question"]) for r in rows})
    rng = random.Random(SEED)
    rng.shuffle(questions)
    n_val = max(1, int(len(questions) * VAL_FRACTION))
    val_qs = set(questions[:n_val])
    train_rows = [r for r in rows if (r["conversation_id"], r["question"]) not in val_qs]
    val_rows = [r for r in rows if (r["conversation_id"], r["question"]) in val_qs]
    return train_rows, val_rows


def _rank_metrics(order: list[str], gold_ids: set[str], episode_ids: list[str]) -> dict:
    """order: episode indices best-first (into episode_ids). Recall@5/@10 and
    reciprocal rank of the first gold-supporting hit."""
    ranked_ids = [episode_ids[i] for i in order]
    hit_positions = [pos for pos, eid in enumerate(ranked_ids) if eid in gold_ids]
    rr = 1.0 / (hit_positions[0] + 1) if hit_positions else 0.0
    return {
        "recall_at_5": float(any(p < 5 for p in hit_positions)),
        "recall_at_10": float(any(p < 10 for p in hit_positions)),
        "mrr": rr,
    }


def evaluate_val_split(val_rows: list[dict], weights: dict) -> dict:
    """Re-runs retrieval per val question to get the SAME base pool (shared
    candidates, base_order-style fairness) for both the baseline (pool
    order unchanged) and reranked (same pool, reordered by the trained
    model) conditions."""
    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]
    by_conv_id = {c.conversation_id: c for c in convs}
    by_question: dict[tuple[str, str], list[dict]] = {}
    for r in val_rows:
        by_question.setdefault((r["conversation_id"], r["question"]), []).append(r)

    systems: dict[str, tuple] = {}
    baseline_metrics, reranked_metrics = [], []
    for (cid, question), rows in by_question.items():
        conv = by_conv_id[cid]
        qa = next(q for q in conv.qa if q.question == question)
        gold_ids = {f"{cid}:{dia}" for dia in qa.evidence}
        if cid not in systems:
            system = HybridSystem(**FROZEN)
            store = EpisodicStore(":memory:")
            written = [store.append(ep) for ep in conv.episodes]
            graph_store(store)
            system.ingest(written)
            systems[cid] = (system, store)
        system, store = systems[cid]

        feats = system.candidate_features(question, store=store, pool_size=POOL_SIZE)
        if not feats:
            continue
        episode_ids = [ep.id for ep in system.episodes]
        baseline_order = [f["index"] for f in feats]  # shared base pool, unreordered
        reranked_order = rank_by_model(weights, feats)  # SAME pool, reordered

        baseline_metrics.append(_rank_metrics(baseline_order, gold_ids, episode_ids))
        reranked_metrics.append(_rank_metrics(reranked_order, gold_ids, episode_ids))

    for _, store in systems.values():
        store.close()

    def _mean(metrics: list[dict], key: str) -> float:
        return round(sum(m[key] for m in metrics) / len(metrics), 4) if metrics else 0.0

    return {
        "n_val_questions": len(baseline_metrics),
        "baseline": {k: _mean(baseline_metrics, k) for k in ("recall_at_5", "recall_at_10", "mrr")},
        "reranked": {k: _mean(reranked_metrics, k) for k in ("recall_at_5", "recall_at_10", "mrr")},
    }


def main() -> int:
    pairs, per_file = collect_dev_pairs()
    print(f"dev (conv,question) pairs found: {len(pairs)}")
    for f, n in per_file.items():
        print(f"  {f}: {n} dev-conv rows")

    summary = build_training_rows(pairs)
    print("build summary:", json.dumps(summary, indent=2))

    rows = _load_all_rows()
    train_rows, val_rows = train_val_split(rows)
    print(f"train candidate rows: {len(train_rows)}, val candidate rows: {len(val_rows)}")

    X = np.array([rerank_feature_vector(r["features"]) for r in train_rows])
    y = np.array([float(r["label"]) for r in train_rows])
    print(f"positive rate (train): {y.mean():.4f}")

    weights = train_logistic(X, y, epochs=500)
    save_weights(weights, WEIGHTS_PATH)
    print(f"wrote {WEIGHTS_PATH}")

    eval_result = evaluate_val_split(val_rows, weights)
    eval_result["source_files"] = per_file
    eval_result["dev_conv_ids"] = sorted(DEV_CONV_IDS)
    eval_result["pool_size"] = POOL_SIZE
    eval_result["n_train_candidate_rows"] = len(train_rows)
    eval_result["n_val_candidate_rows"] = len(val_rows)
    eval_result["train_positive_rate"] = round(float(y.mean()), 4)
    EVAL_PATH.write_text(json.dumps(eval_result, indent=2))
    print("dev-slice eval (val split, shared base-order pool):")
    print(json.dumps(eval_result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
