"""scripts/triage_vault.py: heuristic review-queue triage over the store.

Covers the three flagged categories and the cap, using a small synthetic
store so this stays fast and independent of the real vault/corpus.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memsub.store import Episode, EpisodicStore  # noqa: E402
from scripts.triage_vault import build_review_queue, render_review_queue  # noqa: E402


def _store_with_corrected_reassertion() -> EpisodicStore:
    store = EpisodicStore(":memory:")
    old = store.append(
        Episode(
            content="Rustom has experience with PyTorch, TensorFlow, LangChain, "
            "OpenClaw, OpenAI, and Claude APIs.",
            run_id="r1",
            session_id="s1",
        )
    )
    new = store.append(
        Episode(
            content="Rustom has experience with PyTorch, TensorFlow, LangChain, "
            "OpenAI, and Claude APIs.",
            run_id="r1",
            session_id="s1",
        )
    )
    store.log_event(old.id, "superseded", {"reason": "human_correction", "by": new.id})
    store.append(
        Episode(
            content="Rustom builds applied LLM systems using LangChain, OpenClaw, "
            "and the OpenAI and Claude APIs.",
            run_id="r2",
            session_id="s2",
        )
    )
    return store


def test_flags_reassertion_of_a_human_corrected_claim():
    store = _store_with_corrected_reassertion()
    queue = build_review_queue(store)
    assert queue.counts_found["corrected_reassertion"] == 1
    entry = next(e for e in queue.entries if e.category == "corrected_reassertion")
    assert "restates" in entry.reason
    assert entry.wikilink  # points at the reasserting note, not the old/new pair


def test_ordinary_dissimilar_facts_are_not_flagged():
    store = EpisodicStore(":memory:")
    store.append(Episode(content="The repo uses uv for dependency management.", run_id="r", session_id="s"))
    store.append(Episode(content="Tests run in under ten seconds locally.", run_id="r", session_id="s"))
    queue = build_review_queue(store)
    assert queue.counts_found == {"corrected_reassertion": 0, "stale_transient": 0, "near_duplicate": 0}


def test_flags_transient_system_state_as_stale():
    store = EpisodicStore(":memory:")
    store.append(
        Episode(content="An orphaned python3 process pid 12345 is currently running.", run_id="r", session_id="s")
    )
    queue = build_review_queue(store)
    assert queue.counts_found["stale_transient"] == 1


def test_flags_near_duplicate_pair_once():
    store = EpisodicStore(":memory:")
    store.append(
        Episode(content="The user disabled the openclaw.service to stop it restarting the agents server.", run_id="r", session_id="s")
    )
    store.append(
        Episode(content="The user disabled the openclaw.service to stop it restarting the agents server and burning credits.", run_id="r", session_id="s")
    )
    queue = build_review_queue(store)
    assert queue.counts_found["near_duplicate"] == 1
    entries = [e for e in queue.entries if e.category == "near_duplicate"]
    assert len(entries) == 1  # one entry per pair, not one per note


def test_render_respects_cap_and_reports_total_found():
    store = EpisodicStore(":memory:")
    for i in range(5):
        store.append(
            Episode(content=f"An orphaned python3 process pid {90000 + i * 7919} is running today.", run_id="r", session_id="s")
        )
    queue = build_review_queue(store)
    assert queue.counts_found["stale_transient"] == 5
    total = len(queue.entries)
    text = render_review_queue(queue, cap=2)
    assert f"{total} found in total" in text
    assert f"2 of {total} shown" in text


def test_no_verdicts_are_ever_applied_only_suggested():
    store = _store_with_corrected_reassertion()
    queue = build_review_queue(store)
    text = render_review_queue(queue)
    assert "verdict: confirm" not in text and "verdict: reject" not in text
    assert "Suggested:" in text
