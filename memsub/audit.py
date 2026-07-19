"""Audit: why a memory exists, when it was used, and what context it entered.

Everything here reads the append-only episode table plus the memory_events
log. Writers of retrieval paths call log_recall() so that every retrieval and
injection is recorded from the moment it happens; the CLI (memsub audit)
renders the trail.

Event vocabulary (store.MemoryEvent.event):
  retrieved   the memory surfaced in a search (detail: query, score, rank)
  injected    the memory made it into injected context (detail: query, position, budget)
  superseded  a consolidation or update decision replaced it (detail: reason, by)
  forgotten   decay dropped it (detail: reason, value, age_days)
  quarantined a write-channel signature failed verification (memsub.provenance,
              memsub.consolidate): detail reason=signature_invalid, never deleted
"""

from __future__ import annotations

import time
from typing import Any

from .store import Episode, EpisodicStore


def log_recall(
    store: EpisodicStore,
    query: str,
    scored: list[tuple[Episode, float]],
    injected: list[Episode],
    budget_tokens: int | None = None,
) -> None:
    """Record one recall: which episodes surfaced and which entered context."""
    for rank, (ep, score) in enumerate(scored):
        store.log_event(
            ep.id,
            "retrieved",
            {"query": query, "score": round(float(score), 6), "rank": rank},
        )
    for position, ep in enumerate(injected):
        detail: dict[str, Any] = {"query": query, "position": position}
        if budget_tokens is not None:
            detail["budget_tokens"] = budget_tokens
        store.log_event(ep.id, "injected", detail)


def audit_episode(store: EpisodicStore, episode_id: str) -> dict[str, Any] | None:
    """Full audit record for one memory: the episode, provenance, status, events."""
    ep = store.get(episode_id)
    if ep is None:
        return None
    events = store.events(episode_id=episode_id)
    return {
        "episode": ep,
        "status": store.status(episode_id),
        "provenance": ep.provenance,
        "events": events,
        "counts": {
            "retrieved": sum(1 for e in events if e.event == "retrieved"),
            "injected": sum(1 for e in events if e.event == "injected"),
        },
    }


def recent_activity(store: EpisodicStore, limit: int = 20) -> list[dict[str, Any]]:
    """Most recent memory events joined with episode snippets, newest first."""
    rows = []
    for ev in store.events(limit=limit):
        ep = store.get(ev.episode_id)
        rows.append(
            {
                "event": ev,
                "content": (ep.content if ep else "(missing episode)"),
                "namespace": (ep.namespace if ep else "?"),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Rendering (plain text for the CLI)
# --------------------------------------------------------------------------- #
def _ts(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def render_audit(record: dict[str, Any]) -> str:
    ep: Episode = record["episode"]
    lines = [
        f"memory {ep.id}",
        f"  status:     {record['status']}",
        f"  namespace:  {ep.namespace}",
        f"  kind:       {ep.kind}   importance: {ep.importance}",
        f"  written:    {_ts(ep.ts)}",
        f"  content:    {ep.content}",
    ]
    if record["provenance"]:
        lines.append("  provenance (why it was written):")
        for k, v in sorted(record["provenance"].items()):
            lines.append(f"    {k}: {v}")
    counts = record["counts"]
    lines.append(
        f"  usage: retrieved {counts['retrieved']} time(s), injected {counts['injected']} time(s)"
    )
    if record["events"]:
        lines.append("  events (newest first):")
        for ev in record["events"]:
            detail = " ".join(f"{k}={v}" for k, v in sorted(ev.detail.items()))
            lines.append(f"    {_ts(ev.ts)}  {ev.event:<10} {detail}")
    else:
        lines.append("  events: none recorded")
    return "\n".join(lines)


def render_recent(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no memory activity recorded"
    lines = []
    for row in rows:
        ev = row["event"]
        snippet = row["content"][:70]
        lines.append(
            f"{_ts(ev.ts)}  {ev.event:<10} [{row['namespace']}] {ev.episode_id[:12]}  {snippet}"
        )
    return "\n".join(lines)
