"""Belief-revision ripple: impact analysis for a memory.

The product's hero feature. When a memory is superseded, rejected, or
corrected, this module computes everything downstream that derived from it,
so the user sees "changing your mind about X touches these N things."

Pure graph walking over indexes memsub/store.py and memsub/graph.py already
maintain, plus a read-only scan of distilled topic notes on disk
(memsub/distill.py, imported read-only, never written here). Zero LLM calls,
deterministic, fast: see WORKLOG.md 2026-07-20 "Belief-revision ripple
engine" for the full assumption list this design is built on.

Four derivation-edge tiers, strongest to weakest:
  (a) fusion       -- a "fused_into" event on the memory (memsub.consolidate
                       phase 3): a fusion episode was built citing this
                       memory as a source.
  (b) topic         -- a distilled topic note (memsub/distill.py) lists this
                        memory as a member/source via wikilink.
  (c) supersede      -- this memory superseded something, or was itself
                        superseded by something (memsub.consolidate phases
                        1/2, or a human_correction event).
  (d) associative    -- entity-share neighbors, weighted by co-occurrence.
                        This tier is correlation, not derivation: it is
                        clearly labeled weaker, computed only from the root
                        memory (never chained across hops), and never
                        promoted into the two "derived" tiers.

ripple() walks tiers (a)-(c) breadth-first, up to max_hops, grouping hits
into "derived_directly" (hop 1) and "derived_transitively" (hop >= 2). Tier
(d) is computed once from the root and reported separately as
"associatively_related". ripple_on_event() is the entry point future event
wiring (supersede/reject) will call: it is a one-line wrapper keyed on the
event's episode_id (the affected/loser memory).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .store import Episode, EpisodicStore, MemoryEvent

DEFAULT_VAULT_PATH = Path.home() / "memsub-vault"

TIER_DIRECT = "derived_directly"
TIER_TRANSITIVE = "derived_transitively"
TIER_ASSOCIATIVE = "associatively_related"

# Per-item impact score by tier. Associative additionally scales by
# shared-entity count (see _impact_score); the two "derived" tiers are flat
# per-tier weights, halved per tier so "count-weighted by tier" holds at
# both the item and the report level (total_impact_score is just the sum).
TIER_WEIGHT = {TIER_DIRECT: 3.0, TIER_TRANSITIVE: 1.5, TIER_ASSOCIATIVE: 0.5}

# Associative is the weakest tier by design: cap it so it cannot dominate a
# report (see WORKLOG.md assumption 1d).
ASSOCIATIVE_TOP_N = 10

_TITLE_LEN = 90
_WIKILINK_ID_RE = re.compile(r"\[\[[^\]]*--([0-9a-f]{8})\]\]")
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# Small formatting helpers (self-contained: no coupling to vault_notes'
# title resolver, which is tied to a served-note index object)
# --------------------------------------------------------------------------- #
def _title_for(ep: Episode) -> str:
    text = " ".join(ep.content.split())
    m = re.search(r"[.!?](\s|$)", text)
    sentence = text[: m.end()].strip() if m else text
    if len(sentence) > _TITLE_LEN:
        sentence = sentence[:_TITLE_LEN].rstrip() + "..."
    return sentence or ep.id[:12]


def _item_type(ep: Episode) -> str:
    if ep.kind == "fusion":
        return "fused"
    if ep.kind == "idea":
        return "idea"
    return "memory"


def _ref_title(store: EpisodicStore, episode_id: str) -> str:
    ep = store.get(episode_id)
    return _title_for(ep) if ep is not None else episode_id[:12]


# --------------------------------------------------------------------------- #
# Result shapes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RippleItem:
    id: str
    title: str
    type: str  # memory | fused | topic | idea | unknown
    status: str  # store.status() value, "" for topic notes
    tier: str
    edge_type: str
    hop: int
    path: list[str] = field(default_factory=list)
    impact_score: float = 0.0
    co_occurrence: int | None = None


@dataclass(frozen=True)
class RippleReport:
    memory_id: str
    memory_title: str
    memory_status: str
    max_hops: int
    tiers: dict[str, list[RippleItem]]
    counts: dict[str, int]
    total_impact_score: float

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Edge collection
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _RawEdge:
    target_id: str
    edge_type: str
    explanation: str
    co_occurrence: int | None = None


def _fused_edges(store: EpisodicStore, node_id: str) -> list[_RawEdge]:
    """A memory this node was fused into (memsub.consolidate phase 3)."""
    edges = []
    for ev in store.events(episode_id=node_id, event="fused_into"):
        fused_id = ev.detail.get("by")
        if not fused_id:
            continue
        edges.append(
            _RawEdge(
                fused_id,
                "fused_from",
                f'cited as a source in fused entry "{_ref_title(store, fused_id)}"',
            )
        )
    return sorted(edges, key=lambda e: e.target_id)


def _supersede_edges(
    store: EpisodicStore, node_id: str, all_superseded: list[MemoryEvent]
) -> list[_RawEdge]:
    """Both directions: node_id lost to something (backward), or node_id
    beat something (forward, found in the pre-fetched full event scan --
    there is no reverse index for "what did X win against")."""
    edges = []
    for ev in store.events(episode_id=node_id, event="superseded"):
        winner = ev.detail.get("by")
        if not winner:
            continue
        reason = ev.detail.get("reason", "revision")
        edges.append(
            _RawEdge(
                winner,
                "superseded_by",
                f'superseded by "{_ref_title(store, winner)}" (reason: {reason})',
            )
        )
    for ev in all_superseded:
        if ev.detail.get("by") != node_id:
            continue
        loser = ev.episode_id
        reason = ev.detail.get("reason", "revision")
        edges.append(
            _RawEdge(
                loser,
                "supersedes",
                f'supersedes "{_ref_title(store, loser)}" (reason: {reason})',
            )
        )
    return sorted(edges, key=lambda e: (e.edge_type, e.target_id))


def _topic_edges(node_id: str, topics_index: dict[str, dict]) -> list[_RawEdge]:
    suffix = node_id[:8]
    edges = []
    for slug, info in topics_index.items():
        if suffix in info["member_ids8"]:
            edges.append(
                _RawEdge(
                    f"topic:{slug}",
                    "topic_member",
                    f'listed as a source in topic note "{info["title"]}"',
                )
            )
    return sorted(edges, key=lambda e: e.target_id)


def _associative_edges(
    store: EpisodicStore, memory_id: str, top_n: int = ASSOCIATIVE_TOP_N
) -> list[_RawEdge]:
    """Entity-share neighbors, weighted by count of shared entities. Root
    only -- this tier is never expanded across hops (WORKLOG.md
    assumption 1d): correlation, not derivation."""
    root_entity_ids = {e.id for e in store.entities_for_episode(memory_id)}
    if not root_entity_ids:
        return []
    neighbor_ids = set(store.episode_ids_for_entities(root_entity_ids)) - {memory_id}
    scored: list[tuple[str, int]] = []
    for nid in neighbor_ids:
        neighbor_entity_ids = {e.id for e in store.entities_for_episode(nid)}
        co = len(neighbor_entity_ids & root_entity_ids)
        if co > 0:
            scored.append((nid, co))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    edges = []
    for nid, co in scored[:top_n]:
        plural = "entity" if co == 1 else "entities"
        edges.append(
            _RawEdge(
                nid,
                "entity_share",
                f"shares {co} {plural} with this memory (associative, not derivation)",
                co_occurrence=co,
            )
        )
    return edges


# --------------------------------------------------------------------------- #
# Topic-note index (read-only scan of vault/topics/*.md)
# --------------------------------------------------------------------------- #
def _build_topics_index(vault_path: Path) -> dict[str, dict]:
    """slug -> {"title": str, "member_ids8": set[str]}. Membership is read
    from the wikilinks in the machine-generated block
    (memsub.distill.render_machine_block: `[[<slug>--<id[:8]>]]` per member),
    since the real topic-note frontmatter (memsub.distill.topic_frontmatter)
    carries only member_count/member_hash, no id list."""
    topics_dir = vault_path / "topics"
    index: dict[str, dict] = {}
    if not topics_dir.is_dir():
        return index
    for path in sorted(topics_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        member_ids8 = set(_WIKILINK_ID_RE.findall(text))
        h1 = _H1_RE.search(text)
        title = h1.group(1).strip() if h1 else path.stem
        index[path.stem] = {"title": title, "member_ids8": member_ids8}
    return index


def _topic_item(slug: str, topics_index: dict[str, dict]) -> tuple[str, str]:
    info = topics_index.get(slug, {"title": slug})
    return info["title"], "topic"


# --------------------------------------------------------------------------- #
# Impact scoring
# --------------------------------------------------------------------------- #
def _impact_score(tier: str, co_occurrence: int | None) -> float:
    if tier == TIER_ASSOCIATIVE:
        return round(TIER_WEIGHT[TIER_ASSOCIATIVE] * (co_occurrence or 1), 3)
    return TIER_WEIGHT[tier]


def _build_item(
    store: EpisodicStore,
    target_id: str,
    edge_type: str,
    tier: str,
    hop: int,
    path: list[str],
    topics_index: dict[str, dict],
    co_occurrence: int | None = None,
) -> RippleItem:
    if target_id.startswith("topic:"):
        title, item_type = _topic_item(target_id[len("topic:") :], topics_index)
        status = ""
    else:
        ep = store.get(target_id)
        if ep is None:
            title, item_type, status = target_id[:12], "unknown", "missing"
        else:
            title, item_type, status = _title_for(ep), _item_type(ep), store.status(target_id)
    return RippleItem(
        id=target_id,
        title=title,
        type=item_type,
        status=status,
        tier=tier,
        edge_type=edge_type,
        hop=hop,
        path=path,
        impact_score=_impact_score(tier, co_occurrence),
        co_occurrence=co_occurrence,
    )


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def ripple(
    store: EpisodicStore,
    memory_id: str,
    max_hops: int = 2,
    vault_path: str | Path | None = None,
) -> RippleReport:
    """Impact analysis for one memory: everything derived from it, grouped
    by tier, with a plain-words explanation of how each item connects back.

    Deterministic and LLM-free: same store state always produces the same
    report. Raises ValueError for an unknown memory id or a non-positive
    max_hops, rather than silently returning an empty report for bad input.
    """
    root = store.get(memory_id)
    if root is None:
        raise ValueError(f"unknown memory id: {memory_id}")
    if max_hops < 1:
        raise ValueError("max_hops must be >= 1")

    vpath = Path(vault_path) if vault_path is not None else DEFAULT_VAULT_PATH
    topics_index = _build_topics_index(vpath)
    all_superseded = store.events(event="superseded")

    visited = {memory_id}
    tiers: dict[str, list[RippleItem]] = {TIER_DIRECT: [], TIER_TRANSITIVE: []}

    frontier: list[tuple[str, list[str]]] = [(memory_id, [])]
    hop = 0
    while hop < max_hops and frontier:
        hop += 1
        tier = TIER_DIRECT if hop == 1 else TIER_TRANSITIVE
        expansions: list[tuple[str, list[str], _RawEdge]] = []
        for node_id, path in frontier:
            edges = (
                _fused_edges(store, node_id)
                + _topic_edges(node_id, topics_index)
                + _supersede_edges(store, node_id, all_superseded)
            )
            for edge in sorted(edges, key=lambda e: (e.edge_type, e.target_id)):
                expansions.append((node_id, path, edge))
        next_frontier: list[tuple[str, list[str]]] = []
        for node_id, path, edge in expansions:
            target = edge.target_id
            if target in visited:
                continue
            visited.add(target)
            new_path = path + [edge.explanation]
            item = _build_item(store, target, edge.edge_type, tier, hop, new_path, topics_index)
            tiers[tier].append(item)
            next_frontier.append((target, new_path))
        frontier = next_frontier

    associative_items = []
    for edge in _associative_edges(store, memory_id):
        if edge.target_id in visited:
            continue
        visited.add(edge.target_id)
        item = _build_item(
            store,
            edge.target_id,
            edge.edge_type,
            TIER_ASSOCIATIVE,
            1,
            [edge.explanation],
            topics_index,
            co_occurrence=edge.co_occurrence,
        )
        associative_items.append(item)
    tiers[TIER_ASSOCIATIVE] = associative_items

    for items in tiers.values():
        items.sort(key=lambda it: (-it.impact_score, it.id))

    counts = {t: len(v) for t, v in tiers.items()}
    counts["total"] = sum(counts.values())
    total_impact = round(sum(it.impact_score for items in tiers.values() for it in items), 3)

    return RippleReport(
        memory_id=memory_id,
        memory_title=_title_for(root),
        memory_status=store.status(memory_id),
        max_hops=max_hops,
        tiers=tiers,
        counts=counts,
        total_impact_score=total_impact,
    )


TIER_LABELS = {
    TIER_DIRECT: "Derived directly",
    TIER_TRANSITIVE: "Derived transitively",
    TIER_ASSOCIATIVE: "Associatively related (correlation, not derivation)",
}


def render_report(report: RippleReport) -> str:
    """Plain-text rendering for a CLI/demo surface (mirrors memsub.audit's
    render_audit style: one function, reused everywhere a human reads a
    report instead of a UI panel consuming the JSON directly)."""
    lines = [
        f"ripple: {report.memory_title}",
        f"  memory_id:    {report.memory_id}",
        f"  status:       {report.memory_status}",
        f"  max_hops:     {report.max_hops}",
        f"  total impact: {report.total_impact_score}  "
        f"({report.counts['total']} affected item(s))",
    ]
    for tier in (TIER_DIRECT, TIER_TRANSITIVE, TIER_ASSOCIATIVE):
        items = report.tiers.get(tier, [])
        lines.append(f"\n  {TIER_LABELS[tier]} ({len(items)}):")
        if not items:
            lines.append("    (none)")
            continue
        for item in items:
            co = ""
            if item.co_occurrence:
                plural = "entity" if item.co_occurrence == 1 else "entities"
                co = f", shares {item.co_occurrence} {plural}"
            lines.append(
                f"    [{item.type}] {item.title}  "
                f"(impact {item.impact_score}{co}, status: {item.status or 'n/a'})"
            )
            for step in item.path:
                lines.append(f"        -> {step}")
    return "\n".join(lines)


def ripple_on_event(
    store: EpisodicStore,
    event: MemoryEvent,
    max_hops: int = 2,
    vault_path: str | Path | None = None,
) -> RippleReport:
    """Entry point for future event wiring: given a supersede/reject/
    correction event, produce the same ripple report for the affected
    (loser) memory's id. One-line wrapper -- event.episode_id is always the
    memory the event was logged against (memsub/store.py: log_event is
    called on the loser, never the winner)."""
    return ripple(store, event.episode_id, max_hops=max_hops, vault_path=vault_path)
