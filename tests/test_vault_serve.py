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
from memsub.store import EpisodicStore
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


def test_safe_path_rejects_embedded_null_byte(tmp_path):
    # Path.resolve() raises ValueError on an embedded null byte -- confirm
    # safe_path treats that the same as any other bad path (None), rather
    # than letting the ValueError propagate and kill the connection.
    root = tmp_path / "vault"
    root.mkdir()
    assert safe_path(root, "foo\x00bar") is None


def test_note_route_with_null_byte_returns_clean_404(tmp_path):
    import socketserver

    idx, graph, sim = _fixture_vault(tmp_path)
    handler = make_handler(idx, None, graph, sim)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/note/foo%00bar")
        resp = conn.getresponse()
        assert resp.status == 404
        resp.read()
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_serve_vault_over_http_smoke(tmp_path):
    """End-to-end: start the real server on an ephemeral port, hit the
    showcase root, the notes dashboard, and a note over HTTP, then shut it
    down. Never leaves a server running."""
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
        assert b"memsub" in resp.read().lower()  # the showcase landing page

        conn.request("GET", "/notes")
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"hello" in resp.read().lower()  # the notes dashboard lists it

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


def _raw_post_status(port: int, path: str, content_length_header: str, timeout: float = 5) -> str:
    """Send a raw HTTP POST with an attacker-controlled Content-Length header
    (http.client would recompute a correct one itself, so a real socket is
    needed to reproduce a malformed/negative value) and return the status
    line. A socket timeout here means the handler hung reading the body --
    the DoS this guards against -- so the caller's own timeout is the
    real assertion that it did not."""
    import socket

    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        request = (
            f"POST {path} HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            f"Content-Length: {content_length_header}\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        return response.split(b"\r\n", 1)[0].decode("ascii")
    finally:
        sock.close()


def test_api_ask_and_resonate_reject_malformed_content_length_without_hanging(tmp_path):
    import socketserver

    idx, graph, sim = _fixture_vault(tmp_path)
    handler = make_handler(idx, None, graph, sim)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        for path in ("/api/ask", "/api/resonate"):
            assert "400" in _raw_post_status(port, path, "abc")
            assert "400" in _raw_post_status(port, path, "-1")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# "Ask your brain" (2026-07-20): /ask page + /api/ask route
# --------------------------------------------------------------------------- #
def test_ask_page_renders_form_and_sample_chips(tmp_path):
    from memsub.vault_serve import render_ask_page

    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_ask_page(idx, graph).decode("utf-8")
    assert 'id="ask-form"' in page
    assert 'id="ask-input"' in page
    assert 'class="ask-chip"' in page
    assert "Chat" in page  # top hub entity from the fixture's shared-entity links
    assert '<a class="nav-tab active" href="/ask">ask</a>' in page


def test_api_ask_without_store_gives_honest_vault_only_message(tmp_path):
    import json
    import socketserver

    idx, graph, sim = _fixture_vault(tmp_path)
    handler = make_handler(idx, None, graph, sim)  # no ask_clients/ask_backend -- vault-only mode
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/api/ask", body=json.dumps({"question": "anything"}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert payload["sufficient"] is False
        assert "vault-only mode" in payload["message"]
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_api_ask_end_to_end_with_mock_backend_returns_cited_answer(tmp_path):
    import json
    import socketserver

    from memsub.backends import MockBackend
    from memsub.sdk import MemoryClient

    idx, graph, sim = _fixture_vault(tmp_path)
    db_path = tmp_path / "memory.db"
    seed_client = MemoryClient(db_path=db_path, namespace="claude-code", retrieval="hybrid")
    seed_client.add("The user prefers nohup over tmux for the agent loop.")
    seed_client.close()
    backend = MockBackend(responder=lambda s, m: "The user prefers nohup over tmux. [1]")

    handler = make_handler(idx, db_path, graph, sim, backend)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)

        conn.request("GET", "/ask")
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"ask-form" in resp.read()

        conn.request(
            "POST", "/api/ask",
            body=json.dumps({"question": "does the user prefer nohup or tmux for the agent loop"}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert payload["sufficient"] is True
        assert "cite-chip" in payload["answer_html"]
        assert payload["cited"] == [1]

        conn.request("POST", "/api/ask", body=json.dumps({"question": "what is the capital of France"}),
                      headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        assert payload["sufficient"] is False
        assert payload["message"] == "Your brain does not contain enough on this yet."
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# Idea resonance (2026-07-21): /resonate page + /api/resonate route
# --------------------------------------------------------------------------- #
def test_resonate_page_renders_form(tmp_path):
    from memsub.vault_serve import render_resonate_page

    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_resonate_page(idx, graph).decode("utf-8")
    assert 'id="resonance-form"' in page
    assert 'id="resonance-input"' in page
    assert '<a class="nav-btn" href="/resonate">resonate</a>' in page


def test_api_resonate_without_store_gives_honest_vault_only_message(tmp_path):
    import json
    import socketserver

    idx, graph, sim = _fixture_vault(tmp_path)
    handler = make_handler(idx, None, graph, sim)  # no db -- vault-only mode
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/api/resonate", body=json.dumps({"idea": "anything"}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert payload["matches"] == []
        assert "vault-only mode" in payload["message"]
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_api_resonate_end_to_end_finds_distant_match_and_persists_idea(tmp_path):
    import json
    import socketserver

    from memsub.backends import MockBackend
    from memsub.sdk import MemoryClient

    idx, graph, sim = _fixture_vault(tmp_path)
    db_path = tmp_path / "memory.db"
    seed_client = MemoryClient(db_path=db_path, namespace="claude-code")
    ep_lighthouse = seed_client.add(
        "Maya wrote about the tide and coastline near the lighthouse, and how alive it felt at dusk."
    )
    ep_rhythm = seed_client.add(
        "Maya read that irregular rhythm patterns near the tide and coastline feel alive to visitors."
    )
    store = seed_client.store
    store.upsert_entity("claude-code::lighthouse", "claude-code", "TOPIC", ep_lighthouse.ts)
    store.link_entity("claude-code::lighthouse", ep_lighthouse.id, ep_lighthouse.ts)
    store.upsert_entity("claude-code::rhythm", "claude-code", "TOPIC", ep_rhythm.ts)
    store.link_entity("claude-code::rhythm", ep_rhythm.id, ep_rhythm.ts)
    before_count = store.count()
    seed_client.close()

    backend = MockBackend(responder=lambda s, m: "These connect because both are about shared imagery.")
    # allow_resonate_write=True: this test is specifically about the opt-in
    # persistence path (see test_api_resonate_does_not_persist_by_default
    # for the off-by-default behavior).
    handler = make_handler(idx, db_path, graph, sim, backend, None, None, None, True)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        idea = "the tide and rhythm of the coastline feel alive to me lately"
        conn.request(
            "POST", "/api/resonate", body=json.dumps({"idea": idea}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert len(payload["matches"]) == 1
        match = payload["matches"][0]
        assert "lighthouse" in match["title"]
        assert match["bridge"] == "These connect because both are about shared imagery."
        assert payload["persisted_id"]
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    # the idea itself was persisted through the public write API
    verify_store = EpisodicStore(db_path)
    try:
        assert verify_store.count() == before_count + 1
        newest = verify_store.all()[-1]
        assert newest.content == idea
        assert newest.provenance.get("channel") == "user_stated"
    finally:
        verify_store.close()


def test_api_resonate_does_not_persist_by_default(tmp_path):
    import json
    import socketserver

    from memsub.backends import MockBackend
    from memsub.sdk import MemoryClient

    idx, graph, sim = _fixture_vault(tmp_path)
    db_path = tmp_path / "memory.db"
    seed_client = MemoryClient(db_path=db_path, namespace="claude-code")
    seed_client.add("Maya wrote about the tide and coastline near the lighthouse.")
    before_count = seed_client.store.count()
    seed_client.close()

    backend = MockBackend(responder=lambda s, m: "why")
    # allow_resonate_write left at its default (False): a resonate POST must
    # return the resonance result without writing anything to --db.
    handler = make_handler(idx, db_path, graph, sim, backend)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/api/resonate", body=json.dumps({"idea": "a brand new idea, never seen before"}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert payload["persisted_id"] is None
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    verify_store = EpisodicStore(db_path)
    try:
        assert verify_store.count() == before_count  # no row added
    finally:
        verify_store.close()


# --------------------------------------------------------------------------- #
# /loops page + /api/loops.json (2026-07-22): dropped-work inbox rendered
# from memsub.loops.Finding objects, computed once at server startup.
# --------------------------------------------------------------------------- #
def test_render_loops_page_empty_state():
    from memsub.vault_serve import render_loops_page

    page = render_loops_page([]).decode("utf-8")
    assert "0 findings" in page


def test_render_loops_page_groups_by_severity_and_collapses_low_tiers():
    from memsub.loops import Finding
    from memsub.vault_serve import render_loops_page

    findings = [
        Finding(session="/tmp/a.jsonl", ts=1_800_000_000.0,
                reason="unanswered ask: \"add tests please\"",
                severity="interrupt", confidence="certain", signal="unanswered_ask"),
        Finding(session="/tmp/b.jsonl", ts=1_800_000_100.0,
                reason="tool call 'Bash' never got a result: session died mid-action",
                severity="ambient", confidence="certain", signal="dangling_tool_use"),
    ]
    page = render_loops_page(findings).decode("utf-8")
    assert "unanswered ask" in page
    assert "died mid-action" in page
    # interrupt tier is always visible (no <details> wrapper)...
    interrupt_pos = page.index("badge-severity-interrupt")
    assert "<details" not in page[max(0, interrupt_pos - 200):interrupt_pos]
    # ...ambient tier starts collapsed inside a <details>
    assert "<details class=\"loops-section\">" in page
    assert "click to expand" in page


def test_dashboard_has_no_loops_tile_by_default(tmp_path):
    """No --projects-dir means no scan happened: the tile must be absent,
    not a zero-count tile (docs/loops-2026-07-22.md: /loops is opt-in)."""
    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_index_page(idx, "", None, graph, sim).decode("utf-8")
    assert 'class="tile tile-link" href="/loops"' not in page


def test_dashboard_shows_loops_tile_with_interrupt_count_when_enabled(tmp_path):
    from memsub.loops import Finding

    idx, graph, sim = _fixture_vault(tmp_path)
    findings = [
        Finding(session="/tmp/a.jsonl", ts=1_800_000_000.0, reason="unanswered ask",
                severity="interrupt", confidence="certain", signal="unanswered_ask"),
        Finding(session="/tmp/b.jsonl", ts=1_800_000_100.0, reason="dangling tool call",
                severity="ambient", confidence="certain", signal="dangling_tool_use"),
    ]
    page = render_index_page(idx, "", None, graph, sim, findings, True).decode("utf-8")
    assert 'class="tile tile-link" href="/loops"' in page
    assert "1 interrupt" in page
    assert ">2<" in page  # total finding count as the tile value


def test_dashboard_loops_tile_absent_when_disabled_even_with_findings(tmp_path):
    from memsub.loops import Finding

    idx, graph, sim = _fixture_vault(tmp_path)
    findings = [Finding(session="/tmp/a.jsonl", ts=1_800_000_000.0, reason="x",
                        severity="interrupt", confidence="certain", signal="unanswered_ask")]
    page = render_index_page(idx, "", None, graph, sim, findings, False).decode("utf-8")
    assert 'class="tile tile-link" href="/loops"' not in page


def _seed_blackbox_store(tmp_path, sweep_ts: float, incidents: int) -> Path:
    """A tmp store populated via memsub.filehist (one real file state) plus a
    directly-inserted blackbox_sweeps row (memsub.blackbox's own schema)."""
    import json

    from memsub import filehist
    from memsub.blackbox import _SWEEP_SCHEMA
    from memsub.store import EpisodicStore

    db_path = tmp_path / "bb.db"
    store = EpisodicStore(db_path)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    target = tmp_path / "main.py"
    target.write_text("v1 = 1\n")
    line = json.dumps({
        "type": "assistant", "timestamp": "2026-07-22T00:00:00Z", "message": {"content": [
            {"type": "tool_use", "id": "w0", "name": "Write",
             "input": {"file_path": str(target), "content": "v1 = 1\n"}}]},
    })
    (tdir / "s.jsonl").write_text(line + "\n")
    filehist.index_transcripts(store, tdir)
    conn = filehist._conn(store)
    conn.executescript(_SWEEP_SCHEMA)
    conn.execute(
        "INSERT INTO blackbox_sweeps (ts, repos_checked, states_indexed, incidents, elapsed_s)"
        " VALUES (?, ?, ?, ?, ?)",
        (sweep_ts, 1, 1, incidents, 0.5),
    )
    conn.commit()
    store.close()
    return db_path


def test_dashboard_shows_blackbox_tile_when_sweep_table_exists(tmp_path):
    import time

    idx, graph, sim = _fixture_vault(tmp_path)
    db_path = _seed_blackbox_store(tmp_path, time.time() - 60, incidents=2)
    page = render_index_page(idx, "", db_path, graph, sim).decode("utf-8")
    assert 'class="tile tile-link" href="/blackbox"' in page
    assert "2 incident(s)" in page


def test_dashboard_has_no_blackbox_tile_when_table_missing(tmp_path):
    from memsub.store import EpisodicStore

    idx, graph, sim = _fixture_vault(tmp_path)
    db_path = tmp_path / "plain.db"
    EpisodicStore(db_path).close()  # store exists, but blackbox never scanned
    page = render_index_page(idx, "", db_path, graph, sim).decode("utf-8")
    assert 'class="tile tile-link" href="/blackbox"' not in page


def test_dashboard_has_no_blackbox_tile_without_db(tmp_path):
    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_index_page(idx, "", None, graph, sim).decode("utf-8")
    assert 'class="tile tile-link" href="/blackbox"' not in page


def test_blackbox_page_renders_states_and_correct_restore_command(tmp_path):
    """Regression test for the CLI command rename: the recovery command shown
    on /blackbox must read `memsub blackbox restore ... --at ...` (the old
    name is long gone, but the page's f-string could silently drift back)."""
    import time

    from memsub.vault_serve import render_blackbox_page

    idx, graph, sim = _fixture_vault(tmp_path)
    db_path = _seed_blackbox_store(tmp_path, time.time() - 120, incidents=1)
    target = str(tmp_path / "main.py")
    page = render_blackbox_page(idx, db_path, target).decode("utf-8")
    assert "recorded state(s)" in page
    assert f"memsub blackbox restore '{target}' --at '" in page


def test_loops_page_and_api_over_http_from_fixture_transcripts(tmp_path):
    """A 'test vault': an isolated tmp_path fixture standing in for a real
    ~/.claude*/projects transcripts dir (never the real one -- see this
    module's docstring / CLAUDE.md rule on tmp fixtures only)."""
    import json
    import os
    import socketserver
    import time

    from memsub.loops import scan_loops

    root = tmp_path / "vault"
    _write(root, "hello.md", "# Hello")
    idx = build_index(root)

    transcripts = tmp_path / "fake-claude-projects"
    session = transcripts / "s1.jsonl"
    session.parent.mkdir(parents=True)
    ts = 1_800_000_000.0
    session.write_text(
        json.dumps({"type": "user", "timestamp": "2027-01-15T00:00:00Z",
                    "message": {"content": [{"type": "text", "text": "fix the parser"}]}}) + "\n"
        + json.dumps({"type": "assistant", "timestamp": "2027-01-15T00:01:00Z",
                      "message": {"content": [{"type": "text", "text": "done"}]}}) + "\n"
        + json.dumps({"type": "user", "timestamp": "2027-01-15T00:02:00Z",
                      "message": {"content": [{"type": "text", "text": "now add tests please"}]}}) + "\n"
    )
    old = time.time() - 5000
    os.utime(session, (old, old))

    findings = scan_loops([transcripts], days=3650, min_age_secs=0)
    assert findings, "fixture session should produce at least one finding"

    handler = make_handler(idx, None, None, None, None, findings)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/loops")
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "unanswered ask" in body

        conn.request("GET", "/api/loops.json")
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert isinstance(payload, list) and len(payload) == len(findings)
        assert payload[0]["session"] == str(session)
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# Unified UI (2026-07-23): nav tab bar, Showcase ("/"), Garden, Bridges
# --------------------------------------------------------------------------- #
def test_nav_has_all_eight_primary_tabs_with_active_state(tmp_path):
    from memsub.vault_serve import render_graph_page

    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_graph_page(idx, graph).decode("utf-8")
    # the current page's own tab is highlighted...
    assert '<a class="nav-tab active" href="/graph">graph</a>' in page
    # ...and every other primary tab is present, not active
    for href, label in [
        ("/", "showcase"), ("/notes", "notes"), ("/garden", "garden"),
        ("/bridges", "bridges"), ("/blackbox", "blackbox"), ("/loops", "loops"),
        ("/ask", "ask"),
    ]:
        assert f'<a class="nav-tab" href="{href}">{label}</a>' in page


def test_showcase_page_renders_hero_and_all_four_product_cards(tmp_path):
    from memsub.vault_serve import render_showcase_page

    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_showcase_page(idx, None).decode("utf-8")
    assert '<a class="nav-tab active" href="/">showcase</a>' in page
    for name, href in [("Garden", "/garden"), ("Bridges", "/bridges"),
                       ("Blackbox", "/blackbox"), ("Loops", "/loops")]:
        assert f"<h2>{name}</h2>" in page
        assert f'href="{href}"' in page


def test_showcase_page_shows_live_bridges_and_garden_counts(tmp_path):
    from memsub.vault_serve import render_showcase_page

    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_showcase_page(idx, None, bridges_count=7, garden_published=2).decode("utf-8")
    assert "<strong>7</strong> found" in page
    assert "<strong>2</strong> published" in page


def test_showcase_page_honest_empty_states_when_blackbox_and_loops_not_scanned(tmp_path):
    from memsub.vault_serve import render_showcase_page

    idx, graph, sim = _fixture_vault(tmp_path)
    page = render_showcase_page(idx, None).decode("utf-8")
    assert "not yet scanned" in page  # blackbox: no --db
    assert "not scanned (--projects-dir)" in page  # loops: not opted in


def _garden_note(publish=None, channel=None, publish_inferred=None, extra_fm="") -> str:
    lines = ['---', 'namespace: "topics"']
    if publish is not None:
        lines.append(f'publish: "{publish}"')
    if channel is not None:
        lines.append(f'channel: "{channel}"')
    if publish_inferred is not None:
        lines.append(f'publish_inferred: "{publish_inferred}"')
    if extra_fm:
        lines.append(extra_fm)
    lines.append("---")
    return "\n".join(lines) + "\n\n# A published idea\n\n## Content\n\nSome public-safe prose.\n"


def test_garden_page_shows_only_published_notes_with_status_panel(tmp_path):
    from memsub.vault_serve import build_index, render_garden_page
    from memsub import garden

    root = tmp_path / "vault"
    _write(root, "public.md", _garden_note(publish="true", channel="user_stated"))
    _write(root, "private.md", _garden_note(publish=None))  # never enters the publish set
    idx = build_index(root)
    notes = garden.resolve_publish_set(root)

    page = render_garden_page(idx, notes).decode("utf-8")
    assert '<a class="nav-tab active" href="/garden">garden</a>' in page
    assert "Some public-safe prose" in page
    # published notes link through the garden-scoped route, never the
    # full-vault /note/ renderer (see FIX 1 / render_garden_note_page)
    assert '<a href="/garden/note/public.md">' in page
    assert "private.md" not in page
    assert "evergreen" in page and "verified" in page
    assert '<div class="tile-value accent" data-count="1">1</div>' in page  # published tile


def test_garden_note_route_never_leaks_unpublished_linked_note(tmp_path):
    """FIX 1 regression: a published note that wikilinks an unpublished note
    must not leak that note's content or provenance/audit text when served
    through the /garden/note/ route -- the leak class commit 054dd66 fixed
    in garden.py's own static-site renderer, reopened when the Garden tab's
    note view went through render_note_page (full-vault, no publish/private
    concept: Constellation embeds a linked note's full text as JSON, Details
    shows frontmatter/provenance/audit) instead of a garden-scoped route."""
    import socketserver

    from memsub import garden

    root = tmp_path / "vault"
    # namespace+created on both notes so both are "content notes" the graph
    # connects (is_content_note) -- this is what makes public.md's
    # Constellation include a node for secret.md at all, the exact
    # condition the leak needs.
    _write(
        root, "public.md",
        '---\nnamespace: "topics"\ncreated: "2026-07-01T00:00:00"\n'
        'publish: "true"\nchannel: "user_stated"\n---\n\n'
        "# A published idea\n\n## Content\n\nSee [[secret]] for more.\n",
    )
    _write(
        root, "secret.md",
        '---\nid: "deadbeefdeadbeef"\nnamespace: "topics"\n'
        'created: "2026-07-01T00:00:00"\n---\n\n'
        "# A secret idea\n\n## Content\n\n"
        "VERY-SECRET-CONTENT-XYZ must never leak to the public garden.\n\n"
        "## Provenance\n\n"
        "- Run: `run-abcdef123456`\n"
        "- Session: `sess-abcdef123456`\n\n"
        "### Audit events\n\n"
        "- 2026-07-01T00:00:00Z  **created**  actor=agent\n",
    )
    idx = build_index(root)
    graph = build_graph_index(idx)
    sim = build_similarity_index(idx, linked_neighbors(graph))
    garden_notes = garden.resolve_publish_set(root)

    handler = make_handler(idx, None, graph, sim, None, None, None, garden_notes)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/garden/note/public.md")
        resp = conn.getresponse()
        assert resp.status == 200
        page = resp.read().decode("utf-8")

        # the unpublished note itself is never directly reachable either
        conn.request("GET", "/garden/note/secret.md")
        resp2 = conn.getresponse()
        assert resp2.status == 404
        resp2.read()
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert "VERY-SECRET-CONTENT-XYZ" not in page
    assert "run-abcdef123456" not in page
    assert "sess-abcdef123456" not in page
    assert "deadbeefdeadbeef" not in page
    assert "Constellation" not in page
    assert "Details (ids, provenance, audit log)" not in page
    assert "private" in page  # unresolved wikilink renders as the fixed private marker


def test_garden_page_awaiting_flag_note_is_excluded_from_the_preview_list(tmp_path):
    from memsub.vault_serve import build_index, render_garden_page
    from memsub import garden

    root = tmp_path / "vault"
    _write(root, "seed.md", _garden_note(publish="true", channel="llm_inferred", publish_inferred=None))
    idx = build_index(root)
    notes = garden.resolve_publish_set(root)

    page = render_garden_page(idx, notes).decode("utf-8")
    assert "Some public-safe prose" not in page  # blocked by the fail-closed gate, not shown as published
    assert '<div class="tile-label">awaiting publish_inferred flag</div>' in page


def test_garden_page_empty_state_when_nothing_published(tmp_path):
    from memsub.vault_serve import build_index, render_garden_page

    root = tmp_path / "vault"
    _write(root, "note.md", "# Just a note\n\nno frontmatter at all.")
    idx = build_index(root)

    page = render_garden_page(idx, {}).decode("utf-8")
    assert "No published notes yet" in page


def _make_bridge(a_id="a" * 40, b_id="b" * 40, score=0.5, why=None):
    from memsub.bridges import Bridge

    return Bridge(
        a_id=a_id, b_id=b_id,
        a_excerpt="Arjun switched Coldstart's inference to the Claude API.",
        b_excerpt="Lena Petrova finished the onboarding flow design.",
        a_bucket="entity:coldstart", b_bucket="entity:petrova",
        score=score, why=why,
    )


def test_bridges_page_renders_cards_and_filter_chips():
    from memsub.vault_serve import render_bridges_page

    bridges = [_make_bridge(), _make_bridge(a_id="c" * 40, b_id="d" * 40, score=0.3)]
    page = render_bridges_page(bridges, note_count=3).decode("utf-8")
    assert '<a class="nav-tab active" href="/bridges">bridges</a>' in page
    assert "2 unmade connections across your projects" in page
    assert 'class="bridge-card"' in page
    assert "entity:coldstart" in page and "entity:petrova" in page
    assert 'data-action="keep"' in page and 'data-action="dismiss"' in page


def test_bridges_page_empty_state():
    from memsub.vault_serve import render_bridges_page

    page = render_bridges_page([], note_count=0).decode("utf-8")
    assert "No candidate bridges yet" in page


def test_showcase_garden_bridges_routes_and_verdict_over_http(tmp_path):
    """End-to-end: showcase/garden/bridges wired through make_handler + a
    real bridges._ServeState, including a keep/dismiss verdict POST that
    persists to the cache file (memsub.bridges' own mechanism, reused)."""
    import json
    import socketserver

    from memsub.bridges import _ServeState, load_cache
    from memsub import garden

    root = tmp_path / "vault"
    _write(root, "public.md", _garden_note(publish="true", channel="user_stated"))
    idx = build_index(root)
    garden_notes = garden.resolve_publish_set(root)
    cache_path = tmp_path / "bridges.json"
    bridges_state = _ServeState(bridges=[_make_bridge()], cache_path=cache_path)

    handler = make_handler(idx, None, None, None, None, None, bridges_state, garden_notes)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)

        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"<strong>1</strong> found" in resp.read()  # showcase's live bridges count

        conn.request("GET", "/garden")
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"Some public-safe prose" in resp.read()

        conn.request("GET", "/bridges")
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"bridge-card" in resp.read()

        pair = f"{'a' * 40}|{'b' * 40}"
        conn.request(
            "POST", "/api/verdict",
            body=json.dumps({"pair": pair, "verdict": "dismiss"}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        assert json.loads(resp.read()) == {"ok": True}

        conn.request("GET", "/bridges")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert b"No candidate bridges yet" in body  # the one bridge was dismissed

        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert load_cache(cache_path)["verdicts"] == {pair: "dismiss"}
