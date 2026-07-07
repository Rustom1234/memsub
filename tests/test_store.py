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
