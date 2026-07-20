"""Belief-revision ripple: 3 real cases from the live store.

Read-only against ~/.memsub/memory.db and ~/memsub-vault (the real store and
vault this machine already has) -- writes nothing, appends nothing. Prints a
human-readable ripple report for each case via memsub.ripple.render_report.

The 3 cases were found by querying the live store's audit trail directly
(memory_events where event='superseded'/'fused_into'), not invented -- see
WORKLOG.md 2026-07-20 "Belief-revision ripple engine" assumption 5 for how
each id was located.

Run: python3 scripts/ripple_demo.py
"""

from __future__ import annotations

from pathlib import Path

from memsub.ripple import render_report, ripple
from memsub.store import EpisodicStore

STORE_PATH = Path.home() / ".memsub" / "memory.db"

CASES = [
    (
        "Case 1: a human correction (the OpenClaw-attribution fix)",
        "The user corrected a fact that implied he built OpenClaw as his own "
        "project ('...using LangChain, OpenClaw, OpenAI...'); the corrected "
        "version drops OpenClaw from his own ML experience. This is the "
        "cleanest belief-revision example in the store: one explicit "
        "human_correction event, not an automatic dedup.",
        "0054af9952a9485babb90841457caede",
    ),
    (
        "Case 2: a duplicate-collapse chain (same OpenClaw-adjacent wording)",
        "A near-identical 'builds applied LLM and agent systems ... using "
        "LangChain, OpenClaw, and the OpenAI/Claude APIs' sentence was "
        "recorded 7 times across sessions and consolidation collapsed them "
        "one hop at a time. Ripple on the first loser shows the chain "
        "reaching 2 hops deep at max_hops=2.",
        "bf35552fec52443ab3bbeb9e77d48561",
    ),
    (
        "Case 3: a fusion source with a rich associative neighborhood",
        "An ILS-optimization fact ('regret_ils_v1 ... regret-2 perturbation') "
        "was folded into a fusion note summarizing several ILS strategy "
        "candidates, and separately shares entities with 10 other memories "
        "about the same optimization work -- the richest of the 3 cases, "
        "touching all three tiers.",
        "cf4855a8f9ab4c9c93b4b5f8000e134e",
    ),
]


def main() -> None:
    if not STORE_PATH.exists():
        print(f"ABORT: no live store at {STORE_PATH}")
        return
    store = EpisodicStore(STORE_PATH)
    try:
        for title, blurb, memory_id in CASES:
            print("=" * 78)
            print(title)
            print(blurb)
            print("-" * 78)
            report = ripple(store, memory_id, max_hops=2)
            print(render_report(report))
            print()
    finally:
        store.close()


if __name__ == "__main__":
    main()
