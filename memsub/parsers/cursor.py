"""Cursor editor transcript parser (fixture-tested only, confidence LOW).

Cursor stores composer/chat history in local sqlite databases:
  ~/.config/Cursor/User/globalStorage/state.vscdb           (global)
  ~/.config/Cursor/User/workspaceStorage/<hash>/state.vscdb (per-workspace)
in two key-value tables, ItemTable and cursorDiskKV (both key TEXT, value
TEXT/BLOB JSON; which table holds composer data varies by Cursor version),
keyed by `composerData:<composerId>` (session-level) and
`bubbleId:<composerId>:<bubbleId>` (per-message, newer versions). This
table/key layout is community-documented reverse engineering, not published
by Cursor/Anysphere. There is no Cursor install on this machine to validate
against, so the JSON field names this parser looks for inside a value (a
message carrying a "codeBlocks" list of {"uri": {"path": ...}, "content":
...}, or the flatter {"path": ..., "content": ...}) are this parser's own
best guess at that shape, exercised only against a synthetic fixture built
to match it. If a real Cursor database uses different field names, this
finds nothing and returns 0: it fails open, it never crashes the scan.

Read-only, always: every sqlite connection is opened with `mode=ro` in the
URI so Blackbox can never write to a live Cursor session database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .. import filehist

DEFAULT_CURSOR_HOME = Path.home() / ".config" / "Cursor" / "User"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _open_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _kv_rows(db_path: Path):
    """Yield (key, value) for composerData:*/bubbleId:* keys across
    whichever of ItemTable/cursorDiskKV exists in this db (both are
    optional, depending on Cursor version)."""
    conn = _open_ro(db_path)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for table in ("ItemTable", "cursorDiskKV"):
            if table not in tables:
                continue
            rows = conn.execute(
                f"SELECT key, value FROM {table}"
                " WHERE key LIKE 'composerData:%' OR key LIKE 'bubbleId:%'"
            ).fetchall()
            yield from rows
    finally:
        conn.close()


def _file_writes(raw_value) -> list[tuple[str, str]]:
    """Best-effort (path, content) pairs found in one row's JSON value."""
    try:
        text = raw_value.decode() if isinstance(raw_value, (bytes, bytearray)) else raw_value
        data = json.loads(text)
    except (ValueError, TypeError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    messages = data.get("conversation") or data.get("messages") or [data]
    if isinstance(messages, dict):
        messages = [messages]
    out: list[tuple[str, str]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for block in msg.get("codeBlocks") or []:
            if not isinstance(block, dict):
                continue
            content = block.get("content")
            uri = block.get("uri")
            path = uri.get("path") if isinstance(uri, dict) else block.get("path")
            if isinstance(path, str) and isinstance(content, str):
                out.append((path, content))
    return out


def index_storage(store, cursor_home: str | Path | None = None) -> int:
    """Index every state.vscdb under cursor_home (global + workspaceStorage/*).
    Returns rows added. Silent no-op if Cursor isn't installed here."""
    home = Path(cursor_home) if cursor_home else DEFAULT_CURSOR_HOME
    if not home.is_dir():
        return 0

    db_paths: list[Path] = []
    global_db = home / "globalStorage" / "state.vscdb"
    if global_db.is_file():
        db_paths.append(global_db)
    ws_root = home / "workspaceStorage"
    if ws_root.is_dir():
        db_paths.extend(sorted(ws_root.glob("*/state.vscdb")))

    conn = filehist._conn(store)
    added = 0
    for db_path in db_paths:
        tkey = f"cursor:{db_path}"
        try:
            rows = list(_kv_rows(db_path))
        except sqlite3.DatabaseError:
            continue
        for _key, value in rows:
            for path, content in _file_writes(value):
                body = content[: filehist.MAX_CONTENT_BYTES]
                content_hash = _sha(body)
                # No byte-offset checkpoint here: Cursor's sqlite is a live
                # key-value store, not an append-only log. Dedup instead by
                # (path, transcript, content_hash), so an unchanged row costs
                # one cheap SELECT on rerun rather than growing filestates
                # forever.
                exists = conn.execute(
                    "SELECT 1 FROM filestates WHERE path = ? AND transcript = ?"
                    " AND content_hash = ?",
                    (path, tkey, content_hash),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO filestates (path, ts, transcript, kind, content_hash, content)"
                    " VALUES (?, ?, ?, 'write', ?, ?)",
                    (path, db_path.stat().st_mtime, tkey, content_hash, body),
                )
                added += 1
    conn.commit()
    return added
