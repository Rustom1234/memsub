"""`memsub vault serve`: read-only browser viewer for a markdown vault."""

from __future__ import annotations

import http.client
import threading

from memsub.vault_graph import build_graph_index, linked_neighbors
from memsub.vault_serve import (
    build_index,
    make_handler,
    render_graph_page,
    render_index_page,
    render_note_page,
    safe_path,
    search_notes,
    serve_vault,
)
from memsub.vault_similarity import build_similarity_index


def _write(root, relpath: str, text: str):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_wikilink_to_existing_note_renders_as_link(tmp_path):
    _write(tmp_path, "a.md", "See [[b]] for details.")
    _write(tmp_path, "b.md", "# B\n\nBody.")
    idx = build_index(tmp_path)

    page = render_note_page("a.md", idx).decode("utf-8")
    assert '<a class="wikilink" href="/note/b.md">b</a>' in page


def test_wikilink_to_missing_note_gets_dead_style(tmp_path):
    _write(tmp_path, "a.md", "See [[nope]] for details.")
    idx = build_index(tmp_path)

    page = render_note_page("a.md", idx).decode("utf-8")
    assert 'class="wikilink dead"' in page
    assert "nope" in page
    assert '<a class="wikilink" href="/note/nope.md">' not in page


def test_frontmatter_shown_as_metadata_block_not_raw(tmp_path):
    # Layer 0 (2026-07-20): raw machine fields (id, namespace, etc.) collapse
    # into one closed <details> block at the bottom, never a flat dump.
    _write(
        tmp_path,
        "note.md",
        '---\nid: "abc123"\nnamespace: "work"\n---\n\n# Note\n\nBody text.',
    )
    idx = build_index(tmp_path)

    page = render_note_page("note.md", idx).decode("utf-8")
    assert '<details class="note-details">' in page
    assert "abc123" in page
    assert "namespace" in page
    # raw frontmatter fence must not leak into the rendered body
    assert "---\nid:" not in page
    # provenance badge still shown prominently, outside the details block
    assert '<span class="badge badge-observed">observed</span>' in page


def test_mermaid_block_rendered_as_pre_mermaid_with_cdn_script(tmp_path):
    _write(tmp_path, "diagram.md", "# Diagram\n\n```mermaid\ngraph TD; A-->B;\n```\n")
    idx = build_index(tmp_path)

    page = render_note_page("diagram.md", idx).decode("utf-8")
    assert '<pre class="mermaid">' in page
    assert "graph TD; A--&gt;B;" in page  # html-escaped inside the pre
    assert "mermaid.min.js" in page
    assert "mermaid.initialize" in page


def test_search_finds_note_by_content_and_filename(tmp_path):
    _write(tmp_path, "topic.md", "Rustom's cold email product idea.")
    _write(tmp_path, "other.md", "Unrelated note.")
    idx = build_index(tmp_path)

    by_content = search_notes(idx, "cold email")
    assert by_content == ["topic.md"]

    by_filename = search_notes(idx, "topic")
    assert by_filename == ["topic.md"]

    assert search_notes(idx, "nonexistent-term-xyz") == []


