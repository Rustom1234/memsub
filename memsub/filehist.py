"""Flight recorder: a file time machine built from agent transcripts.

Feature X (2026-07-21 night, docs/research-memory-landscape-2026-07-21.md
addendum): Claude Code transcripts record every Write (full content), Edit
(old/new strings), and Read (numbered snapshot) an agent ever performed.
Indexed incrementally, that is a cross-session, cross-machine shadow
history of every file the agents touched: enough to reconstruct any file
as of any moment, and to DETECT silent disk rollbacks by comparing what is
on disk against what the transcripts prove was true later. This
productizes the manual recovery that saved four days of work during the
2026-07-21 NFS incident. Nothing else does this: Claude Code's own
checkpoints are per-session/30-day/restore-only, ShadowGit snapshots saves
but alerts on nothing (see the researched comparison in the docs).

Storage: two additive tables in the memsub SQLite db. Contents are deduped
by sha256; per-transcript byte offsets make reindexing an append-only tail
parse (transcripts only ever grow).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .store import EpisodicStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS filestates (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    ts REAL NOT NULL,
    transcript TEXT NOT NULL,
    kind TEXT NOT NULL,           -- write | read | edit
    content_hash TEXT,            -- write/read only
    content TEXT,                 -- write/read only (deduped via blobs)
    old_string TEXT,              -- edit only
    new_string TEXT,              -- edit only
    replace_all INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_filestates_path_ts ON filestates(path, ts);
CREATE TABLE IF NOT EXISTS filehist_checkpoints (
    transcript TEXT PRIMARY KEY,
    byte_offset INTEGER NOT NULL
);
"""

MAX_CONTENT_BYTES = 1_000_000


@dataclass(frozen=True)
class FileVersion:
    path: str
    ts: float
    kind: str
    transcript: str
    content_hash: str | None


@dataclass(frozen=True)
class Incident:
    path: str
    kind: str          # "rollback"
    disk_hash: str
    matched_ts: float  # the OLD state the disk currently matches
    latest_ts: float   # the newest state the transcripts know


def _conn(store: EpisodicStore):
    store._conn.executescript(_SCHEMA)
    return store._conn


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _parse_ts(iso: str) -> float:
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


_NUMBERED = re.compile(r"^(\d+)\t(.*)$")


def _read_result_to_content(text: str) -> str | None:
    """Reconstruct a full file from a Read result's `N<tab>line` format.
    Returns None for truncated/offset reads (non-contiguous or not starting
    at 1): a partial snapshot must never masquerade as a full one (that
    exact mistake corrupted a test file during the 2026-07-21 recovery)."""
    lines: list[str] = []
    for ln in text.split("\n"):
        m = _NUMBERED.match(ln)
        if m:
            if int(m.group(1)) != len(lines) + 1:
                return None
            lines.append(m.group(2))
        elif ln.strip():
            return None
    if not lines:
        return None
    return "\n".join(lines) + "\n"


