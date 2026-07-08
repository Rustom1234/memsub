"""Namespaces: scoping and isolation for one shared store.

A namespace is a string that partitions the episode table. Conventions (not
enforced): "user:<id>", "agent:<name>", "session:<id>", "shared:<team>". One
SQLite file can hold memories for several users, agents, and sessions, plus
shared namespaces that multi-agent teams read and write together.

Access model:

- An UNREGISTERED namespace is open: anyone can read and write. This keeps
  zero-config single-agent use working with no ceremony.
- A REGISTERED namespace is closed: the owner has read+write, everyone else
  needs an explicit grant (read and/or write per agent).

Honest scope: this is isolation and accident prevention between cooperating
agents sharing one local file, not a security boundary. Any process that can
open the SQLite file can bypass it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .store import Episode, EpisodicStore

_NS_SCHEMA = """
CREATE TABLE IF NOT EXISTS namespaces (
    name TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS namespace_grants (
    namespace TEXT NOT NULL,
    agent TEXT NOT NULL,
    can_read INTEGER NOT NULL DEFAULT 1,
    can_write INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (namespace, agent)
);
"""


class PermissionDenied(Exception):
    """An agent tried to read or write a namespace it has no grant for."""


@dataclass(frozen=True)
class Grant:
    namespace: str
    agent: str
    can_read: bool
    can_write: bool


class NamespaceManager:
    """Registry and ACL for namespaces, stored in the same SQLite file."""

    def __init__(self, store: EpisodicStore) -> None:
        self._store = store
        self._conn = store._conn
        self._conn.executescript(_NS_SCHEMA)
        self._conn.commit()

    # -- registry ---------------------------------------------------------- #
    def create(self, name: str, owner: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO namespaces (name, owner, created_ts) VALUES (?, ?, ?)",
            (name, owner, time.time()),
        )
        self._conn.commit()

    def owner(self, name: str) -> str | None:
        row = self._conn.execute("SELECT owner FROM namespaces WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def list(self) -> list[str]:
        rows = self._conn.execute("SELECT name FROM namespaces ORDER BY name").fetchall()
        return [r[0] for r in rows]

    # -- grants -------------------------------------------------------------- #
    def grant(self, name: str, agent: str, read: bool = True, write: bool = False) -> None:
        if self.owner(name) is None:
            raise ValueError(f"namespace {name!r} is not registered; create it first")
        self._conn.execute(
            "INSERT OR REPLACE INTO namespace_grants (namespace, agent, can_read, can_write)"
            " VALUES (?, ?, ?, ?)",
            (name, agent, int(read), int(write)),
        )
        self._conn.commit()

    def revoke(self, name: str, agent: str) -> None:
        self._conn.execute(
            "DELETE FROM namespace_grants WHERE namespace = ? AND agent = ?", (name, agent)
        )
        self._conn.commit()

    def grants(self, name: str) -> list[Grant]:
        rows = self._conn.execute(
            "SELECT namespace, agent, can_read, can_write FROM namespace_grants"
            " WHERE namespace = ? ORDER BY agent",
            (name,),
        ).fetchall()
        return [Grant(r[0], r[1], bool(r[2]), bool(r[3])) for r in rows]

    # -- checks -------------------------------------------------------------- #
    def can(self, agent: str, action: str, name: str) -> bool:
        """action: "read" | "write". Unregistered namespaces are open."""
        owner = self.owner(name)
        if owner is None:
            return True
        if owner == agent:
            return True
        row = self._conn.execute(
            "SELECT can_read, can_write FROM namespace_grants WHERE namespace = ? AND agent = ?",
            (name, agent),
        ).fetchone()
        if row is None:
            return False
        return bool(row[0]) if action == "read" else bool(row[1])

    def check(self, agent: str, action: str, name: str) -> None:
        if not self.can(agent, action, name):
            raise PermissionDenied(f"agent {agent!r} may not {action} namespace {name!r}")

    def readable_by(self, agent: str) -> list[str]:
        """Registered namespaces this agent can read (open namespaces are not enumerable)."""
        return [n for n in self.list() if self.can(agent, "read", n)]


class MemoryScope:
    """A (store, agent, namespace) handle that enforces the ACL on every call."""

    def __init__(self, store: EpisodicStore, manager: NamespaceManager, agent: str, namespace: str) -> None:
        self.store = store
        self.manager = manager
        self.agent = agent
        self.namespace = namespace

    def append(self, episode: Episode) -> Episode:
        self.manager.check(self.agent, "write", self.namespace)
        ep = Episode(
            content=episode.content,
            run_id=episode.run_id,
            session_id=episode.session_id,
            kind=episode.kind,
            importance=episode.importance,
            provenance={**episode.provenance, "written_by": self.agent},
            namespace=self.namespace,
            ts=episode.ts,
            id=episode.id,
        )
        return self.store.append(ep)

    def active(self, kind: str | None = None) -> list[Episode]:
        self.manager.check(self.agent, "read", self.namespace)
        return self.store.active(kind=kind, namespace=self.namespace)

    def all(self, kind: str | None = None) -> list[Episode]:
        self.manager.check(self.agent, "read", self.namespace)
        return self.store.all(kind=kind, namespace=self.namespace)
