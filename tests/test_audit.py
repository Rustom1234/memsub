"""Audit view tests: recall logging, audit records, recent activity, CLI."""

from memsub.audit import audit_episode, log_recall, recent_activity, render_audit, render_recent
from memsub.cli import main
from memsub.store import Episode, EpisodicStore


def _ep(content, **kw):
    return Episode(content=content, run_id="r", session_id="s", kind="fact", **kw)


def test_log_recall_records_retrieval_and_injection():
    store = EpisodicStore(":memory:")
    a = store.append(_ep("fact a"))
    b = store.append(_ep("fact b"))
    log_recall(store, "what is a?", scored=[(a, 0.9), (b, 0.4)], injected=[a], budget_tokens=100)
    retrieved = store.events(event="retrieved")
    assert len(retrieved) == 2
    assert {e.episode_id for e in retrieved} == {a.id, b.id}
    ranks = {e.episode_id: e.detail["rank"] for e in retrieved}
    assert ranks[a.id] == 0 and ranks[b.id] == 1
    injected = store.events(event="injected")
    assert len(injected) == 1
    assert injected[0].episode_id == a.id
    assert injected[0].detail == {"query": "what is a?", "position": 0, "budget_tokens": 100}


def test_retrieval_memory_logs_events():
    from memsub.memory import RetrievalMemory
    from memsub.policy import MemoryPolicy

    mem = RetrievalMemory(policy=MemoryPolicy(write_kinds=("fact",)))
    mem.write(_ep("the deploy port is 8443"))
    result = mem.recall("deploy port?")
    assert result.used_memory
    assert len(mem.store.events(event="retrieved")) >= 1
    assert len(mem.store.events(event="injected")) >= 1
    ev = mem.store.events(event="injected")[0]
    assert ev.detail["query"] == "deploy port?"


def test_audit_episode_full_record():
    store = EpisodicStore(":memory:")
    ep = store.append(_ep("a fact", provenance={"origin": "extraction", "source": "conv.jsonl"}))
    log_recall(store, "q", scored=[(ep, 0.8)], injected=[ep])
    store.log_event(ep.id, "superseded", {"reason": "contradiction", "by": "other-id"})
    record = audit_episode(store, ep.id)
    assert record["status"] == "superseded"
    assert record["provenance"]["source"] == "conv.jsonl"
    assert record["counts"] == {"retrieved": 1, "injected": 1}
    assert [e.event for e in record["events"]] == ["superseded", "injected", "retrieved"]
    text = render_audit(record)
    assert "conv.jsonl" in text and "superseded" in text and "retrieved 1 time(s)" in text


def test_audit_missing_episode():
    store = EpisodicStore(":memory:")
    assert audit_episode(store, "nope") is None


def test_recent_activity_joins_snippets():
    store = EpisodicStore(":memory:")
    ep = store.append(_ep("memorable content here"))
    log_recall(store, "q", scored=[(ep, 0.5)], injected=[ep])
    rows = recent_activity(store, limit=10)
    assert len(rows) == 2
    assert rows[0]["content"] == "memorable content here"
    out = render_recent(rows)
    assert "memorable content" in out and "injected" in out


def test_cli_audit_and_recent(tmp_path, capsys):
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    ep = store.append(_ep("cli audited fact", provenance={"origin": "extraction"}))
    log_recall(store, "query x", scored=[(ep, 0.7)], injected=[ep])
    store.close()

    assert main(["audit", ep.id, "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "cli audited fact" in out and "origin: extraction" in out

    # unambiguous prefix works
    assert main(["audit", ep.id[:8], "--db", str(db)]) == 0
    assert "cli audited fact" in capsys.readouterr().out

    assert main(["audit", "--recent", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "injected" in out and "retrieved" in out

    assert main(["audit", "definitely-not-an-id", "--db", str(db)]) == 1
