"""Layer 2: the graph. Builds an in-memory node/edge graph from vault
markdown text -- no store/database access (see WORKLOG.md 2026-07-20
assumption 2 for why: memsub/vault.py's render_note() already writes every
entity relationship as a literal `[[wikilink]]` in each note's `## Links`
section, the same source memsub/graph.py's DB backfill reads from, so
parsing vault text keeps this module store-optional and reuses the same
"walk raw text" precedent memsub/vault_serve.py's `_supersede_links`
already sets).

Built once at server start (`build_graph_index`), same "no live reload"
contract the note index already has -- `/api/graph.json` filters and caps an
already-built graph, it never re-scans the vault per request.

Node kinds today: "note" (small, dim) and "hub" (large, accent -- the
biggest entities). No "topic" kind yet: Layer 1 (distillation) hasn't run
against this vault (no topics/ directory exists), so topic nodes are not
built speculatively -- see docs/second-brain-spec-2026-07-20.md Layer 2 and
WORKLOG.md assumption 1.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

_SHARES_ENTITY_RE = re.compile(r"^- shares entity \*\*([^*]+)\*\* with \[\[([^\]]+)\]\]\s*$", re.MULTILINE)
_WIKILINK_TARGET_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]")

DEFAULT_TOP_ENTITIES = 40
DEFAULT_NODE_LIMIT = 150
MAX_NODE_LIMIT = 1000


@dataclass
class GraphNode:
    id: str  # "note:<relpath>" or "hub:<entity name>"
    kind: str  # "note" | "hub"
    label: str
    namespace: str = ""
    ts: float = 0.0
    degree: int = 0


@dataclass
class GraphEdge:
    source: str
    target: str
    weight: int = 1


@dataclass
class GraphIndex:
    """Full precomputed graph. `notes`: relpath -> GraphNode (kind="note").
    `hubs`: entity name -> GraphNode (kind="hub"), already limited to the
    top DEFAULT_TOP_ENTITIES by member count. `edges`: note<->note wikilink
    edges plus hub<->note membership edges. `entity_notes`: entity name ->
    set of relpaths, kept for Layer 3 exclusion (already-linked pairs) and
    the dashboard's "what your brain knows" entity chips."""

    notes: dict[str, GraphNode] = field(default_factory=dict)
    hubs: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    entity_notes: dict[str, set[str]] = field(default_factory=dict)


def _resolve(target: str, idx) -> str | None:
    from .vault_serve import resolve_wikilink

    return resolve_wikilink(target, idx)


