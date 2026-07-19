"""Consolidation tests: event log, dedup, contradiction, decay. LLM mocked."""

import json
import time

from memsub.backends import MockBackend
from memsub.cli import main
from memsub.consolidate import ConsolidationConfig, consolidate
from memsub.store import Episode, EpisodicStore


NOW = time.time()


def _ep(content, ts, kind="fact", importance=1.0, session_id="s"):
    return Episode(
        content=content, run_id="r", session_id=session_id, kind=kind,
        importance=importance, ts=ts,
    )


def _store_with(*episodes):
    store = EpisodicStore(":memory:")
    return store, [store.append(e) for e in episodes]


# --------------------------------------------------------------------------- #
# Event log
# --------------------------------------------------------------------------- #
def test_event_log_and_status():
    store, (ep,) = _store_with(_ep("a fact", NOW))
    assert store.status(ep.id) == "active"
    store.log_event(ep.id, "retrieved", {"query": "q"})
    assert store.status(ep.id) == "active"
    store.log_event(ep.id, "superseded", {"reason": "duplicate", "by": "x"})
    assert store.status(ep.id) == "superseded"
    evs = store.events(episode_id=ep.id)
    assert [e.event for e in evs] == ["superseded", "retrieved"]
    assert store.events(event="retrieved")[0].detail == {"query": "q"}


def test_active_excludes_terminal():
    store, (a, b, c) = _store_with(
        _ep("keep me", NOW), _ep("supersede me", NOW + 1), _ep("forget me", NOW + 2)
    )
    store.log_event(b.id, "superseded", {})
    store.log_event(c.id, "forgotten", {})
    active = store.active()
    assert [e.id for e in active] == [a.id]
    # audit trail intact: all rows still present
    assert store.count() == 3


# --------------------------------------------------------------------------- #
# Dedup
# --------------------------------------------------------------------------- #
def test_dedup_supersedes_older_copy():
    store, (old, new) = _store_with(
        _ep("the repo uses pytest.ini to disable ROS plugins", NOW),
        _ep("the repo uses pytest.ini to disable ROS plugins", NOW + 10),
    )
    actions = consolidate(store, backend=None)
    assert len(actions) == 1
    a = actions[0]
    assert a.action == "supersede" and a.reason == "duplicate"
    assert a.episode_id == old.id and a.detail["by"] == new.id
    assert store.status(old.id) == "superseded"
    assert store.status(new.id) == "active"


def test_unrelated_memories_untouched():
    store, _ = _store_with(
        _ep("user prefers plain markdown", NOW),
        _ep("the VPS runs docker compose", NOW + 1),
    )
    assert consolidate(store, backend=None) == []


# --------------------------------------------------------------------------- #
# Contradiction
# --------------------------------------------------------------------------- #
def _contradiction_backend(verdict=True):
    resp = json.dumps([{"pair": 0, "contradicts": verdict}])
    return MockBackend(responder=lambda s, m: resp)


def test_contradiction_newer_supersedes_older():
    store, (old, new) = _store_with(
        _ep("the project database is postgres", NOW),
        _ep("the project database is sqlite", NOW + 10),
    )
    backend = _contradiction_backend(True)
    actions = consolidate(store, backend=backend)
    assert [a.reason for a in actions] == ["contradiction"]
    assert actions[0].episode_id == old.id and actions[0].detail["by"] == new.id
    assert store.status(old.id) == "superseded"
    # superseded memory kept for audit
    assert store.get(old.id) is not None
    assert len(backend.calls) == 1


def test_contradiction_judge_accepts_bare_object():
    """A smaller local backend sometimes returns a bare {"pair":0,...} object
    instead of the array the prompt asks for; the judge must still act on it."""
    store, (old, new) = _store_with(
        _ep("the project database is postgres", NOW),
        _ep("the project database is sqlite", NOW + 10),
    )
    bare = MockBackend(responder=lambda s, m: '{"pair": 0, "contradicts": true}')
    actions = consolidate(store, backend=bare)
    assert [a.reason for a in actions] == ["contradiction"]
    assert store.status(old.id) == "superseded"


