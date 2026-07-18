"""`memsub vault serve`: read-only browser viewer for a markdown vault."""

from __future__ import annotations

import http.client
import threading

from memsub.vault_serve import (
    build_index,
    render_note_page,
    safe_path,
    search_notes,
    serve_vault,
)


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
    _write(
        tmp_path,
        "note.md",
        '---\nid: "abc123"\nnamespace: "work"\n---\n\n# Note\n\nBody text.',
    )
    idx = build_index(tmp_path)

    page = render_note_page("note.md", idx).decode("utf-8")
    assert '<div class="meta">' in page
    assert "abc123" in page
    assert "namespace" in page
    # raw frontmatter fence must not leak into the rendered body
    assert "---\nid:" not in page


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
