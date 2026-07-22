"""Event-log JSONL mirror: round-trip and idempotence (eng-review I4)."""

import json

from memsub.store import Episode, EpisodicStore


def _mk_store(tmp_path):
    store = EpisodicStore(tmp_path / "m.db")
    ep = Episode(content="fact one", run_id="r", session_id="s").with_defaults()
    store.append(ep)
    return store, ep


def test_log_event_appends_mirror_line(tmp_path):
    store, ep = _mk_store(tmp_path)
    store.log_event(ep.id, "superseded", {"reason": "test"})
    lines = (tmp_path / "m.db.events.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["episode_id"] == ep.id
    assert rec["event"] == "superseded"
    assert rec["detail"] == {"reason": "test"}
    store.close()


def test_rebuild_from_mirror_restores_status(tmp_path):
    store, ep = _mk_store(tmp_path)
    store.log_event(ep.id, "superseded", {"reason": "test"})
    mirror = tmp_path / "m.db.events.jsonl"
    store.close()

    fresh = EpisodicStore(tmp_path / "fresh.db")
    fresh.append(Episode(content="fact one", run_id="r", session_id="s", id=ep.id).with_defaults())
    assert fresh.status(ep.id) == "active"
    inserted = fresh.rebuild_events_from_mirror(mirror)
    assert inserted == 1
    assert fresh.status(ep.id) == "superseded"
    # replay is idempotent
    assert fresh.rebuild_events_from_mirror(mirror) == 0
    fresh.close()


def test_rebuild_handles_seed_lines_without_id(tmp_path):
    store, ep = _mk_store(tmp_path)
    mirror = tmp_path / "m.db.events.jsonl"
    mirror.write_text(json.dumps({
        "episode_id": ep.id, "event": "superseded",
        "detail": json.dumps({"reason": "seed"}), "ts": 1.0,
    }) + "\n")
    assert store.rebuild_events_from_mirror(mirror) == 1
    assert store.rebuild_events_from_mirror(mirror) == 0
    assert store.status(ep.id) == "superseded"
    assert store.events(episode_id=ep.id)[0].detail == {"reason": "seed"}
    store.close()


def test_memory_store_skips_mirror():
    store = EpisodicStore(":memory:")
    ep = Episode(content="x", run_id="r", session_id="s").with_defaults()
    store.append(ep)
    store.log_event(ep.id, "retrieved")  # must not raise
    store.close()
