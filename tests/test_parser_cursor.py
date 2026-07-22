"""Cursor parser: fixture-tested only (no Cursor install on this machine,
confirmed absent while building this). See memsub/parsers/cursor.py's
module docstring: the JSON field names this parser looks for are a best
guess at community-documented reverse engineering, confidence LOW."""

import json
import sqlite3

from memsub.filehist import versions
from memsub.parsers.cursor import index_storage
from memsub.store import EpisodicStore


def _make_db(path, rows, table="ItemTable"):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE {table} (key TEXT, value TEXT)")
    for key, value in rows.items():
        conn.execute(f"INSERT INTO {table} (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def test_global_storage_extracts_code_block_writes(tmp_path):
    cursor_home = tmp_path / "Cursor" / "User"
    db_path = cursor_home / "globalStorage" / "state.vscdb"
    composer = {"conversation": [{"codeBlocks": [
        {"uri": {"path": "/tmp/proj/x.py"}, "content": "hello\n"}
    ]}]}
    _make_db(db_path, {"composerData:abc": json.dumps(composer)})

    store = EpisodicStore(tmp_path / "m.db")
    added = index_storage(store, cursor_home)
    assert added == 1
    assert versions(store, "/tmp/proj/x.py")[0].kind == "write"


def test_workspace_storage_dbs_are_also_scanned(tmp_path):
    cursor_home = tmp_path / "Cursor" / "User"
    db_path = cursor_home / "workspaceStorage" / "somehash" / "state.vscdb"
    bubble = {"messages": [{"codeBlocks": [{"path": "/tmp/proj/y.py", "content": "hi\n"}]}]}
    _make_db(db_path, {"bubbleId:abc:1": json.dumps(bubble)}, table="cursorDiskKV")

    store = EpisodicStore(tmp_path / "m.db")
    added = index_storage(store, cursor_home)
    assert added == 1
    assert versions(store, "/tmp/proj/y.py")[0].kind == "write"


def test_rerun_dedups_unchanged_rows(tmp_path):
    cursor_home = tmp_path / "Cursor" / "User"
    db_path = cursor_home / "globalStorage" / "state.vscdb"
    composer = {"conversation": [{"codeBlocks": [
        {"uri": {"path": "/tmp/proj/x.py"}, "content": "hello\n"}
    ]}]}
    _make_db(db_path, {"composerData:abc": json.dumps(composer)})

    store = EpisodicStore(tmp_path / "m.db")
    assert index_storage(store, cursor_home) == 1
    assert index_storage(store, cursor_home) == 0  # unchanged: deduped


def test_read_only_never_writes_to_the_source_db(tmp_path):
    cursor_home = tmp_path / "Cursor" / "User"
    db_path = cursor_home / "globalStorage" / "state.vscdb"
    _make_db(db_path, {"composerData:abc": json.dumps({"conversation": []})})
    before = db_path.read_bytes()

    store = EpisodicStore(tmp_path / "m.db")
    index_storage(store, cursor_home)
    assert db_path.read_bytes() == before


def test_absent_cursor_home_is_a_silent_noop(tmp_path):
    store = EpisodicStore(tmp_path / "m.db")
    assert index_storage(store, tmp_path / "nope") == 0


def test_unrecognized_json_shape_is_a_silent_noop(tmp_path):
    cursor_home = tmp_path / "Cursor" / "User"
    db_path = cursor_home / "globalStorage" / "state.vscdb"
    _make_db(db_path, {"composerData:abc": json.dumps({"totally": "unexpected"})})

    store = EpisodicStore(tmp_path / "m.db")
    assert index_storage(store, cursor_home) == 0
