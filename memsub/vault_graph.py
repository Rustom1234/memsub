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

import hashlib
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

_SHARES_ENTITY_RE = re.compile(r"^- shares entity \*\*([^*]+)\*\* with \[\[([^\]]+)\]\]\s*$", re.MULTILINE)
_WIKILINK_TARGET_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]")

DEFAULT_TOP_ENTITIES = 40
DEFAULT_NODE_LIMIT = 150
MAX_NODE_LIMIT = 1000

# Layout: 2 components (x, y), power-iteration count. 24 passes over
# ~7,300 sparse TF-IDF vectors (the real vault, measured directly) runs in
# single-digit seconds -- see WORKLOG.md 2026-07-21 for the benchmark this
# was chosen against (build_similarity_index's comparable one-pass scan
# takes 1.6s over the same corpus).
LAYOUT_COMPONENTS = 2
LAYOUT_ITER = 24
LAYOUT_SEED = 42

# Cluster color index: 8 slots, matched client-side to a curated jewel-tone
# palette (vault_serve.py's _GRAPH_JS). Assigned by stable hash of a label
# (a note's primary hub entity, or its namespace as fallback) so the same
# entity always lands on the same color across page loads and filters.
CLUSTER_COUNT = 8


@dataclass
class GraphNode:
    id: str  # "note:<relpath>" or "hub:<entity name>"
    kind: str  # "note" | "hub"
    label: str
    namespace: str = ""
    ts: float = 0.0
    degree: int = 0
    x: float = 0.0  # layout position (semantic 2D projection, see compute_layout)
    y: float = 0.0
    cluster: int = -1  # 0..CLUSTER_COUNT-1 palette slot, -1 = no hub/namespace (neutral)


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

    compute_layout(idx, graph, content_notes)
    _assign_clusters(graph)
    return graph


def _cluster_of_label(label: str) -> int:
    return int(hashlib.sha1(label.encode("utf-8")).hexdigest()[:8], 16) % CLUSTER_COUNT


def _assign_clusters(graph: GraphIndex) -> None:
    """Each note's cluster color follows the biggest hub entity it belongs
    to (falls back to namespace, then neutral) so nodes that share a hub in
    the graph also share a color region -- render quality's "cluster-colored"
    requirement, computed once here rather than re-derived per request."""
    note_to_hubs: dict[str, list[str]] = defaultdict(list)
    for entity, hub in graph.hubs.items():
        for rel in graph.entity_notes.get(entity, ()):
            if rel in graph.notes:
                note_to_hubs[rel].append(entity)

    for rel, node in graph.notes.items():
        candidates = note_to_hubs.get(rel)
        if candidates:
            primary = max(candidates, key=lambda e: graph.hubs[e].degree)
            node.cluster = _cluster_of_label(primary)
        elif node.namespace:
            node.cluster = _cluster_of_label(node.namespace)
        else:
            node.cluster = -1

    for entity, hub in graph.hubs.items():
        hub.cluster = _cluster_of_label(entity)


# ---------------------------------------------------------------------------
# Layout: replaces pure force-physics with a precomputed semantic position so
# clusters form visible islands instead of an evenly-spread blob. Method:
# truncated SVD to exactly 2 components (LSA-style text-clustering
# projection) over the same TF-IDF signal vault_similarity.py already
# computes for Layer 3 -- reuses its tokenizer/content-extraction functions
# by import (that module is out of this task's edit surface, stays
# untouched). Client-side force sim then only does a gentle local relax on
# top of these coordinates -- see vault_serve.py's graph JS.

_LAYOUT_CACHE: dict[tuple, dict[str, tuple[float, float]]] = {}
_LAYOUT_CACHE_MAX = 8


def _layout_cache_key(content_notes) -> tuple:
    # "recompute only when note count changes" (the spec's literal wording)
    # plus a content hash so two same-size vaults (e.g. two tests' tmp_path
    # fixtures) never collide in this process-wide cache.
    digest = hashlib.sha1("\n".join(sorted(content_notes)).encode("utf-8")).hexdigest()
    return (len(content_notes), digest)


