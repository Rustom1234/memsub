"""Layer 2: memsub/vault_graph.py -- graph built from vault text, filter/cap,
mini constellation, linked-neighbor computation."""

from __future__ import annotations

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