def test_no_contradiction_keeps_both():
    store, _ = _store_with(
        _ep("the project database is postgres", NOW),
        _ep("the project database is backed up nightly", NOW + 10),
    )
    assert consolidate(store, backend=_contradiction_backend(False)) == []


def test_contradiction_pass_skipped_without_backend():
    store, _ = _store_with(
        _ep("the project database is postgres", NOW),
        _ep("the project database is sqlite", NOW + 10),
    )
    assert consolidate(store, backend=None) == []


def test_contradiction_only_same_kind():
    store, _ = _store_with(
        _ep("the project database is postgres", NOW, kind="fact"),
        _ep("the project database is sqlite", NOW + 10, kind="decision"),
    )
    backend = _contradiction_backend(True)
    assert consolidate(store, backend=backend) == []
    assert backend.calls == []


# --------------------------------------------------------------------------- #
# Fusion (MIRIX-style write-time cross-session fact fusion)
# --------------------------------------------------------------------------- #
def _fusion_backend(content="Alice moved to Paris and started a job at Google."):
    resp = json.dumps({"fuse": True, "content": content})
    return MockBackend(responder=lambda s, m: resp)


def test_fusion_creates_linked_entry_across_sessions():
    store, (a, b) = _store_with(
        _ep("Alice mentioned she moved to Paris.", NOW, session_id="s1"),
        _ep("Alice said she started a new job at Google.", NOW + 10, session_id="s2"),
    )
    backend = _fusion_backend()
    actions = consolidate(store, backend=backend)
    fuse_actions = [act for act in actions if act.action == "fuse"]
    assert len(fuse_actions) == 1
    fused = store.get(fuse_actions[0].episode_id)
    assert fused is not None
    assert fused.kind == "fusion"
    assert set(fused.provenance["fused_from"]) == {a.id, b.id}
    assert fused.provenance["entity"] == "Alice"
    # sources preserved: still active, never superseded or deleted
    assert store.status(a.id) == "active"
    assert store.status(b.id) == "active"
    assert store.get(a.id) is not None and store.get(b.id) is not None
    assert any(e.event == "fused_into" for e in store.events(episode_id=a.id))
    assert any(e.event == "fused_into" for e in store.events(episode_id=b.id))


def test_fusion_idempotent_on_rerun():
    store, _ = _store_with(
        _ep("Alice mentioned she moved to Paris.", NOW, session_id="s1"),
        _ep("Alice said she started a new job at Google.", NOW + 10, session_id="s2"),
    )
    backend = _fusion_backend()
    first = [a for a in consolidate(store, backend=backend) if a.action == "fuse"]
    assert len(first) == 1
    n_calls_after_first = len(backend.calls)
    second = [a for a in consolidate(store, backend=backend) if a.action == "fuse"]
    assert second == []  # no duplicate fused entry
    assert len(backend.calls) == n_calls_after_first  # no new LLM call either
    fusion_rows = [ep for ep in store.all() if ep.kind == "fusion"]
    assert len(fusion_rows) == 1


def test_fusion_skipped_without_backend():
    store, _ = _store_with(
        _ep("Alice mentioned she moved to Paris.", NOW, session_id="s1"),
        _ep("Alice said she started a new job at Google.", NOW + 10, session_id="s2"),
    )
    actions = consolidate(store, backend=None)
    assert [a for a in actions if a.action == "fuse"] == []
    assert [ep for ep in store.all() if ep.kind == "fusion"] == []


def test_fusion_requires_min_sessions():
    """Same entity, same session: not a cross-session fusion candidate."""
    store, _ = _store_with(
        _ep("Alice mentioned she moved to Paris.", NOW, session_id="s1"),
        _ep("Alice said she started a new job at Google.", NOW + 10, session_id="s1"),
    )
    backend = _fusion_backend()
    actions = consolidate(store, backend=backend)
    assert [a for a in actions if a.action == "fuse"] == []


