"""Layer 2: memsub/vault_graph.py -- graph built from vault text, filter/cap,
mini constellation, linked-neighbor computation, semantic layout."""

from __future__ import annotations

import itertools
import math

from memsub.vault_graph import build_graph_index, filter_and_cap, linked_neighbors, note_constellation
from memsub.vault_serve import build_index


def _write(root, relpath, text):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _small_vault(tmp_path):
    _write(
        tmp_path,
        "a.md",
        '---\nnamespace: "work"\ncreated: "2026-07-19T00:00:00"\n---\n\n'
        "## Content\n\nA note.\n\n## Links\n\n- shares entity **X** with [[b]]\n",
    )
    _write(
        tmp_path,
        "b.md",
        '---\nnamespace: "work"\ncreated: "2026-01-01T00:00:00"\n---\n\n'
        "## Content\n\nB note.\n\n## Links\n\n- shares entity **X** with [[a]]\n",
    )
    _write(
        tmp_path,
        "c.md",
        '---\nnamespace: "personal"\ncreated: "2026-01-01T00:00:00"\n---\n\n'
        "## Content\n\nC note, unrelated. [[a]]\n",
    )
    return build_index(tmp_path)


def test_build_graph_index_creates_hub_for_shared_entity(tmp_path):
    idx = _small_vault(tmp_path)
    graph = build_graph_index(idx)
    assert "X" in graph.hubs
    assert graph.hubs["X"].degree == 2
    assert graph.notes["a.md"].namespace == "work"


def test_filter_and_cap_respects_namespace_filter(tmp_path):
    idx = _small_vault(tmp_path)
    graph = build_graph_index(idx)
    payload = filter_and_cap(graph, namespace="personal")
    note_ids = {n["id"] for n in payload["nodes"] if n["kind"] == "note"}
    assert note_ids == {"note:c.md"}


def test_filter_and_cap_caps_and_reports_truncation(tmp_path):
    idx = _small_vault(tmp_path)
    graph = build_graph_index(idx)
    payload = filter_and_cap(graph, limit=1)
    assert payload["shown_notes"] == 1
    assert payload["total_notes"] == 3
    assert payload["truncated"] is True


def test_note_constellation_returns_immediate_neighborhood_only(tmp_path):
    idx = _small_vault(tmp_path)
    graph = build_graph_index(idx)
    star = note_constellation(graph, "a.md", hops=1)
    ids = {n["id"] for n in star["nodes"]}
    assert "note:a.md" in ids
    assert "note:b.md" in ids or "hub:X" in ids
    assert star["center"] == "note:a.md"


def test_note_constellation_unknown_note_is_empty(tmp_path):
    idx = _small_vault(tmp_path)
    graph = build_graph_index(idx)
    assert note_constellation(graph, "ghost.md") == {"nodes": [], "edges": []}


def test_linked_neighbors_includes_wikilink_and_shared_entity(tmp_path):
    idx = _small_vault(tmp_path)
    graph = build_graph_index(idx)
    linked = linked_neighbors(graph)
    assert "b.md" in linked["a.md"]  # shared entity X
    assert "a.md" in linked["c.md"]  # direct wikilink [[a]]


# --- semantic layout (2026-07-21 rebuild) ---------------------------------


def _two_topic_vault(tmp_path):
    """Two content clusters (widgets vs rockets) sharing no vocabulary, no
    edges between them -- layout separation here can only come from the
    TF-IDF projection, not from graph structure."""
    widget_words = "widget gearbox assembly torque calibration fixture"
    rocket_words = "rocket propellant thruster orbital telemetry gimbal"
    for i in range(4):
        _write(
            tmp_path,
            f"widget{i}.md",
            '---\nnamespace: "eng"\ncreated: "2026-01-01T00:00:00"\n---\n\n'
            f"## Content\n\n{widget_words} note number {i} widget widget gearbox.\n\n## Links\n\n",
        )
    for i in range(4):
        _write(
            tmp_path,
            f"rocket{i}.md",
            '---\nnamespace: "eng"\ncreated: "2026-01-01T00:00:00"\n---\n\n'
            f"## Content\n\n{rocket_words} note number {i} rocket rocket propellant.\n\n## Links\n\n",
        )
    return build_index(tmp_path)


def _dist(a, b, graph):
    na, nb = graph.notes[a], graph.notes[b]
    return math.hypot(na.x - nb.x, na.y - nb.y)


def test_layout_gives_every_note_finite_coordinates(tmp_path):
    idx = _small_vault(tmp_path)
    graph = build_graph_index(idx)
    for node in graph.notes.values():
        assert math.isfinite(node.x)
        assert math.isfinite(node.y)


def test_layout_separates_unrelated_topics_more_than_same_topic(tmp_path):
    idx = _two_topic_vault(tmp_path)
    graph = build_graph_index(idx)
    widgets = [f"widget{i}.md" for i in range(4)]
    rockets = [f"rocket{i}.md" for i in range(4)]
    within = [_dist(a, b, graph) for a, b in itertools.combinations(widgets, 2)]
    within += [_dist(a, b, graph) for a, b in itertools.combinations(rockets, 2)]
    across = [_dist(a, b, graph) for a in widgets for b in rockets]
    assert sum(within) / len(within) < sum(across) / len(across)


def test_layout_hub_position_is_centroid_of_members(tmp_path):
    idx = _small_vault(tmp_path)
    graph = build_graph_index(idx)
    hub = graph.hubs["X"]
    members = [rel for rel in graph.entity_notes["X"] if rel in graph.notes]
    expected_x = sum(graph.notes[m].x for m in members) / len(members)
    expected_y = sum(graph.notes[m].y for m in members) / len(members)
    assert hub.x == expected_x
    assert hub.y == expected_y


def test_layout_is_deterministic_across_rebuilds(tmp_path):
    idx = _two_topic_vault(tmp_path)
    graph1 = build_graph_index(idx)
    graph2 = build_graph_index(idx)
    for rel in graph1.notes:
        assert graph1.notes[rel].x == graph2.notes[rel].x
        assert graph1.notes[rel].y == graph2.notes[rel].y


def test_filter_and_cap_and_constellation_payloads_carry_coordinates(tmp_path):
    idx = _small_vault(tmp_path)
    graph = build_graph_index(idx)
    payload = filter_and_cap(graph)
    for node in payload["nodes"]:
        assert "x" in node and "y" in node
    star = note_constellation(graph, "a.md")
    for node in star["nodes"]:
        assert "x" in node and "y" in node
