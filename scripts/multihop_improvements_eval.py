"""Measure Part B items 1 and 2 of docs/improvement-plan-2026-07-17.md against the
LOCOMO dev slice (conversations 1-3, the same split error_analysis.py and dev_eval.py
use, and the source of the 94.6% multi-hop failure figure this task targets).

Three conditions, same evidence-all / evidence-any metric family as
docs/error-analysis-locomo.md and docs/benchmark.md's dev-eval log:

  baseline          hybrid v2 (memsub.bench.HybridSystem, TUNED params), the shipped
                     default retrieval, unchanged.
  fusion            same retriever, but ingested episodes are first passed through
                     memsub.consolidate.consolidate() with the new fusion phase (write
                     time, once per conversation, not once per question). Contradiction
                     and decay are disabled in this eval's ConsolidationConfig to
                     isolate fusion's effect from those existing, unrelated phases (see
                     WORKLOG.md assumption 7).
  fusion_decompose  same fused episode set, retrieval via HybridRetriever.rank_decomposed
                     (one bounded LLM call per question).

Streams one JSONL record per (condition, conversation, question) to
results/multihop-improvements-2026-07-17.json as it runs (flushed immediately, durable
per repo convention), plus one summary record per condition. Resumable: records already
present in the output file are skipped on restart.

Requires a reachable ollama daemon with qwen3:8b pulled (memsub.backends.OllamaBackend);
uses think=False (see memsub/backends.py) to keep this run inside a reasonable time
budget on this machine (~35s/call with thinking vs ~1.3s without).
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memsub.backends import OllamaBackend, ollama_available  # noqa: E402
from memsub.bench import HybridSystem, load_locomo, pack_greedy  # noqa: E402
from memsub.consolidate import ConsolidationConfig, consolidate  # noqa: E402
from memsub.store import EpisodicStore  # noqa: E402

TUNED = {"recency_weight": 0.999, "recency_floor": 0.75, "importance_floor": 0.0}
# HybridSystem (memsub/bench.py) keeps a "benchmark-era constructor, all boosts off by
# default" for historical-eval reproducibility; it does NOT reproduce the shipped hybrid
# v2 defaults on its own (that's memsub.hybrid.HybridRetriever's job). These are the
# frozen-v2 boost params from docs/benchmark.md ("Frozen stack after dev selection"),
# passed explicitly so `HYBRID_V2_KWARGS` below reproduces the actual shipped default
# retrieval (verified against docs/benchmark.md's dev totals: 72.8% / 85.1% at budget
# 1000, multi-hop 20.3% / 77.0%, before trusting any fusion/decomposition delta from it).
HYBRID_V2_KWARGS = dict(
    dense_weight=0.5, bm25_weight=1.0, temporal=True, speaker_boost=1.0, session_weight=3.0
)
BUDGET = 1000
CATEGORY_NAMES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
RESULTS_PATH = ROOT / "results" / "multihop-improvements-2026-07-17.json"
CONDITIONS = ["baseline", "fusion", "fusion_decompose"]
# hard wall-clock safety cap for this script (see docs/error-analysis-locomo.md
# instructions: fall back to a smoke-test subset if a full run would exceed ~2h)
MAX_WALL_SECONDS = 100 * 60


def _fuse_episodes(episodes, backend):
    """Route episodes through consolidate()'s new fusion phase (write time, once
    per conversation). Contradiction and decay are disabled here so this
    condition isolates fusion's effect (see WORKLOG.md assumption 7): LOCOMO
    turns all share kind="turn", so the existing contradiction pass would
    otherwise fire on nearly every close-similarity pair, which is a
    pre-existing, unrelated phase this eval is not measuring."""
    store = EpisodicStore(":memory:")
    for ep in episodes:
        store.append(ep)
    cfg = ConsolidationConfig(contradiction_min_sim=1.1, decay_value_threshold=0.0)
    consolidate(store, backend=backend, config=cfg)
    fused = store.active()
    store.close()
    return fused


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


def main() -> int:
    if not ollama_available():
        print("ollama unreachable; aborting. See WORKLOG.md for the fallback note.")
        return 1

    backend = OllamaBackend(model="qwen3:8b", think=False)
    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]  # DEV ONLY, matches error_analysis.py
    existing = _load_existing_keys()
    t0 = time.time()
    truncated = False

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as fh:
        for condition in CONDITIONS:
            acc: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])  # cat -> n, all, any
            for conv in convs:
                if condition == "baseline":
                    fused_or_raw = list(conv.episodes)
                else:
                    fused_or_raw = _fuse_episodes(conv.episodes, backend)

                system = HybridSystem(policy_params=TUNED, **HYBRID_V2_KWARGS)
                system.ingest(fused_or_raw)

                for q in conv.qa:
                    if q.category not in CATEGORY_NAMES or not q.evidence:
                        continue
                    key = (condition, conv.conversation_id, q.question)
                    if key in existing:
                        continue
                    if time.time() - t0 > MAX_WALL_SECONDS:
                        truncated = True
                        break

                    if condition == "fusion_decompose":
                        order = system.rank_decomposed(q.question, backend, max_subqueries=2)
                        packed = pack_greedy([system.episodes[i] for i in order], BUDGET)
                    else:
                        packed = system.inject(q.question, BUDGET)

                    packed_ids = {ep.id for ep in packed}
                    ev_ids = {f"{conv.conversation_id}:{e}" for e in q.evidence}
                    hits = len(ev_ids & packed_ids)
                    all_hit = int(hits == len(ev_ids))
                    any_hit = int(hits > 0)

                    _write_record(fh, {
                        "record_type": "question",
                        "condition": condition,
                        "conversation_id": conv.conversation_id,
                        "question": q.question,
                        "category": q.category,
                        "category_name": CATEGORY_NAMES[q.category],
                        "budget": BUDGET,
                        "n_evidence": len(ev_ids),
                        "hits": hits,
                        "evidence_all_hit": bool(all_hit),
                        "evidence_any_hit": bool(any_hit),
                    })
                    cell = acc[q.category]
                    cell[0] += 1
                    cell[1] += all_hit
                    cell[2] += any_hit
                    print(f"  [{condition}] {conv.conversation_id} cat={q.category} "
                          f"all={all_hit} any={any_hit} ({time.time() - t0:.0f}s)")
                if truncated:
                    break

            totals = [0, 0, 0]
            per_cat = {}
            for cat in sorted(CATEGORY_NAMES):
                n, a, y = acc[cat]
                totals = [totals[0] + n, totals[1] + a, totals[2] + y]
                per_cat[CATEGORY_NAMES[cat]] = {
                    "n": n,
                    "evidence_all_pct": round(100 * a / n, 1) if n else None,
                    "evidence_any_pct": round(100 * y / n, 1) if n else None,
                }
            _write_record(fh, {
                "record_type": "summary",
                "condition": condition,
                "budget": BUDGET,
                "truncated": truncated,
                "per_category": per_cat,
                "total": {
                    "n": totals[0],
                    "evidence_all_pct": round(100 * totals[1] / totals[0], 1) if totals[0] else None,
                    "evidence_any_pct": round(100 * totals[2] / totals[0], 1) if totals[0] else None,
                },
            })
            print(f"== {condition} done: n={totals[0]} "
                  f"all={100*totals[1]/totals[0]:.1f}% any={100*totals[2]/totals[0]:.1f}% "
                  f"({time.time()-t0:.0f}s elapsed) ==")
            if truncated:
                print("WALL-CLOCK CAP HIT: remaining questions/conditions skipped, see WORKLOG.md")
                break

    print(f"\nwrote {RESULTS_PATH}, wall {time.time() - t0:.1f}s, truncated={truncated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
