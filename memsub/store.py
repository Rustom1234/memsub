"""Append-only episodic store backed by SQLite.

Episodes are first-class objects with timestamps, session ids, and provenance.
The store never mutates or deletes rows: memory writes are appends, which
keeps the audit trail intact (a design requirement for the safety experiment,
where we need to inspect exactly what the agent could remember at any point).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 1.0,
    provenance TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_episodes_run ON episodes(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
"""


@dataclass(frozen=True)
class Episode:
    """One remembered event.

    kind: free-form category tag, e.g. "session_summary", "seeded", "report".
    importance: scalar in (0, inf), used as a retrieval weight.
    provenance: where this memory came from (writer, transform applied, source ids).
    """

    content: str
    run_id: str
    session_id: str
    kind: str = "event"
    importance: float = 1.0
    provenance: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0
    id: str = ""

    def with_defaults(self) -> "Episode":
        ep_id = self.id or uuid.uuid4().hex
        ts = self.ts if self.ts > 0 else time.time()
        return Episode(
            content=self.content,
            run_id=self.run_id,
            session_id=self.session_id,
            kind=self.kind,
            importance=self.importance,
            provenance=self.provenance,
            ts=ts,
            id=ep_id,
        )


class EpisodicStore:
    """Append-only SQLite episode log."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def append(self, episode: Episode) -> Episode:
        ep = episode.with_defaults()
        self._conn.execute(
            "INSERT INTO episodes (id, ts, run_id, session_id, kind, content, importance, provenance)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ep.id,
                ep.ts,
                ep.run_id,
                ep.session_id,
                ep.kind,
                ep.content,
                ep.importance,
                json.dumps(ep.provenance, sort_keys=True),
            ),
        )
        self._conn.commit()
        return ep

    def get(self, episode_id: str) -> Episode | None:
        row = self._conn.execute(
            "SELECT id, ts, run_id, session_id, kind, content, importance, provenance"
            " FROM episodes WHERE id = ?",
            (episode_id,),
        ).fetchone()
        return self._row_to_episode(row) if row else None

    def all(self, run_id: str | None = None, kind: str | None = None) -> list[Episode]:
        """Episodes in insertion (timestamp) order, oldest first."""
        query = (
            "SELECT id, ts, run_id, session_id, kind, content, importance, provenance"
            " FROM episodes WHERE 1=1"
        )
        params: list[Any] = []
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY ts ASC, id ASC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_episode(r) for r in rows]

    def count(self, run_id: str | None = None) -> int:
        if run_id is None:
            return self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        return self._conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE run_id = ?", (run_id,)
        ).fetchone()[0]

    def __iter__(self) -> Iterator[Episode]:
        return iter(self.all())

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_episode(row: tuple) -> Episode:
        return Episode(
            id=row[0],
            ts=row[1],
            run_id=row[2],
            session_id=row[3],
            kind=row[4],
            content=row[5],
            importance=row[6],
            provenance=json.loads(row[7]),
        )