def test_safe_path_rejects_traversal_outside_root(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    _write(root, "inside.md", "fine")
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me", encoding="utf-8")

    assert safe_path(root, "inside.md") is not None
    assert safe_path(root, "../secret.txt") is None
    assert safe_path(root, "../../etc/passwd") is None
    assert safe_path(root, "/etc/passwd") is None
    assert safe_path(root, "notes/../../secret.txt") is None


def test_safe_path_rejects_missing_file(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    assert safe_path(root, "ghost.md") is None


def test_serve_vault_over_http_smoke(tmp_path):
    """End-to-end: start the real server on an ephemeral port, hit the index
    and a note over HTTP, then shut it down. Never leaves a server running."""
    root = tmp_path / "vault"
    _write(root, "hello.md", "# Hello\n\nA note with a [[missing]] link.")

    import socketserver

    from memsub.vault_serve import build_index, make_handler

    idx = build_index(root)
    handler = make_handler(idx)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"hello" in resp.read().lower()

        conn.request("GET", "/note/hello.md")
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "Hello" in body
        assert "wikilink dead" in body

        conn.request("GET", "/note/%2e%2e%2foutside.md")
        resp = conn.getresponse()
        assert resp.status == 404
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_serve_vault_rejects_nonexistent_root(tmp_path):
    assert serve_vault(tmp_path / "does-not-exist", port=0) == 1


# --------------------------------------------------------------------------- #
# Second brain Layers 0/2/3 (2026-07-20): readability, graph, connections
# --------------------------------------------------------------------------- #
def _standard_note(entity_targets):
    links = "\n".join(f"- shares entity **Chat** with [[{t}]]" for t in entity_targets)
    return (
        '---\nid: "e1"\nnamespace: "claude-code"\nkind: "event"\ntype: "observed"\n'
        'status: "active"\ncreated: "2026-07-19T00:00:00"\n---\n\n'
        "# some-truncated-slug-that-should-never-appear-as-a-title\n\n"
        "## Content\n\nThe user prefers nohup over tmux for the agent loop. Second sentence here.\n\n"
        "## Provenance\n\n- Run: `r1`\n- Session: `s1`\n\n"
        "### Audit events\n\n- none recorded\n\n"
        f"## Links\n\n{links}\n"
    )


def _fixture_vault(tmp_path):
    _write(tmp_path, "note.md", _standard_note(["other-a", "other-b", "other-c"]))
    for i, name in enumerate(["other-a", "other-b", "other-c"]):
        _write(
            tmp_path,
            f"{name}.md",
            f'---\nnamespace: "claude-code"\ncreated: "2026-07-1{i}T00:00:00"\n---\n\n'
            f"## Content\n\nUnrelated content number {i} about gardening and soil.\n",
        )
    idx = build_index(tmp_path)
    graph = build_graph_index(idx)
    sim = build_similarity_index(idx, linked_neighbors(graph))
    return idx, graph, sim


def test_note_page_title_is_prose_not_slug(tmp_path):
    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_note_page("note.md", idx, graph, sim).decode("utf-8")
    assert "<h1>The user prefers nohup over tmux for the agent loop.</h1>" in page
    assert "some-truncated-slug" not in page


def test_note_page_link_soup_collapses_to_grouped_related_line(tmp_path):
    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_note_page("note.md", idx, graph, sim).decode("utf-8")
    assert "Related: 3 notes about Chat" in page
    # the raw repeated bullet form must be gone
    assert page.count("shares entity") == 0


def test_note_page_machine_fields_collapse_into_closed_details(tmp_path):
    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_note_page("note.md", idx, graph, sim).decode("utf-8")
    assert "<details class=\"note-details\">" in page
    # closed by default: no `open` attribute
    assert "<details class=\"note-details\" open>" not in page
    assert "r1" in page  # run id still present, just inside the details block


def test_note_page_related_panel_has_two_groups(tmp_path):
    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_note_page("note.md", idx, graph, sim).decode("utf-8")
    assert "Already linked" in page
    assert "Similar but unlinked" in page


def test_dashboard_shows_brain_knows_and_recent_activity(tmp_path):
    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_index_page(idx, "", None, graph, sim).decode("utf-8")
    assert "what your brain knows" in page
    assert "recent activity" in page
    assert "projects" in page
    assert "entity-chip" in page


def test_graph_page_renders_controls_and_canvas(tmp_path):
    idx, graph, _ = _fixture_vault(tmp_path)
    page = render_graph_page(idx, graph).decode("utf-8")
    assert 'id="graph-namespace"' in page
    assert 'id="graph-period"' in page
    assert 'class="graph-canvas"' in page
    assert "1 hub entity" in page  # node data itself loads client-side via /api/graph.json


def test_note_page_has_mini_constellation_canvas(tmp_path):
    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_note_page("note.md", idx, graph, sim).decode("utf-8")
    assert 'data-mode="mini"' in page
    assert "Constellation" in page


def test_api_graph_json_over_http_smoke_and_traversal_stays_safe(tmp_path):
    import json
    import socketserver

    idx, graph, sim = _fixture_vault(tmp_path)
    handler = make_handler(idx, None, graph, sim)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)

        conn.request("GET", "/graph")
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"graph-canvas" in resp.read()

        conn.request("GET", "/api/graph.json?limit=2")
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert payload["shown_notes"] <= 2
        assert payload["total_notes"] == 4  # note.md + 3 others
        assert payload["truncated"] is True

        # namespace filter narrows to zero when no note matches
        conn.request("GET", "/api/graph.json?namespace=nope")
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        assert payload["shown_notes"] == 0

        # no path parameter on these routes -- confirm the existing
        # traversal-safe /note/ route is unaffected by the new wiring
        conn.request("GET", "/note/%2e%2e%2foutside.md")
        resp = conn.getresponse()
        assert resp.status == 404
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


