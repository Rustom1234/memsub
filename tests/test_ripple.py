"""Tests for memsub/ripple.py: belief-revision ripple (impact analysis for
a memory). See WORKLOG.md 2026-07-20 "Belief-revision ripple engine" for
the design and the four-tier edge model this exercises.

Every test passes an explicit vault_path (a tmp directory, usually one that
does not even exist) so no test depends on or touches the real
~/memsub-vault -- ripple.DEFAULT_VAULT_PATH is only exercised by the demo
script and the one test below that checks the fallback does not error.
"""

from __future__ import annotations

import json

from memsub.distill import distill
from memsub.graph import graph_store
from memsub.ripple import (
    TIER_ASSOCIATIVE,
    TIER_DIRECT,
    TIER_TRANSITIVE,
    ripple,
    ripple_on_event,
)
from memsub.store import Episode, EpisodicStore


def _ep(content, ts, id, run_id="r", session="s1", kind="turn"):
    return Episode(content=content, run_id=run_id, session_id=session, kind=kind, ts=ts, id=id)


def _empty_vault(tmp_path):
    return tmp_path / "no-such-vault"


# --------------------------------------------------------------------------- #
# Tier (a): fusion
# --------------------------------------------------------------------------- #
def test_fused_edge_lands_in_derived_directly(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("Alice: I love hiking.", 1.0, "src1"))
    store.append(_ep("Alice enjoys hiking every weekend, per fused notes.", 2.0, "fused1", kind="fusion"))
    store.log_event("src1", "fused_into", {"by": "fused1", "entity": "alice"})

    report = ripple(store, "src1", vault_path=_empty_vault(tmp_path))
    ids = {it.id for it in report.tiers[TIER_DIRECT]}
    assert "fused1" in ids
    item = next(it for it in report.tiers[TIER_DIRECT] if it.id == "fused1")
    assert item.edge_type == "fused_from"
    assert item.type == "fused"
    assert "fused entry" in item.path[0]
    store.close()


# --------------------------------------------------------------------------- #
# Tier (b): topic membership (real distill.py output, format fidelity)
# --------------------------------------------------------------------------- #
def test_topic_edge_lands_in_derived_directly(tmp_path):
    # Real episode ids are uuid4().hex (32 hex chars); note_wikilink cites a
    # member by its first 8 hex chars, so the ids here must look like real
    # ones (unlike the short "m1"/"m2" ids used elsewhere in this file) for
    # the topic-note wikilink scan to have anything real to match against.
    store = EpisodicStore(tmp_path / "mem.db")
    id1, id2, id3 = "aaaaaaaa" + "1" * 24, "bbbbbbbb" + "2" * 24, "cccccccc" + "3" * 24
    store.append(_ep("Melanie: I love pottery classes on Tuesdays.", 1.0, id1))
    store.append(_ep("Caroline: Melanie says pottery relaxes her.", 2.0, id2))
    store.append(_ep("Melanie signed up for another pottery workshop.", 3.0, id3))
    graph_store(store, backend=None)
    vault = tmp_path / "vault"
    result = distill(store, vault, backend=None)
    assert result.topics_written == 1
    slug = result.slugs[0]

    report = ripple(store, id1, vault_path=vault)
    ids = {it.id for it in report.tiers[TIER_DIRECT]}
    assert f"topic:{slug}" in ids
    item = next(it for it in report.tiers[TIER_DIRECT] if it.id == f"topic:{slug}")
    assert item.edge_type == "topic_member"
    assert item.type == "topic"
    assert "topic note" in item.path[0]
    store.close()


def test_topic_tier_empty_when_vault_missing(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("Solo memory with no topic note.", 1.0, "m1"))
    report = ripple(store, "m1", vault_path=_empty_vault(tmp_path))
    assert report.counts["total"] == 0
    store.close()


