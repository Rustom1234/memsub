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
