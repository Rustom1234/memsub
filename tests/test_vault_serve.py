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
    assert '<a class="nav-btn" href="/ask">ask</a>' in page


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
    handler = make_handler(idx, db_path, graph, sim, backend)
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
