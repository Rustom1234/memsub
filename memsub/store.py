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
    provenance TEXT NOT NULL DEFAULT '{}',
    namespace TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_episodes_namespace ON episodes(namespace, ts);
CREATE INDEX IF NOT EXISTS idx_episodes_run ON episodes(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
CREATE TABLE IF NOT EXISTS memory_events (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    episode_id TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_episode ON memory_events(episode_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON memory_events(ts);
"""

# Events that end an episode's active life. The episode row is never deleted;
# status is derived from the event log, which is the audit trail.
TERMINAL_EVENTS = ("superseded", "forgotten")


@dataclass(frozen=True)
class MemoryEvent:
    """One lifecycle or usage event for an episode.

    event: "superseded" | "forgotten" | "retrieved" | "injected" (open set).
    detail: structured context, e.g. {"reason": ..., "by": <episode_id>} for
    supersede, {"query": ..., "score": ...} for retrieval.
    """

    episode_id: str
    event: str
    detail: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0
    id: str = ""


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
    namespace: str = "default"
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
            namespace=self.namespace,
            ts=ts,
            id=ep_id,
        )


class EpisodicStore:
    """Append-only SQLite episode log."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._migrate()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first release to pre-existing DBs."""
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='episodes'"
        ).fetchone()
        if row is None:
            return
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(episodes)")}
        if "namespace" not in cols:
            self._conn.execute(
                "ALTER TABLE episodes ADD COLUMN namespace TEXT NOT NULL DEFAULT 'default'"
            )
            self._conn.commit()

    def append(self, episode: Episode) -> Episode:
        ep = episode.with_defaults()
        self._conn.execute(
            "INSERT INTO episodes (id, ts, run_id, session_id, kind, content, importance, provenance, namespace)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ep.id,
                ep.ts,
                ep.run_id,
                ep.session_id,
                ep.kind,
                ep.content,
                ep.importance,
                json.dumps(ep.provenance, sort_keys=True),
                ep.namespace,
            ),
        )
        self._conn.commit()
        return ep

    def get(self, episode_id: str) -> Episode | None:
        row = self._conn.execute(
            "SELECT id, ts, run_id, session_id, kind, content, importance, provenance, namespace"
            " FROM episodes WHERE id = ?",
            (episode_id,),
        ).fetchone()
        return self._row_to_episode(row) if row else None

    def all(
        self,
        run_id: str | None = None,
        kind: str | None = None,
        namespace: str | None = None,
    ) -> list[Episode]:
        """Episodes in insertion (timestamp) order, oldest first."""
        query = (
            "SELECT id, ts, run_id, session_id, kind, content, importance, provenance, namespace"
            " FROM episodes WHERE 1=1"
        )
        params: list[Any] = []
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if namespace is not None:
            query += " AND namespace = ?"
            params.append(namespace)
        query += " ORDER BY ts ASC, id ASC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_episode(r) for r in rows]

    def count(self, run_id: str | None = None) -> int:
        if run_id is None:
            return self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        return self._conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE run_id = ?", (run_id,)
        ).fetchone()[0]

    # ------------------------------------------------------------------ #
    # Event log: lifecycle (supersede/forget) and usage (retrieve/inject)
    # ------------------------------------------------------------------ #
    def log_event(self, episode_id: str, event: str, detail: dict[str, Any] | None = None) -> MemoryEvent:
        ev = MemoryEvent(
            episode_id=episode_id,
            event=event,
            detail=detail or {},
            ts=time.time(),
            id=uuid.uuid4().hex,
        )
        self._conn.execute(
            "INSERT INTO memory_events (id, ts, episode_id, event, detail) VALUES (?, ?, ?, ?, ?)",
            (ev.id, ev.ts, ev.episode_id, ev.event, json.dumps(ev.detail, sort_keys=True)),
        )
        self._conn.commit()
        return ev

    def events(
        self,
        episode_id: str | None = None,
        event: str | None = None,
        limit: int | None = None,
    ) -> list[MemoryEvent]:
        """Events newest first, optionally filtered by episode or event type."""
        query = "SELECT id, ts, episode_id, event, detail FROM memory_events WHERE 1=1"
        params: list[Any] = []
        if episode_id is not None:
            query += " AND episode_id = ?"
            params.append(episode_id)
        if event is not None:
            query += " AND event = ?"
            params.append(event)
        query += " ORDER BY ts DESC, id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [
            MemoryEvent(id=r[0], ts=r[1], episode_id=r[2], event=r[3], detail=json.loads(r[4]))
            for r in rows
        ]

    def status(self, episode_id: str) -> str:
        """"active", or the terminal event name ("superseded"/"forgotten")."""
        row = self._conn.execute(
            "SELECT event FROM memory_events WHERE episode_id = ? AND event IN (?, ?)"
            " ORDER BY ts DESC LIMIT 1",
            (episode_id, *TERMINAL_EVENTS),
        ).fetchone()
        return row[0] if row else "active"

    def active(
        self,
        run_id: str | None = None,
        kind: str | None = None,
        namespace: str | None = None,
    ) -> list[Episode]:
        """Episodes with no terminal event, oldest first."""
        query = (
            "SELECT id, ts, run_id, session_id, kind, content, importance, provenance, namespace"
            " FROM episodes WHERE id NOT IN"
            " (SELECT episode_id FROM memory_events WHERE event IN (?, ?))"
        )
        params: list[Any] = list(TERMINAL_EVENTS)
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if namespace is not None:
            query += " AND namespace = ?"
            params.append(namespace)
        query += " ORDER BY ts ASC, id ASC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_episode(r) for r in rows]

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
            namespace=row[8] if len(row) > 8 else "default",
        )