# --------------------------------------------------------------------------- #
# Tier (c): supersede chains, both directions
# --------------------------------------------------------------------------- #
def test_supersede_forward_direction(tmp_path):
    """B beat A: ripple(B) should surface A as something B supersedes."""
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("Old fact.", 1.0, "a"))
    store.append(_ep("Corrected fact.", 2.0, "b"))
    store.log_event("a", "superseded", {"by": "b", "reason": "human_correction"})

    report = ripple(store, "b", vault_path=_empty_vault(tmp_path))
    item = next(it for it in report.tiers[TIER_DIRECT] if it.id == "a")
    assert item.edge_type == "supersedes"
    assert "human_correction" in item.path[0]
    store.close()


def test_supersede_backward_direction(tmp_path):
    """A lost to B: ripple(A) should surface B as what superseded it."""
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("Old fact.", 1.0, "a"))
    store.append(_ep("Corrected fact.", 2.0, "b"))
    store.log_event("a", "superseded", {"by": "b", "reason": "human_correction"})

    report = ripple(store, "a", vault_path=_empty_vault(tmp_path))
    item = next(it for it in report.tiers[TIER_DIRECT] if it.id == "b")
    assert item.edge_type == "superseded_by"
    store.close()


# --------------------------------------------------------------------------- #
# Tier (d): associative, weighted by co-occurrence, root-only
# --------------------------------------------------------------------------- #
def test_associative_tier_weighted_by_cooccurrence(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    # m1/m2 share both "Melanie" and "pottery"; m3 shares only "Melanie".
    store.append(_ep("Melanie: pottery class was fun today.", 1.0, "m1"))
    store.append(_ep("Melanie: pottery relaxes me a lot.", 2.0, "m2"))
    store.append(_ep("Melanie called about something unrelated.", 3.0, "m3"))
    graph_store(store, backend=None)

    report = ripple(store, "m1", vault_path=_empty_vault(tmp_path))
    assoc_ids = {it.id: it for it in report.tiers[TIER_ASSOCIATIVE]}
    assert "m2" in assoc_ids or "m3" in assoc_ids
    for item in report.tiers[TIER_ASSOCIATIVE]:
        assert item.co_occurrence is not None
        assert item.co_occurrence >= 1
        assert item.tier == TIER_ASSOCIATIVE
    # higher shared-entity count outranks lower, by impact_score
    scores = [it.impact_score for it in report.tiers[TIER_ASSOCIATIVE]]
    assert scores == sorted(scores, reverse=True)
    store.close()


def test_associative_tier_never_duplicates_a_direct_hit(tmp_path):
    """A memory reachable via fusion also shares entities with the root --
    it must appear once, in the stronger tier, not twice."""
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("Melanie: pottery class today.", 1.0, "m1"))
    store.append(_ep("Melanie loves pottery, fused summary.", 2.0, "fused1", kind="fusion"))
    store.log_event("m1", "fused_into", {"by": "fused1", "entity": "melanie"})
    graph_store(store, backend=None)  # only indexes active episodes; fused1 has no entities anyway

    report = ripple(store, "m1", vault_path=_empty_vault(tmp_path))
    all_ids = [it.id for tier in report.tiers.values() for it in tier]
    assert all_ids.count("fused1") == 1
    store.close()


# --------------------------------------------------------------------------- #
# Hop bounding
# --------------------------------------------------------------------------- #
def test_hop_bounding_max_hops_1_stops_at_direct(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("A", 1.0, "a"))
    store.append(_ep("B fused from A", 2.0, "b", kind="fusion"))
    store.append(_ep("C fused from B", 3.0, "c", kind="fusion"))
    store.log_event("a", "fused_into", {"by": "b", "entity": "x"})
    store.log_event("b", "fused_into", {"by": "c", "entity": "x"})

    report = ripple(store, "a", max_hops=1, vault_path=_empty_vault(tmp_path))
    assert {it.id for it in report.tiers[TIER_DIRECT]} == {"b"}
    assert report.tiers[TIER_TRANSITIVE] == []
    store.close()