def build_graph_index(idx, top_entities: int = DEFAULT_TOP_ENTITIES) -> GraphIndex:
    graph = GraphIndex()

    # note nodes, from the same source the rest of vault_serve uses.
    # Structural pages (namespace/root index.md, review-queue.md, templates)
    # are excluded via is_content_note -- found live against the real vault:
    # claude-code/index.md links to all 4,330 notes (a table of contents,
    # not a memory relationship) and dominated connectivity ranking before
    # this filter (WORKLOG.md 2026-07-20, "index.md pollution" finding).
    from .vault_notes import is_content_note, note_title

    content_notes = {rel for rel in idx.notes if is_content_note(rel, idx)}

    for rel in content_notes:
        fields = idx.frontmatter.get(rel, {})
        ts = _parse_iso(fields.get("created", ""))
        graph.notes[rel] = GraphNode(
            id=f"note:{rel}",
            kind="note",
            label=note_title(rel, idx),
            namespace=fields.get("namespace", ""),
            ts=ts,
        )

    # note<->note wikilink edges (dedup by unordered pair, weight = number
    # of distinct link lines found between them -- entity-share lines and
    # editorial links both count, since both are real relationships)
    pair_weight: Counter[tuple[str, str]] = Counter()
    entity_notes: dict[str, set[str]] = defaultdict(set)

    for rel in content_notes:
        text = idx.notes[rel]
        for m in _WIKILINK_TARGET_RE.finditer(text):
            resolved = _resolve(m.group(1), idx)
            if resolved is None or resolved == rel or resolved not in content_notes:
                continue
            pair_weight[tuple(sorted((rel, resolved)))] += 1
        for m in _SHARES_ENTITY_RE.finditer(text):
            entity, target = m.group(1), m.group(2)
            resolved = _resolve(target, idx)
            entity_notes[entity].add(rel)
            if resolved is not None and resolved in content_notes:
                entity_notes[entity].add(resolved)

    for (a, b), weight in pair_weight.items():
        if a in graph.notes and b in graph.notes:
            graph.edges.append(GraphEdge(source=f"note:{a}", target=f"note:{b}", weight=weight))
            graph.notes[a].degree += 1
            graph.notes[b].degree += 1

    graph.entity_notes = dict(entity_notes)

    # hub nodes: top-N entities by member-note count
    ranked = sorted(entity_notes.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    for entity, members in ranked[:top_entities]:
        hub_id = f"hub:{entity}"
        graph.hubs[entity] = GraphNode(id=hub_id, kind="hub", label=entity, degree=len(members))
        for rel in members:
            if rel not in graph.notes:
                continue
            graph.edges.append(GraphEdge(source=hub_id, target=f"note:{rel}", weight=1))
            graph.notes[rel].degree += 1

    return graph


def _parse_iso(created: str) -> float:
    if not created:
        return 0.0
    try:
        return datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
    except ValueError:
        return 0.0


def _period_cutoff(period: str) -> float:
    now = datetime.now()
    if period == "week":
        return (now - timedelta(days=7)).timestamp()
    if period == "month":
        return (now - timedelta(days=30)).timestamp()
    return 0.0


def filter_and_cap(
    graph: GraphIndex,
    namespace: str = "",
    period: str = "",
    limit: int = DEFAULT_NODE_LIMIT,
) -> dict:
    """Apply namespace/time filters, then cap to the top `limit` notes by
    degree (connectivity) plus every hub connected to a surviving note.
    Returns a plain-dict payload ready for JSON, with honest truncation
    counts so the UI can say "showing top N of M"."""
    limit = max(1, min(limit, MAX_NODE_LIMIT))
    cutoff = _period_cutoff(period)

    candidates = [
        n
        for n in graph.notes.values()
        if (not namespace or n.namespace == namespace) and (not cutoff or n.ts >= cutoff)
    ]
    total_candidates = len(candidates)
    candidates.sort(key=lambda n: -n.degree)
    shown_notes = candidates[:limit]
    shown_ids = {n.id for n in shown_notes}

    edges_out = []
    hub_ids: set[str] = set()
    for e in graph.edges:
        if e.source in shown_ids and e.target in shown_ids:
            edges_out.append(e)
        elif e.source.startswith("hub:") and e.target in shown_ids:
            hub_ids.add(e.source)
            edges_out.append(e)
        elif e.target.startswith("hub:") and e.source in shown_ids:
            hub_ids.add(e.target)
            edges_out.append(e)

    nodes_out = [
        {"id": n.id, "kind": n.kind, "label": n.label, "namespace": n.namespace, "degree": n.degree}
        for n in shown_notes
    ] + [
        {"id": h.id, "kind": h.kind, "label": h.label, "namespace": "", "degree": h.degree}
        for entity, h in graph.hubs.items()
        if h.id in hub_ids
    ]
    return {
        "nodes": nodes_out,
        "edges": [{"source": e.source, "target": e.target, "weight": e.weight} for e in edges_out],
        "total_notes": total_candidates,
        "shown_notes": len(shown_notes),
        "truncated": total_candidates > len(shown_notes),
    }


def linked_neighbors(graph: GraphIndex) -> dict[str, set[str]]:
    """relpath -> set of relpaths already connected to it, via a direct
    wikilink edge or shared hub-entity membership. Computed once and reused
    by Layer 3 (vault_similarity) to exclude already-linked pairs from the
    "similar but unlinked" discovery set."""
    linked: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.source.startswith("note:") and e.target.startswith("note:"):
            a, b = e.source[len("note:") :], e.target[len("note:") :]
            linked[a].add(b)
            linked[b].add(a)
    for members in graph.entity_notes.values():
        for rel in members:
            linked[rel] |= members - {rel}
    return dict(linked)


def note_constellation(graph: GraphIndex, relpath: str, hops: int = 1) -> dict:
    """Immediate neighborhood of one note: the note itself plus every node
    directly connected to it (hops=1; hops>=2 walks one further ring). Used
    for the mini constellation embedded on every note page -- always small,
    no cap needed at this scale."""
    center_id = f"note:{relpath}"
    if relpath not in graph.notes:
        return {"nodes": [], "edges": []}
    frontier = {center_id}
    visited = {center_id}
    edges_out: list[GraphEdge] = []
    for _ in range(max(1, hops)):
        next_frontier: set[str] = set()
        for e in graph.edges:
            if e.source in frontier and e.target not in visited:
                next_frontier.add(e.target)
                edges_out.append(e)
            elif e.target in frontier and e.source not in visited:
                next_frontier.add(e.source)
                edges_out.append(e)
            elif e.source in frontier and e.target in frontier:
                edges_out.append(e)
        visited |= next_frontier
        frontier = next_frontier

    def _node_for(node_id: str) -> dict | None:
        if node_id.startswith("note:"):
            n = graph.notes.get(node_id[len("note:"):])
        else:
            n = graph.hubs.get(node_id[len("hub:"):])
        if n is None:
            return None
        return {"id": n.id, "kind": n.kind, "label": n.label, "degree": n.degree}

    nodes_out = [x for x in (_node_for(nid) for nid in visited) if x is not None]
    seen_edges = set()
    dedup_edges = []
    for e in edges_out:
        key = (e.source, e.target)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        dedup_edges.append({"source": e.source, "target": e.target, "weight": e.weight})
    return {"nodes": nodes_out, "edges": dedup_edges, "center": center_id}
