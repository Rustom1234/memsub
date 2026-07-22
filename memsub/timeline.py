"""Belief timelines and point-in-time ("as-of") recall.

F1 of the overnight build (docs/eng-review-2026-07-21.md lineage,
docs/research-memory-landscape-2026-07-21.md rationale): the answer to
"what did I believe on date D, and how did that belief change?", computed
from primitives the store already maintains: bi-temporal validity windows
(valid_from/valid_to, world time), story-time `ts`, and the supersede
event log. Zep/Graphiti markets this model; here it runs locally over
SQLite with signed-channel provenance on every step.

Semantics:
- A belief chain is the maximal supersede path through an episode
  (predecessors: episodes whose `superseded` event names it in detail.by;
  successors: its own `superseded` event's detail.by), ordered oldest
  first.
- status_at(ep, asof): "believed" | "not_yet_known" | "superseded", judged
  by validity windows when present, else by story time + supersede-event
  transaction time. A belief later revised is still "believed" at dates
  before the revision: as-of queries deliberately resurface superseded
  memories, because that is what you believed then.
- as_of_recall: BM25 over the believed-at-D world. Lexical-only by design
  in v1: building a dense index per as-of date would cost minutes of
  embedding per query; BM25 over a few thousand episodes is instant and
  the temporal filter is doing the heavy lifting.
"""

from __future__ import annotations

from .lexical import BM25Index
from .store import Episode, EpisodicStore

BELIEVED = "believed"
NOT_YET_KNOWN = "not_yet_known"
SUPERSEDED = "superseded"


def _supersede_events(store: EpisodicStore) -> list:
    return store.events(event="superseded")


def belief_chain(store: EpisodicStore, episode_id: str) -> list[Episode]:
    """The full revision history through episode_id, oldest first."""
    events = _supersede_events(store)
    by_loser = {ev.episode_id: ev.detail.get("by") for ev in events}
    losers_of = {}
    for ev in events:
        winner = ev.detail.get("by")
        if winner:
            losers_of.setdefault(winner, []).append(ev.episode_id)

    # walk backward to the root
    root = episode_id
    seen = {root}
    while True:
        preds = [l for l in losers_of.get(root, []) if l not in seen]
        if not preds:
            break
        # multiple predecessors (fusion-style merges) pick the oldest
        preds_eps = [store.get(p) for p in preds]
        preds_eps = [p for p in preds_eps if p is not None]
        if not preds_eps:
            break
        root = min(preds_eps, key=lambda e: e.ts).id
        seen.add(root)

    # walk forward from the root
    chain_ids = [root]
    cur = root
    while True:
        nxt = by_loser.get(cur)
        if not nxt or nxt in chain_ids:
            break
        chain_ids.append(nxt)
        cur = nxt

    chain = [store.get(i) for i in chain_ids]
    return [e for e in chain if e is not None]


def status_at(store: EpisodicStore, episode_id: str, asof_ts: float) -> str:
    ep = store.get(episode_id)
    if ep is None:
        return NOT_YET_KNOWN
    if ep.valid_from is not None and asof_ts < ep.valid_from:
        return NOT_YET_KNOWN
    if ep.valid_from is None and asof_ts < ep.ts:
        return NOT_YET_KNOWN
    if ep.valid_to is not None and asof_ts >= ep.valid_to:
        return SUPERSEDED
    if ep.valid_to is None:
        # no world-time window close: fall back to supersede-event
        # transaction time (conservative: before the event, it was believed)
        for ev in store.events(episode_id=episode_id, event="superseded"):
            if ev.ts <= asof_ts:
                return SUPERSEDED
    return BELIEVED


def believed_at(
    store: EpisodicStore, asof_ts: float, namespace: str | None = None
) -> list[Episode]:
    """Every episode that was believed at asof, INCLUDING later-superseded
    ones (that is the point) but excluding quarantined/forgotten."""
    out = []
    for ep in store.all(namespace=namespace) if namespace else store.all():
        st = store.status(ep.id)
        if st in ("quarantined", "forgotten"):
            continue
        if status_at(store, ep.id, asof_ts) == BELIEVED:
            out.append(ep)
    return out


def as_of_recall(
    store: EpisodicStore,
    query: str,
    asof_ts: float,
    k: int = 6,
    namespace: str | None = None,
) -> list[Episode]:
    world = believed_at(store, asof_ts, namespace=namespace)
    if not world:
        return []
    index = BM25Index([ep.content for ep in world])
    scores = index.scores(query)
    ranked = sorted(range(len(world)), key=lambda i: scores[i], reverse=True)
    return [world[i] for i in ranked[:k] if scores[i] > 0.0]


def render_chain(store: EpisodicStore, chain: list[Episode]) -> str:
    """Terminal rendering of a belief timeline with validity + provenance."""
    import datetime

    lines = []
    for i, ep in enumerate(chain):
        day = datetime.date.fromtimestamp(ep.ts).isoformat()
        window = ""
        if ep.valid_from is not None:
            vf = datetime.date.fromtimestamp(ep.valid_from).isoformat()
            vt = (datetime.date.fromtimestamp(ep.valid_to).isoformat()
                  if ep.valid_to is not None else "present")
            window = f"  [valid {vf} -> {vt}]"
        channel = (ep.provenance or {}).get("channel", "unsigned")
        marker = "*" if i == len(chain) - 1 else " "
        lines.append(f" {marker} {day}{window}  ({channel})  {ep.content}")
        if i < len(chain) - 1:
            lines.append("   |")
    return "\n".join(lines)