def test_hop_bounding_max_hops_2_reaches_transitive(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("A", 1.0, "a"))
    store.append(_ep("B fused from A", 2.0, "b", kind="fusion"))
    store.append(_ep("C fused from B", 3.0, "c", kind="fusion"))
    store.log_event("a", "fused_into", {"by": "b", "entity": "x"})
    store.log_event("b", "fused_into", {"by": "c", "entity": "x"})

    report = ripple(store, "a", max_hops=2, vault_path=_empty_vault(tmp_path))
    assert {it.id for it in report.tiers[TIER_DIRECT]} == {"b"}
    assert {it.id for it in report.tiers[TIER_TRANSITIVE]} == {"c"}
    assert report.counts["derived_directly"] == 1
    assert report.counts["derived_transitively"] == 1
    store.close()


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_determinism_same_store_same_report(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("Melanie: pottery class today.", 1.0, "m1"))
    store.append(_ep("Melanie: pottery again.", 2.0, "m2"))
    store.append(_ep("Fused summary.", 3.0, "fused1", kind="fusion"))
    store.log_event("m1", "fused_into", {"by": "fused1", "entity": "melanie"})
    graph_store(store, backend=None)

    r1 = ripple(store, "m1", vault_path=_empty_vault(tmp_path))
    r2 = ripple(store, "m1", vault_path=_empty_vault(tmp_path))
    assert r1 == r2
    assert json.dumps(r1.to_dict(), sort_keys=True) == json.dumps(r2.to_dict(), sort_keys=True)
    store.close()


# --------------------------------------------------------------------------- #
# Empty case
# --------------------------------------------------------------------------- #
def test_empty_case_well_formed_report(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("A lonely memory with no connections at all.", 1.0, "solo"))

    report = ripple(store, "solo", vault_path=_empty_vault(tmp_path))
    assert report.tiers[TIER_DIRECT] == []
    assert report.tiers[TIER_TRANSITIVE] == []
    assert report.tiers[TIER_ASSOCIATIVE] == []
    assert report.counts == {
        TIER_DIRECT: 0,
        TIER_TRANSITIVE: 0,
        TIER_ASSOCIATIVE: 0,
        "total": 0,
    }
    assert report.total_impact_score == 0.0
    assert report.memory_id == "solo"
    assert report.memory_status == "active"
    store.close()


# --------------------------------------------------------------------------- #
# Errors, JSON shape, and the event-wiring wrapper
# --------------------------------------------------------------------------- #
def test_unknown_memory_id_raises(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    try:
        ripple(store, "does-not-exist", vault_path=_empty_vault(tmp_path))
        assert False, "expected ValueError"
    except ValueError:
        pass
    store.close()


def test_report_is_json_serializable(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("A", 1.0, "a"))
    store.append(_ep("B", 2.0, "b", kind="fusion"))
    store.log_event("a", "fused_into", {"by": "b", "entity": "x"})

    report = ripple(store, "a", vault_path=_empty_vault(tmp_path))
    blob = json.dumps(report.to_dict())
    assert '"fused_from"' in blob
    store.close()


def test_ripple_on_event_uses_event_episode_id(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("Old fact.", 1.0, "a"))
    store.append(_ep("Corrected fact.", 2.0, "b"))
    ev = store.log_event("a", "superseded", {"by": "b", "reason": "human_correction"})

    report = ripple_on_event(store, ev, vault_path=_empty_vault(tmp_path))
    assert report.memory_id == "a"
    assert any(it.id == "b" for it in report.tiers[TIER_DIRECT])
    store.close()


def test_default_vault_path_does_not_error(tmp_path):
    """Sanity check for the DEFAULT_VAULT_PATH fallback: whatever is on
    this machine's real ~/memsub-vault, ripple() must not crash when
    vault_path is omitted."""
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(_ep("Standalone memory.", 1.0, "solo"))
    report = ripple(store, "solo")
    assert report.memory_id == "solo"
    store.close()