def index_transcripts(store: EpisodicStore, transcripts_dir: str | Path) -> int:
    """Incremental index over *.jsonl under transcripts_dir. Returns rows added."""
    conn = _conn(store)
    added = 0
    for tpath in sorted(Path(transcripts_dir).rglob("*.jsonl")):
        tkey = str(tpath)
        row = conn.execute(
            "SELECT byte_offset FROM filehist_checkpoints WHERE transcript = ?", (tkey,)
        ).fetchone()
        offset = row[0] if row else 0
        size = tpath.stat().st_size
        if size <= offset:
            continue
        pending_reads: dict[str, tuple[str, float]] = {}  # tool_use_id -> (path, ts)
        with open(tpath, encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            for line in fh:
                if "tool_use" not in line and "tool_result" not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                ts = _parse_ts(rec.get("timestamp", ""))
                content = rec.get("message", {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        name = block.get("name")
                        inp = block.get("input", {})
                        fp = str(inp.get("file_path", ""))
                        if not fp:
                            continue
                        if name == "Write":
                            body = str(inp.get("content", ""))[:MAX_CONTENT_BYTES]
                            conn.execute(
                                "INSERT INTO filestates (path, ts, transcript, kind, content_hash, content)"
                                " VALUES (?, ?, ?, 'write', ?, ?)",
                                (fp, ts, tkey, _sha(body), body),
                            )
                            added += 1
                        elif name == "Edit":
                            conn.execute(
                                "INSERT INTO filestates (path, ts, transcript, kind, old_string, new_string, replace_all)"
                                " VALUES (?, ?, ?, 'edit', ?, ?, ?)",
                                (fp, ts, tkey, str(inp.get("old_string", "")),
                                 str(inp.get("new_string", "")), 1 if inp.get("replace_all") else 0),
                            )
                            added += 1
                        elif name == "Read" and not inp.get("offset") and not inp.get("limit"):
                            pending_reads[block.get("id", "")] = (fp, ts)
                    elif block.get("type") == "tool_result":
                        tuid = block.get("tool_use_id", "")
                        if tuid not in pending_reads:
                            continue
                        fp, req_ts = pending_reads.pop(tuid)
                        text = block.get("content")
                        if isinstance(text, list):
                            text = "".join(x.get("text", "") for x in text if isinstance(x, dict))
                        if not isinstance(text, str) or len(text) > MAX_CONTENT_BYTES:
                            continue
                        body = _read_result_to_content(text)
                        if body is None:
                            continue
                        conn.execute(
                            "INSERT INTO filestates (path, ts, transcript, kind, content_hash, content)"
                            " VALUES (?, ?, ?, 'read', ?, ?)",
                            (fp, ts or req_ts, tkey, _sha(body), body),
                        )
                        added += 1
        conn.execute(
            "INSERT INTO filehist_checkpoints (transcript, byte_offset) VALUES (?, ?)"
            " ON CONFLICT(transcript) DO UPDATE SET byte_offset = excluded.byte_offset",
            (tkey, size),
        )
    conn.commit()
    return added


def versions(store: EpisodicStore, path: str | Path) -> list[FileVersion]:
    conn = _conn(store)
    rows = conn.execute(
        "SELECT path, ts, kind, transcript, content_hash FROM filestates"
        " WHERE path = ? ORDER BY ts, id", (str(path),)
    ).fetchall()
    return [FileVersion(*r) for r in rows]


def reconstruct(store: EpisodicStore, path: str | Path, asof_ts: float) -> str | None:
    """File content as of asof_ts: latest full snapshot at/before it, with
    subsequent edits at/before it replayed on top. None if nothing known."""
    conn = _conn(store)
    rows = conn.execute(
        "SELECT ts, kind, content, old_string, new_string, replace_all FROM filestates"
        " WHERE path = ? AND ts <= ? ORDER BY ts, id", (str(path), asof_ts)
    ).fetchall()
    base = None
    base_i = -1
    for i, (ts, kind, content, *_rest) in enumerate(rows):
        if kind in ("write", "read"):
            base, base_i = content, i
    if base is None:
        return None
    text = base
    for ts, kind, _content, old, new, rall in rows[base_i + 1:]:
        if kind != "edit" or old is None or old not in text:
            continue
        text = text.replace(old, new) if rall else text.replace(old, new, 1)
    return text


def latest_known(store: EpisodicStore, path: str | Path) -> tuple[float, str] | None:
    vs = versions(store, path)
    if not vs:
        return None
    latest_ts = vs[-1].ts
    content = reconstruct(store, path, latest_ts + 1)
    return (latest_ts, content) if content is not None else None


def check_rollbacks(store: EpisodicStore, paths: list[str | Path]) -> list[Incident]:
    """The NFS-incident detector: flag files whose on-disk content matches an
    OLDER transcript-known full state while the transcripts know a newer one.
    A file that merely diverged (hand edits) matches nothing and is fine."""
    conn = _conn(store)
    incidents: list[Incident] = []
    for p in paths:
        p = Path(p)
        if not p.is_file():
            continue
        try:
            disk = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        disk_hash = _sha(disk)
        known = latest_known(store, p)
        if known is None:
            continue
        latest_ts, latest_content = known
        if _sha(latest_content) == disk_hash:
            continue  # clean: disk is at the newest known state
        old_row = conn.execute(
            "SELECT ts FROM filestates WHERE path = ? AND content_hash = ?"
            " ORDER BY ts DESC LIMIT 1", (str(p), disk_hash)
        ).fetchone()
        if old_row and old_row[0] < latest_ts:
            incidents.append(Incident(
                path=str(p), kind="rollback", disk_hash=disk_hash,
                matched_ts=old_row[0], latest_ts=latest_ts,
            ))
    return incidents
