"""F1: belief timelines + as-of (point-in-time) recall.

Written FIRST per superpowers TDD; memsub/timeline.py implements against
these. Scenario mirrors the consulting-rate story: a belief revised twice,
with bi-temporal windows and supersede events.
"""

import datetime

import pytest

from memsub.store import Episode, EpisodicStore
from memsub.timeline import as_of_recall, belief_chain, believed_at, status_at


def _day(y, m, d):
    return datetime.datetime(y, m, d, 12, 0).timestamp()


@pytest.fixture()
def rate_store(tmp_path):
    s = EpisodicStore(tmp_path / "t.db")
    rows = [
        ("v1", "rate is ninety dollars an hour", _day(2026, 1, 6), _day(2026, 1, 6), _day(2026, 3, 1)),
        ("v2", "rate raised to one twenty an hour", _day(2026, 3, 1), _day(2026, 3, 1), _day(2026, 5, 1)),
        ("v3", "rate raised to one sixty an hour", _day(2026, 5, 1), _day(2026, 5, 1), None),
        ("other", "bought a standing desk", _day(2026, 2, 10), None, None),
    ]
    for eid, content, ts, vf, vt in rows:
        s.append(Episode(content=content, run_id="r", session_id="s", id=eid,
                         ts=ts, valid_from=vf, valid_to=vt).with_defaults())
    s.log_event("v1", "superseded", {"reason": "rate_change", "by": "v2"})
    s.log_event("v2", "superseded", {"reason": "rate_change", "by": "v3"})
    return s


def test_belief_chain_orders_full_history(rate_store):
    for start in ("v1", "v2", "v3"):
        chain = belief_chain(rate_store, start)
        assert [e.id for e in chain] == ["v1", "v2", "v3"], f"from {start}"


def test_belief_chain_singleton_for_unchained(rate_store):
    assert [e.id for e in belief_chain(rate_store, "other")] == ["other"]


def test_status_at_uses_validity_windows(rate_store):
    asof = _day(2026, 2, 1)
    assert status_at(rate_store, "v1", asof) == "believed"
    assert status_at(rate_store, "v2", asof) == "not_yet_known"
    asof = _day(2026, 4, 1)
    assert status_at(rate_store, "v1", asof) == "superseded"
    assert status_at(rate_store, "v2", asof) == "believed"


def test_believed_at_filters_the_world(rate_store):
    # Feb 20: v1 still valid, desk (Feb 10) known, v2/v3 not yet
    ids = {e.id for e in believed_at(rate_store, _day(2026, 2, 20))}
    assert ids == {"v1", "other"}
    ids = {e.id for e in believed_at(rate_store, _day(2026, 6, 1))}
    assert ids == {"v3", "other"}


def test_as_of_recall_returns_the_belief_of_that_day(rate_store):
    hits = as_of_recall(rate_store, "what is my rate", _day(2026, 2, 1), k=3)
    assert hits and hits[0].id == "v1"
    hits = as_of_recall(rate_store, "what is my rate", _day(2026, 4, 1), k=3)
    assert hits and hits[0].id == "v2"
    hits = as_of_recall(rate_store, "what is my rate", _day(2026, 6, 1), k=3)
    assert hits and hits[0].id == "v3"


def test_as_of_recall_excludes_future_knowledge(rate_store):
    hits = as_of_recall(rate_store, "standing desk", _day(2026, 1, 20), k=3)
    assert all(e.id != "other" for e in hits)
