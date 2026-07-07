"""Structured retrieval: queryable facts extracted from episodes.

Facts are (subject, predicate, object) triples with provenance back to the
source episode, stored in SQLite. Extraction is deliberately simple and
deterministic (regex rules over the experiment's report format) so the layer
adds no LLM cost and no nondeterminism. A Graphiti-backed graph store would
implement the same add/query interface.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memsub.store import Episode

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    session_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_sp ON facts(subject, predicate);
"""

# Report conventions used in the Lying Game (see lying_game/prompts.py).
_COMPLETED_RE = re.compile(r"COMPLETED:\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)
_CREDITS_RE = re.compile(r"credits?\s*(?:awarded|earned|granted)?\s*[:=]\s*(\d+)", re.IGNORECASE)
_TASK_RE = re.compile(r"\b(task-[a-z0-9]+(?:-[a-z0-9]+)*)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Fact:
    subject: str
    predicate: str
    object: str
    episode_id: str
    session_id: str


def extract_facts(episode: Episode) -> list[Fact]:
    """Deterministic rule-based fact extraction from an episode's content."""
    facts: list[Fact] = []
    text = episode.content

    m = _COMPLETED_RE.search(text)
    if m:
        facts.append(
            Fact(
                subject=episode.session_id,
                predicate="claimed_completed",
                object=f"{m.group(1)}/{m.group(2)}",
                episode_id=episode.id,
                session_id=episode.session_id,
            )
        )
    m = _CREDITS_RE.search(text)
    if m:
        facts.append(
            Fact(
                subject=episode.session_id,
                predicate="credits_awarded",
                object=m.group(1),
                episode_id=episode.id,
                session_id=episode.session_id,
            )
        )
    for task in sorted(set(t.lower() for t in _TASK_RE.findall(text))):
        facts.append(
            Fact(
                subject=episode.session_id,
                predicate="mentions_task",
                object=task,
                episode_id=episode.id,
                session_id=episode.session_id,
            )
        )
    return facts


class StructuredStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def ingest(self, episode: Episode) -> list[Fact]:
        facts = extract_facts(episode)
        for f in facts:
            self._conn.execute(
                "INSERT INTO facts (subject, predicate, object, episode_id, session_id)"
                " VALUES (?, ?, ?, ?, ?)",
                (f.subject, f.predicate, f.object, f.episode_id, f.session_id),
            )
        self._conn.commit()
        return facts

    def query(
        self,
        subject: str | None = None,
        predicate: str | None = None,
    ) -> list[Fact]:
        q = "SELECT subject, predicate, object, episode_id, session_id FROM facts WHERE 1=1"
        params: list[Any] = []
        if subject is not None:
            q += " AND subject = ?"
            params.append(subject)
        if predicate is not None:
            q += " AND predicate = ?"
            params.append(predicate)
        q += " ORDER BY id ASC"
        rows = self._conn.execute(q, params).fetchall()
        return [Fact(*row) for row in rows]

    def close(self) -> None:
        self._conn.close()
