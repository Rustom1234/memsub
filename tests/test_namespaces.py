"""Namespacing tests: isolation, ACL, shared namespaces, migration, CLI."""

import sqlite3
import time

import pytest

from memsub.cli import main
from memsub.namespaces import MemoryScope, NamespaceManager, PermissionDenied
from memsub.store import Episode, EpisodicStore


def _ep(content, ns="default", **kw):
    return Episode(content=content, run_id="r", session_id="s", namespace=ns, **kw)


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #
def test_namespace_isolation():
    store = EpisodicStore(":memory:")
    store.append(_ep("alpha memory", ns="agent:alpha"))
    store.append(_ep("beta memory", ns="agent:beta"))
    assert [e.content for e in store.all(namespace="agent:alpha")] == ["alpha memory"]
    assert [e.content for e in store.active(namespace="agent:beta")] == ["beta memory"]
    assert len(store.all()) == 2  # no filter sees everything (admin view)


def test_namespace_roundtrip_and_default():
    store = EpisodicStore(":memory:")
    ep = store.append(_ep("x", ns="user:rustom"))
    assert store.get(ep.id).namespace == "user:rustom"
    ep2 = store.append(Episode(content="y", run_id="r", session_id="s"))
    assert store.get(ep2.id).namespace == "default"


def test_migration_adds_namespace_column(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE episodes (
            id TEXT PRIMARY KEY, ts REAL NOT NULL, run_id TEXT NOT NULL,
            session_id TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
            importance REAL NOT NULL DEFAULT 1.0, provenance TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.execute(
        "INSERT INTO episodes (id, ts, run_id, session_id, kind, content) VALUES"
        " ('e1', 1.0, 'r', 's', 'fact', 'legacy memory')"
    )
    conn.commit()
    conn.close()
    store = EpisodicStore(db)
    ep = store.get("e1")
    assert ep is not None and ep.namespace == "default"
    store.append(_ep("new one", ns="agent:a"))
    assert len(store.all(namespace="agent:a")) == 1


# --------------------------------------------------------------------------- #
# ACL
# --------------------------------------------------------------------------- #
def test_unregistered_namespace_is_open():
    store = EpisodicStore(":memory:")
    mgr = NamespaceManager(store)
    assert mgr.can("anyone", "read", "agent:whatever")
    assert mgr.can("anyone", "write", "agent:whatever")


def test_registered_namespace_owner_and_grants():
    store = EpisodicStore(":memory:")
    mgr = NamespaceManager(store)
    mgr.create("shared:team", owner="lead")
    assert mgr.can("lead", "write", "shared:team")
    assert not mgr.can("worker", "read", "shared:team")
    mgr.grant("shared:team", "worker", read=True, write=False)
    assert mgr.can("worker", "read", "shared:team")
    assert not mgr.can("worker", "write", "shared:team")
    mgr.grant("shared:team", "worker", read=True, write=True)
    assert mgr.can("worker", "write", "shared:team")
    mgr.revoke("shared:team", "worker")
    assert not mgr.can("worker", "read", "shared:team")


def test_grant_requires_registered_namespace():
    store = EpisodicStore(":memory:")
    mgr = NamespaceManager(store)
    with pytest.raises(ValueError):
        mgr.grant("shared:nope", "agent")


def test_readable_by():
    store = EpisodicStore(":memory:")
    mgr = NamespaceManager(store)
    mgr.create("shared:a", owner="lead")
    mgr.create("shared:b", owner="other")
    mgr.grant("shared:b", "lead", read=True)
    assert mgr.readable_by("lead") == ["shared:a", "shared:b"]
    assert mgr.readable_by("stranger") == []


# --------------------------------------------------------------------------- #
# MemoryScope: shared namespace for a multi-agent team
# --------------------------------------------------------------------------- #
def test_scope_enforces_write_and_read():
    store = EpisodicStore(":memory:")
    mgr = NamespaceManager(store)
    mgr.create("shared:team", owner="lead")
    mgr.grant("shared:team", "reader", read=True, write=False)

    lead = MemoryScope(store, mgr, agent="lead", namespace="shared:team")
    reader = MemoryScope(store, mgr, agent="reader", namespace="shared:team")
    stranger = MemoryScope(store, mgr, agent="stranger", namespace="shared:team")

    ep = lead.append(_ep("team decision: use sqlite"))
    assert ep.namespace == "shared:team"
    assert ep.provenance["written_by"] == "lead"

    assert [e.content for e in reader.active()] == ["team decision: use sqlite"]
    with pytest.raises(PermissionDenied):
        reader.append(_ep("sneaky write"))
    with pytest.raises(PermissionDenied):
        stranger.active()


def test_scope_stamps_namespace_over_episode_field():
    store = EpisodicStore(":memory:")
    mgr = NamespaceManager(store)
    scope = MemoryScope(store, mgr, agent="a", namespace="agent:a")
    ep = scope.append(_ep("x", ns="somewhere:else"))
    assert ep.namespace == "agent:a"


# --------------------------------------------------------------------------- #
# Consolidation respects namespaces
# --------------------------------------------------------------------------- #
def test_consolidate_does_not_cross_namespaces():
    from memsub.consolidate import consolidate

    now = time.time()
    store = EpisodicStore(":memory:")
    dup = "the repo uses pytest.ini to disable ROS plugins"
    store.append(_ep(dup, ns="agent:a", ts=now))
    store.append(_ep(dup, ns="agent:b", ts=now + 1))
    # identical content in different namespaces: not a duplicate
    assert consolidate(store, backend=None) == []
    # same content twice within one namespace: deduped
    store.append(_ep(dup, ns="agent:a", ts=now + 2))
    actions = consolidate(store, backend=None)
    assert len(actions) == 1 and actions[0].reason == "duplicate"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_ns_create_grant_list(tmp_path, capsys):
    db = str(tmp_path / "mem.db")
    assert main(["ns", "--db", db, "create", "shared:team", "--owner", "lead"]) == 0
    assert main(["ns", "--db", db, "grant", "shared:team", "worker", "--write"]) == 0
    assert main(["ns", "--db", db, "list"]) == 0
    out = capsys.readouterr().out
    assert "shared:team" in out and "owner=lead" in out and "worker(rw)" in out
    assert main(["ns", "--db", db, "revoke", "shared:team", "worker"]) == 0


def test_cli_ns_grant_unregistered_fails(tmp_path, capsys):
    db = str(tmp_path / "mem.db")
    assert main(["ns", "--db", db, "grant", "shared:nope", "worker"]) == 1


def test_cli_extract_namespace(tmp_path, monkeypatch):
    import json

    import memsub.cli as cli
    from memsub.backends import MockBackend

    resp = json.dumps([{"content": "a fact", "kind": "fact", "confidence": 0.9}])
    monkeypatch.setattr(
        cli, "build_backend", lambda prefer, model: MockBackend(responder=lambda s, m: resp)
    )
    f = tmp_path / "c.jsonl"
    f.write_text('{"role": "user", "content": "hello"}\n')
    db = tmp_path / "mem.db"
    assert main(["extract", str(f), "--db", str(db), "--namespace", "agent:me", "--min-turns", "1"]) == 0
    store = EpisodicStore(db)
    assert len(store.all(namespace="agent:me")) == 1