def _tfidf_vectors_for_layout(idx, content_notes: set[str]) -> dict[str, dict[str, float]]:
    from .vault_similarity import _content_text, _tokenize

    relpaths = sorted(content_notes)
    n_docs = len(relpaths)
    term_counts: dict[str, Counter] = {}
    df: Counter = Counter()
    for rel in relpaths:
        counts = Counter(_tokenize(_content_text(rel, idx)))
        term_counts[rel] = counts
        for term in counts:
            df[term] += 1

    # Deliberately NOT vault_similarity's max_df=0.15 band: that pruning
    # exists to suppress a whole cluster's defining vocabulary, because
    # Layer 3 wants *surprising* unlinked pairs, not the obvious ones. This
    # is the opposite goal -- layout wants exactly that cluster-defining
    # vocabulary preserved, since it's what makes clusters separate into
    # visible islands in the first place. Only a much higher ceiling (terms
    # in most of the whole corpus -- generic language, not topic signal)
    # and the same df>=2 floor (a term unique to one doc can't relate it to
    # any other) are pruned here.
    max_df = max(2, int(0.6 * n_docs)) if n_docs else 2
    kept_terms = {t for t, d in df.items() if 2 <= d <= max_df}

    vectors: dict[str, dict[str, float]] = {}
    for rel, counts in term_counts.items():
        vec = {}
        for term in kept_terms & counts.keys():
            tf = counts[term]
            idf = math.log(n_docs / df[term]) if df[term] else 0.0
            w = (1 + math.log(tf)) * idf
            if w > 0:
                vec[term] = w
        norm = math.sqrt(sum(w * w for w in vec.values()))
        vectors[rel] = {t: w / norm for t, w in vec.items()} if norm > 0 else {}
    return vectors


def _svd2(vectors: dict[str, dict[str, float]], relpaths: list[str]) -> list[list[float]]:
    """Top-`LAYOUT_COMPONENTS` left singular vectors of the sparse doc-term
    matrix, via sequential power iteration with Gram-Schmidt deflation
    (find component 1, then component 2 orthogonal to it, etc.) -- the
    standard cheap way to get an ordered, non-degenerate low-rank text
    embedding without ever materializing a dense N x D matrix. Returns a
    list of LAYOUT_COMPONENTS arrays, each aligned with `relpaths`."""
    rng = random.Random(LAYOUT_SEED)
    n = len(relpaths)
    docs = [vectors.get(r, {}) for r in relpaths]
    all_terms: set[str] = set()
    for v in docs:
        all_terms.update(v)

    us: list[list[float]] = []
    vs: list[dict[str, float]] = []
    if not all_terms or n == 0:
        return [[0.0] * n for _ in range(LAYOUT_COMPONENTS)]

    for _component in range(LAYOUT_COMPONENTS):
        v = {t: rng.gauss(0.0, 1.0) for t in all_terms}
        _deflate_dict(v, vs)
        _normalize_dict(v)
        u = [0.0] * n
        for _ in range(LAYOUT_ITER):
            u = [sum(w * v.get(t, 0.0) for t, w in doc.items()) for doc in docs]
            _deflate_list(u, us)
            _normalize_list(u)
            nv: dict[str, float] = defaultdict(float)
            for i, doc in enumerate(docs):
                ui = u[i]
                if ui == 0.0:
                    continue
                for t, w in doc.items():
                    nv[t] += w * ui
            v = dict(nv)
            _deflate_dict(v, vs)
            _normalize_dict(v)
        us.append(u)
        vs.append(v)
    return us


def _normalize_list(vec: list[float]) -> None:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    for i in range(len(vec)):
        vec[i] /= norm


def _normalize_dict(vec: dict[str, float]) -> None:
    norm = math.sqrt(sum(x * x for x in vec.values())) or 1.0
    for t in vec:
        vec[t] /= norm


def _deflate_list(vec: list[float], prior: list[list[float]]) -> None:
    for p in prior:
        proj = sum(a * b for a, b in zip(vec, p))
        for i in range(len(vec)):
            vec[i] -= proj * p[i]


def _deflate_dict(vec: dict[str, float], prior: list[dict[str, float]]) -> None:
    for p in prior:
        proj = sum(vec.get(t, 0.0) * w for t, w in p.items())
        for t, w in p.items():
            vec[t] = vec.get(t, 0.0) - proj * w


