"""Layer 3: memsub/vault_similarity.py -- TF-IDF cosine, unlinked-only
discovery, connection of the day. No embeddings, no LLM calls."""

from __future__ import annotations

from memsub.vault_graph import build_graph_index, linked_neighbors
from memsub.vault_serve import build_index
from memsub.vault_similarity import build_similarity_index, connection_of_the_day, related_ideas


def _write(root, relpath, text):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _vault(tmp_path):
    # a and b share vocabulary (rockets, orbit, launch) and are NOT linked.
    # a and c are linked via a wikilink but share no vocabulary with a/b.
    _write(tmp_path, "a.md", "## Content\n\nRockets reach orbit after a successful launch sequence. [[c]]\n")
    _write(tmp_path, "b.md", "## Content\n\nOrbit is reached by rockets after a clean launch today.\n")
    _write(tmp_path, "c.md", "## Content\n\nCompletely unrelated gardening notes about tomatoes.\n")
    _write(tmp_path, "d.md", "## Content\n\nMore gardening: tomatoes, soil, and watering schedules.\n")
    return build_index(tmp_path)


def _build(tmp_path):
    idx = _vault(tmp_path)
    graph = build_graph_index(idx)
    linked = linked_neighbors(graph)
    sim = build_similarity_index(idx, linked, top_k=5)
    return idx, sim


def test_similar_unlinked_notes_are_found(tmp_path):
    idx, sim = _build(tmp_path)
    related = related_ideas("a.md", sim, idx)
    relpaths = [r["relpath"] for r in related]
    assert "b.md" in relpaths
    assert "c.md" not in relpaths  # linked via wikilink -> excluded


def test_already_linked_notes_excluded_from_discovery(tmp_path):
    idx, sim = _build(tmp_path)
    related = related_ideas("c.md", sim, idx)
    relpaths = [r["relpath"] for r in related]
    assert "a.md" not in relpaths  # a links to c, so c<->a is already-linked
    assert "d.md" in relpaths  # gardening overlap, unlinked


def test_related_ideas_carries_prose_title_and_why(tmp_path):
    idx, sim = _build(tmp_path)
    related = related_ideas("a.md", sim, idx)
    hit = next(r for r in related if r["relpath"] == "b.md")
    assert hit["title"] == "Orbit is reached by rockets after a clean launch today."
    assert hit["why"]  # non-empty shared-term explanation


def test_connection_of_the_day_is_deterministic_per_date(tmp_path):
    idx, sim = _build(tmp_path)
    pick1 = connection_of_the_day(sim, idx, "2026-07-20")
    pick2 = connection_of_the_day(sim, idx, "2026-07-20")
    assert pick1 == pick2
    pick_other_day = connection_of_the_day(sim, idx, "2099-01-01")
    # same pool, just confirm both are valid picks with real content
    assert pick_other_day is not None
    assert pick1["a"]["relpath"] != pick1["b"]["relpath"]


def test_connection_of_the_day_empty_pool_returns_none(tmp_path):
    from memsub.vault_similarity import SimilarityIndex

    empty = SimilarityIndex()
    assert connection_of_the_day(empty, None, "2026-07-20") is None