def test_fusion_respects_negative_verdict():
    store, _ = _store_with(
        _ep("Alice mentioned she moved to Paris.", NOW, session_id="s1"),
        _ep("Alice said she started a new job at Google.", NOW + 10, session_id="s2"),
    )
    backend = MockBackend(responder=lambda s, m: json.dumps({"fuse": False, "content": ""}))
    actions = consolidate(store, backend=backend)
    assert [a for a in actions if a.action == "fuse"] == []
    assert len(backend.calls) == 1  # still judged once, just not written


def test_fusion_dry_run_writes_nothing():
    store, _ = _store_with(
        _ep("Alice mentioned she moved to Paris.", NOW, session_id="s1"),
        _ep("Alice said she started a new job at Google.", NOW + 10, session_id="s2"),
    )
    backend = _fusion_backend()
    actions = consolidate(store, backend=backend, dry_run=True)
    assert len([a for a in actions if a.action == "fuse"]) == 1
    assert [ep for ep in store.all() if ep.kind == "fusion"] == []


# --------------------------------------------------------------------------- #
# Decay
# --------------------------------------------------------------------------- #
def test_decay_forgets_old_low_value():
    old_ts = NOW - 90 * 86400
    store, (stale, fresh) = _store_with(
        _ep("some low value note", old_ts, importance=0.3),
        _ep("recent important context", NOW, importance=1.0),
    )
    actions = consolidate(store, backend=None, now=NOW)
    assert len(actions) == 1
    a = actions[0]
    assert a.action == "forget" and a.reason == "decay" and a.episode_id == stale.id
    assert a.detail["retrieved_count"] == 0
    assert store.status(stale.id) == "forgotten"
    assert store.status(fresh.id) == "active"


def test_decay_spares_frequently_retrieved():
    old_ts = NOW - 90 * 86400
    store, (stale,) = _store_with(_ep("some low value note", old_ts, importance=0.3))
    for _ in range(5):
        store.log_event(stale.id, "retrieved", {})
    assert consolidate(store, backend=None, now=NOW) == []


def test_decay_spares_young_memories():
    store, _ = _store_with(_ep("tiny note", NOW - 2 * 86400, importance=0.01))
    assert consolidate(store, backend=None, now=NOW) == []


def test_decay_spares_human_confirmed():
    """A vault-apply confirm (verdict: confirm) logs a "confirmed" event;
    decay must skip it forever, regardless of age or value."""
    old_ts = NOW - 90 * 86400
    store, (pinned,) = _store_with(_ep("some low value note", old_ts, importance=0.3))
    store.log_event(pinned.id, "confirmed", {"reason": "human_confirm"})
    assert consolidate(store, backend=None, now=NOW) == []
    assert store.status(pinned.id) == "active"


def test_dry_run_applies_nothing():
    store, (old, _) = _store_with(
        _ep("the repo uses pytest.ini to disable ROS plugins", NOW),
        _ep("the repo uses pytest.ini to disable ROS plugins", NOW + 10),
    )
    actions = consolidate(store, backend=None, dry_run=True)
    assert len(actions) == 1
    assert store.status(old.id) == "active"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_consolidate(tmp_path, capsys):
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    store.append(_ep("the repo uses pytest.ini to disable ROS plugins", NOW))
    store.append(_ep("the repo uses pytest.ini to disable ROS plugins", NOW + 10))
    store.close()
    rc = main(["consolidate", "--db", str(db), "--no-llm"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "duplicate" in out and "1 action(s) applied" in out
    store = EpisodicStore(db)
    assert len(store.active()) == 1


def test_cli_consolidate_missing_db(tmp_path, capsys):
    rc = main(["consolidate", "--db", str(tmp_path / "nope.db"), "--no-llm"])
    assert rc == 1