def _jittered(seed_text: str, spread: float) -> tuple[float, float]:
    """Deterministic small offset for notes with no TF-IDF signal (empty
    body), so they don't all stack exactly on the origin."""
    h = hashlib.sha1(seed_text.encode("utf-8")).hexdigest()
    rng = random.Random(int(h[:16], 16))
    angle = rng.uniform(0, 2 * math.pi)
    r = spread * rng.uniform(0.15, 1.0)
    return (r * math.cos(angle), r * math.sin(angle))


def _ranks(vals: list[float]) -> list[float]:
    """0-indexed average rank of each value (ties share the mean rank of
    their tied block), aligned with `vals`' input order."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _rank_normalize(coords: dict[str, tuple[float, float]]) -> None:
    """Density-equalizing transform: replace each coordinate with its rank
    percentile along that axis (independently for x and y), mapped to a
    fixed [-0.5, 0.5] range. A raw SVD-2D projection on real text corpora
    tends to crush most documents into one narrow band while a handful of
    outliers stretch the axis wide (2026-07-21 real-vault screenshot: ~80%
    of nodes crushed into one horizontal band) -- rank order is monotonic
    with the raw value, so genuine cluster separation survives, but density
    becomes uniform along each axis by construction, which is what makes
    islands fill the canvas instead of striping it. Mutates in place."""
    if len(coords) < 3:
        return
    keys = list(coords.keys())
    n = len(keys)
    x_rank = _ranks([coords[k][0] for k in keys])
    y_rank = _ranks([coords[k][1] for k in keys])
    for i, k in enumerate(keys):
        rx = (x_rank[i] / (n - 1) - 0.5) if n > 1 else 0.0
        ry = (y_rank[i] / (n - 1) - 0.5) if n > 1 else 0.0
        coords[k] = (rx, ry)


def compute_layout(idx, graph: GraphIndex, content_notes: set[str]) -> None:
    """Populate `x`/`y` on every note and hub node in place. Cached
    per-process on (note count, content hash) so repeated calls against an
    unchanged vault (e.g. the test suite) skip recomputation."""
    key = _layout_cache_key(content_notes)
    cached = _LAYOUT_CACHE.get(key)
    if cached is None:
        vectors = _tfidf_vectors_for_layout(idx, content_notes)
        relpaths = sorted(content_notes)
        components = _svd2(vectors, relpaths)
        coords: dict[str, tuple[float, float]] = {}
        empty = [r for r in relpaths if not vectors.get(r)]
        for i, rel in enumerate(relpaths):
            if vectors.get(rel):
                coords[rel] = (components[0][i], components[1][i])
        # rank-normalize the real (non-jittered) notes only, so density
        # equalization reflects genuine content signal, not filler jitter
        _rank_normalize(coords)
        for rel in empty:
            coords[rel] = _jittered(rel, 0.5)
        if len(_LAYOUT_CACHE) >= _LAYOUT_CACHE_MAX:
            _LAYOUT_CACHE.pop(next(iter(_LAYOUT_CACHE)))
        _LAYOUT_CACHE[key] = coords
        cached = coords

    for rel, node in graph.notes.items():
        x, y = cached.get(rel, (0.0, 0.0))
        node.x, node.y = x, y

    for entity, hub in graph.hubs.items():
        members = graph.entity_notes.get(entity, set())
        pts = [cached[m] for m in members if m in cached]
        if pts:
            hub.x = sum(p[0] for p in pts) / len(pts)
            hub.y = sum(p[1] for p in pts) / len(pts)


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
        {
            "id": n.id,
            "kind": n.kind,
            "label": n.label,
            "namespace": n.namespace,
            "degree": n.degree,
            "x": round(n.x, 4),
            "y": round(n.y, 4),
            "cluster": n.cluster,
        }
        for n in shown_notes
    ] + [
        {
            "id": h.id,
            "kind": h.kind,
            "label": h.label,
            "namespace": "",
            "degree": h.degree,
            "x": round(h.x, 4),
            "y": round(h.y, 4),
            "cluster": h.cluster,
        }
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
        return {
            "id": n.id,
            "kind": n.kind,
            "label": n.label,
            "degree": n.degree,
            "x": round(n.x, 4),
            "y": round(n.y, 4),
            "cluster": n.cluster,
        }

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
