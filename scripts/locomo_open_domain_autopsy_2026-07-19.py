"""Item 4 (Wave 2, docs/improvement-plan-2026-07-18.md): open-domain autopsy.

Read-only reconstruction of the packed context for each open-domain
question that the v3/b2000 run (results/locomo-jscore-v3-2026-07-18-b2000.
jsonl) already scored wrong, so the question/gold/context/model-answer can
be read together for manual failure-mode classification. Makes no
answer/judge calls (the recorded answer and verdict are reused as-is from
the already-published file) -- this script only reruns retrieval
(_build_system/_context_for from the frontier script) to recover the
packed context, which the original run did not persist.

Same condition/budget/dates as the run being autopsied: graph_decompose,
budget 2000, with_dates=True (v3 config). Caveat: the decomposition
sub-query LLM call is not temperature-seeded, so the exact 1-2 sub-queries
picked here may differ slightly from the original run's; the base
(non-decomposed) retrieval ranking is deterministic. See WORKLOG.md
2026-07-19 entry.

Streams one JSONL record per failing question to
results/locomo-open-domain-autopsy-2026-07-19.jsonl, flushed immediately.
Resumable: a conversation_id+question already present is skipped.

Usage: .venv/bin/python scripts/locomo_open_domain_autopsy_2026-07-19.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.holdout_answer_quality import holdout_convs, sample  # noqa: E402
from memsub.backends import OllamaBackend  # noqa: E402

# scripts/locomo_jscore_frontier_2026-07-18.py has a dash-containing module
# name (not a valid dotted import path) -- load it by file path instead.
_spec = importlib.util.spec_from_file_location(
    "locomo_jscore_frontier", ROOT / "scripts" / "locomo_jscore_frontier_2026-07-18.py"
)
_frontier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_frontier)

INPUT_PATH = ROOT / "results" / "locomo-jscore-v3-2026-07-18-b2000.jsonl"
OUTPUT_PATH = ROOT / "results" / "locomo-open-domain-autopsy-2026-07-19.jsonl"
CONDITION = "graph_decompose"
BUDGET = 2000
WITH_DATES = True


def _load_wrong_open_domain() -> list[dict]:
    out = []
    for line in INPUT_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if (
            rec.get("record_type") == "question"
            and rec.get("category") == 3
            and rec.get("correct") is False
        ):
            out.append(rec)
    return out


def _load_existing_keys() -> set[tuple]:
    if not OUTPUT_PATH.exists():
        return set()
    keys = set()
    for line in OUTPUT_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        keys.add((rec["conversation_id"], rec["question"]))
    return keys


def main() -> int:
    wrong = _load_wrong_open_domain()
    print(f"{len(wrong)} wrong open-domain records in {INPUT_PATH.name}")
    by_key = {(r["conversation_id"], r["question"]): r for r in wrong}

    convs = holdout_convs()
    picked = sample(convs)

    decompose_backend = OllamaBackend(model="qwen3:8b")
    existing = _load_existing_keys()
    systems: dict[int, tuple] = {}

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("a", encoding="utf-8") as fh:
        for ci, qi in picked:
            conv = convs[ci]
            q = conv.qa[qi]
            if q.category != 3:
                continue
            key = (conv.conversation_id, q.question)
            if key not in by_key:
                continue
            if key in existing:
                continue

            if ci not in systems:
                systems[ci] = _frontier._build_system(CONDITION, conv)
            system, store = systems[ci]

            ctx = _frontier._context_for(
                CONDITION, system, store, q.question, decompose_backend,
                with_dates=WITH_DATES, budget=BUDGET,
            )

            orig = by_key[key]
            record = {
                "conversation_id": conv.conversation_id,
                "question": q.question,
                "gold": orig["gold"],
                "model_answer": orig["answer"],
                "judge_correct": orig["correct"],
                "packed_context": ctx,
                "packed_context_chars": len(ctx),
            }
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            print(f"  wrote {conv.conversation_id}: {q.question[:60]!r}")

        for system, store in systems.values():
            if store is not None:
                store.close()

    print(f"\ndone, wrote up to {len(wrong)} records to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
