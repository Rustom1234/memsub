import threading

from memsub.store import Episode, EpisodicStore


def test_append_and_get():
    store = EpisodicStore()
    ep = store.append(Episode(content="hello", run_id="r1", session_id="1"))
    assert ep.id
    assert ep.ts > 0
    got = store.get(ep.id)
    assert got is not None
    assert got.content == "hello"


def test_append_only_ordering():
    store = EpisodicStore()
    for i in range(5):
        store.append(Episode(content=f"e{i}", run_id="r1", session_id=str(i), ts=100 + i))
    eps = store.all(run_id="r1")
    assert [e.content for e in eps] == ["e0", "e1", "e2", "e3", "e4"]


def test_filter_by_kind_and_count():
    store = EpisodicStore()
    store.append(Episode(content="a", run_id="r1", session_id="1", kind="session_summary"))
    store.append(Episode(content="b", run_id="r1", session_id="1", kind="other"))
    store.append(Episode(content="c", run_id="r2", session_id="1", kind="session_summary"))
    assert store.count() == 3
    assert store.count(run_id="r1") == 2
    summaries = store.all(kind="session_summary")
    assert len(summaries) == 2


def test_provenance_roundtrip():
    store = EpisodicStore()
    ep = store.append(
        Episode(content="x", run_id="r", session_id="1", provenance={"claimed": 3, "actual": 1})
    )
    got = store.get(ep.id)
    assert got.provenance == {"claimed": 3, "actual": 1}


def test_file_backed_store_uses_wal_and_busy_timeout(tmp_path):
    db = tmp_path / "memory.db"
    store = EpisodicStore(db)
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    timeout_ms = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout_ms >= 10000
    store.close()


def test_append_batch_with_checkpoint_writes_all_and_checkpoint():
    store = EpisodicStore()
    episodes = [Episode(content=f"e{i}", run_id="r1", session_id="1") for i in range(3)]
    written = store.append_batch_with_checkpoint(episodes, "file.jsonl", "hash1")
    assert len(written) == 3
    assert store.count() == 3
    assert store.get_extraction_checkpoint("file.jsonl") == "hash1"


def test_append_batch_with_checkpoint_rolls_back_atomically_on_mid_batch_error(monkeypatch):
    """A crash (or any exception) after some episodes are inserted but before
    the transaction commits must leave zero episodes and no checkpoint, not
    a partial batch with no checkpoint (which would cause a rerun to
    re-extract and duplicate the ones that did land)."""
    store = EpisodicStore()
    episodes = [Episode(content=f"e{i}", run_id="r1", session_id="1") for i in range(3)]

    original_insert = store._insert_episode
    calls = {"n": 0}

    def _failing_insert(ep):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash mid-batch")
        original_insert(ep)

    monkeypatch.setattr(store, "_insert_episode", _failing_insert)

    import pytest

    with pytest.raises(RuntimeError, match="simulated crash mid-batch"):
        store.append_batch_with_checkpoint(episodes, "file.jsonl", "hash1")

    assert store.count() == 0  # the first episode's insert was rolled back too
    assert store.get_extraction_checkpoint("file.jsonl") is None

    # rerun after the "crash" (this time nothing fails) succeeds cleanly
    monkeypatch.setattr(store, "_insert_episode", original_insert)
    written = store.append_batch_with_checkpoint(episodes, "file.jsonl", "hash1")
    assert len(written) == 3
    assert store.count() == 3
    assert store.get_extraction_checkpoint("file.jsonl") == "hash1"


def test_two_connections_interleave_write_and_read_without_locking(tmp_path):
    """Simulates an MCP server (reader) and the extraction/consolidation CLI
    (writer) holding independent connections to the same DB file at once.
    Without WAL + busy_timeout this raises 'database is locked'."""
    db = tmp_path / "memory.db"
    seed_store = EpisodicStore(db)
    seed_store.append(Episode(content="seed", run_id="r1", session_id="1"))
    seed_store.close()

    # Each thread opens its own connection (sqlite3 connections are not
    # shareable across threads), mirroring two independent processes.
    errors: list[Exception] = []

    def write_many():
        try:
            writer = EpisodicStore(db)
            for i in range(50):
                writer.append(Episode(content=f"w{i}", run_id="r1", session_id="1"))
            writer.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def read_many():
        try:
            reader = EpisodicStore(db)
            for _ in range(50):
                reader.all(run_id="r1")
            reader.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=write_many)
    t2 = threading.Thread(target=read_many)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == []
    verify = EpisodicStore(db)
    assert verify.count(run_id="r1") == 51
    verify.close()
