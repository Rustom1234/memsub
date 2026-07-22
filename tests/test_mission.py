"""`memsub mission serve`: phone-first personal ops room.

Fixture-only (no real ~/.memsub, ~/.claude*/projects, or ~/projects
touched): every section is exercised through explicit path/dir overrides.
"""

from __future__ import annotations

import http.client
import json
import os
import socketserver
import sqlite3
import subprocess
import threading
import time

import pytest

from memsub import mission


# --------------------------------------------------------------------------- #
# waiting-on-you.json parser
# --------------------------------------------------------------------------- #
def test_load_waiting_on_you_missing_file_returns_none(tmp_path):
    assert mission.load_waiting_on_you(tmp_path / "nope.json") is None


def test_load_waiting_on_you_parses_valid_list(tmp_path):
    path = tmp_path / "waiting-on-you.json"
    path.write_text(json.dumps([
        {"title": "Approve the plan", "detail": "needs a go/no-go", "since": "2026-07-22T09:00:00"},
        {"title": "Read the doc", "link": "https://example.com"},
    ]))
    items = mission.load_waiting_on_you(path)
    assert len(items) == 2
    assert items[0]["title"] == "Approve the plan"
    assert items[1]["link"] == "https://example.com"


def test_load_waiting_on_you_rejects_non_list(tmp_path):
    path = tmp_path / "waiting-on-you.json"
    path.write_text(json.dumps({"title": "not a list"}))
    with pytest.raises(ValueError):
        mission.load_waiting_on_you(path)


def test_load_waiting_on_you_rejects_entry_without_title(tmp_path):
    path = tmp_path / "waiting-on-you.json"
    path.write_text(json.dumps([{"detail": "no title here"}]))
    with pytest.raises(ValueError):
        mission.load_waiting_on_you(path)


def test_example_fixture_in_docs_is_valid(tmp_path):
    # docs/examples/waiting-on-you.example.json must itself be a valid,
    # loadable instance of the format it documents.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    example = os.path.join(here, "docs", "examples", "waiting-on-you.example.json")
    items = mission.load_waiting_on_you(example)
    assert items
    assert all("title" in it for it in items)


def test_render_waiting_section_hidden_when_file_absent(tmp_path):
    assert mission.render_waiting_section(tmp_path / "nope.json") == ""


def test_render_waiting_section_shows_header_and_entries(tmp_path):
    path = tmp_path / "waiting-on-you.json"
    path.write_text(json.dumps([{"title": "Do the thing"}]))
    html = mission.render_waiting_section(path)
    assert "WAITING ON YOU" in html
    assert "Do the thing" in html


def test_render_waiting_section_unavailable_on_malformed_file(tmp_path):
    path = tmp_path / "waiting-on-you.json"
    path.write_text("not json at all")
    html = mission.render_waiting_section(path)
    assert "WAITING ON YOU" in html
    assert "unavailable" in html


# --------------------------------------------------------------------------- #
# spend cache: at most once per interval, fake runner injected
# --------------------------------------------------------------------------- #
def test_maybe_refresh_spend_async_skips_call_when_cache_fresh(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "spend.json").write_text(json.dumps({"total_cost": 1.0, "top_model": "x", "found": True, "ts": time.time()}))

    def _boom(argv):
        raise AssertionError("runner must not be called when cache is fresh")

    started = mission.maybe_refresh_spend_async(cache_dir, runner=_boom, max_age_s=SPEND_HUGE_MAX_AGE)
    assert started is False


SPEND_HUGE_MAX_AGE = 10_000_000  # any real-world cache mtime is "fresh" under this


def test_maybe_refresh_spend_async_runs_when_cache_stale(tmp_path):
    cache_dir = tmp_path / "cache"
    calls = []

    def _fake_runner(argv):
        calls.append(argv)
        return json.dumps({
            "daily": [{"period": time.strftime("%Y-%m-%d"), "totalCost": 4.5,
                       "modelBreakdowns": [{"modelName": "sonnet", "cost": 4.5}]}]
        })

    class _SyncThread:
        """Runs the target immediately instead of on a real thread, so the
        test is deterministic without sleeping or polling."""

        def __init__(self, target, args=(), daemon=None):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

    started = mission.maybe_refresh_spend_async(
        cache_dir, runner=_fake_runner, max_age_s=0, thread_cls=_SyncThread
    )
    assert started is True
    assert calls == [["npx", "ccusage@latest", "daily", "--json"]]

    data = json.loads(mission.spend_cache_path(cache_dir).read_text())
    assert data["total_cost"] == 4.5
    assert data["top_model"] == "sonnet"


def test_refresh_spend_cache_records_error_without_raising(tmp_path):
    cache_dir = tmp_path / "cache"

    def _fail(argv):
        raise RuntimeError("npx not found")

    mission.refresh_spend_cache(cache_dir, runner=_fail)
    data = json.loads(mission.spend_cache_path(cache_dir).read_text())
    assert "error" in data


def test_render_spend_section_shows_refreshing_when_cache_missing(tmp_path):
    cache_dir = tmp_path / "cache"
    html = mission.render_spend_section(cache_dir)
    assert "TODAY'S SPEND" in html
    assert "refreshing" in html


def test_render_spend_section_renders_cached_value(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "spend.json").write_text(json.dumps({"total_cost": 12.34, "top_model": "opus", "found": True}))
    html = mission.render_spend_section(cache_dir)
    assert "12.34" in html
    assert "opus" in html


# --------------------------------------------------------------------------- #
# agents now
# --------------------------------------------------------------------------- #
def test_scan_agent_sessions_finds_recent_and_skips_old(tmp_path):
    now = time.time()
    proj_a = tmp_path / "claude" / "projects" / "proj-a"
    proj_a.mkdir(parents=True)
    recent = proj_a / "recent.jsonl"
    recent.write_text("{}\n")
    os.utime(recent, (now - 60, now - 60))

    stale = proj_a / "stale.jsonl"
    stale.write_text("{}\n")
    old = now - 2 * mission.AGENTS_WINDOW_S
    os.utime(stale, (old, old))

    sessions = mission.scan_agent_sessions([proj_a.parent.parent / "projects"], now=now)
    assert len(sessions) == 1
    assert sessions[0].project == "proj-a"


def test_scan_agent_sessions_dedups_by_inode(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    session = real / "s.jsonl"
    session.write_text("{}\n")
    link = tmp_path / "link"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")
    sessions = mission.scan_agent_sessions([real, link])
    assert len(sessions) == 1


def test_render_agents_section_shows_live_badge_under_five_minutes(tmp_path):
    now = time.time()
    proj = tmp_path / "projects" / "live-proj"
    proj.mkdir(parents=True)
    f = proj / "s.jsonl"
    f.write_text("{}\n")
    os.utime(f, (now - 30, now - 30))
    html = mission.render_agents_section([tmp_path / "projects"], now=now)
    assert "AGENTS NOW" in html
    assert "LIVE" in html
    assert "live-proj" in html


# --------------------------------------------------------------------------- #
# open loops (guarded reuse of memsub.loops.scan_loops)
# --------------------------------------------------------------------------- #
def test_render_loops_section_reports_interrupt_count(monkeypatch):
    from memsub.loops import Finding, SEVERITY_INTERRUPT

    fake_findings = [
        Finding(session="s1", ts=time.time(), reason="unanswered ask: hi",
                severity=SEVERITY_INTERRUPT, confidence="certain", signal="unanswered_ask"),
    ]
    monkeypatch.setattr(mission._loops, "scan_loops", lambda *a, **k: fake_findings)
    html = mission.render_loops_section()
    assert "OPEN LOOPS" in html
    assert "1 interrupt-tier" in html
    assert "unanswered ask: hi" in html


def test_render_loops_section_unavailable_when_scan_throws(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(mission._loops, "scan_loops", _boom)
    html = mission.render_loops_section()
    assert "OPEN LOOPS" in html
    assert "unavailable" in html


def test_render_loops_section_unavailable_when_module_absent(monkeypatch):
    monkeypatch.setattr(mission, "_loops", None)
    html = mission.render_loops_section()
    assert "OPEN LOOPS" in html
    assert "unavailable" in html


# --------------------------------------------------------------------------- #
# blackbox (read-only query against blackbox_sweeps)
# --------------------------------------------------------------------------- #
def test_query_blackbox_reads_last_sweep(tmp_path):
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE blackbox_sweeps (
            id INTEGER PRIMARY KEY, ts REAL NOT NULL, repos_checked INTEGER NOT NULL,
            states_indexed INTEGER NOT NULL, incidents INTEGER NOT NULL, elapsed_s REAL NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO blackbox_sweeps (ts, repos_checked, states_indexed, incidents, elapsed_s) "
        "VALUES (?, ?, ?, ?, ?)", (time.time(), 3, 10, 2, 0.5),
    )
    conn.commit()
    conn.close()

    info = mission.query_blackbox(db)
    assert info["incidents"] == 2
    assert info["ts"] is not None


def test_render_blackbox_section_unavailable_when_db_missing(tmp_path):
    html = mission.render_blackbox_section(tmp_path / "nope.db")
    assert "BLACKBOX" in html
    assert "unavailable" in html


# --------------------------------------------------------------------------- #
# repos
# --------------------------------------------------------------------------- #
def _init_repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=path, check=True)
    (path / "f.txt").write_text("hi")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def test_scan_repos_reports_branch_and_dirty_count(tmp_path):
    root = tmp_path / "projects"
    repo = root / "myrepo"
    _init_repo(repo)
    (repo / "dirty.txt").write_text("uncommitted")

    repos = mission.scan_repos(root)
    assert len(repos) == 1
    assert repos[0].name == "myrepo"
    assert repos[0].dirty == 1
    assert repos[0].age_s is not None


def test_scan_repos_empty_root_returns_empty_list(tmp_path):
    assert mission.scan_repos(tmp_path / "does-not-exist") == []


def test_render_repos_section_shows_header(tmp_path):
    root = tmp_path / "projects"
    _init_repo(root / "r1")
    html = mission.render_repos_section(root)
    assert "REPOS" in html
    assert "r1" in html


# --------------------------------------------------------------------------- #
# full page render: every section header present when fixtured/mocked
# --------------------------------------------------------------------------- #
def _fixtured_world(tmp_path, monkeypatch):
    waiting = tmp_path / "waiting-on-you.json"
    waiting.write_text(json.dumps([{"title": "Do the thing"}]))

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "spend.json").write_text(json.dumps({"total_cost": 3.5, "top_model": "sonnet", "found": True}))

    proj_dir = tmp_path / "claude-projects" / "some-proj"
    proj_dir.mkdir(parents=True)
    f = proj_dir / "s.jsonl"
    f.write_text("{}\n")

    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE blackbox_sweeps (id INTEGER PRIMARY KEY, ts REAL NOT NULL, "
        "repos_checked INTEGER NOT NULL, states_indexed INTEGER NOT NULL, "
        "incidents INTEGER NOT NULL, elapsed_s REAL NOT NULL);"
    )
    conn.execute(
        "INSERT INTO blackbox_sweeps (ts, repos_checked, states_indexed, incidents, elapsed_s) "
        "VALUES (?, 1, 1, 0, 0.1)", (time.time(),),
    )
    conn.commit()
    conn.close()

    repos_root = tmp_path / "projects"
    _init_repo(repos_root / "r1")

    return dict(
        waiting_path=waiting,
        cache_dir=cache_dir,
        claude_project_dirs=[proj_dir.parent],
        projects_root=repos_root,
        db_path=db,
    )


def test_render_mission_page_contains_every_section_header(tmp_path, monkeypatch):
    kwargs = _fixtured_world(tmp_path, monkeypatch)
    html = mission.render_mission_page(**kwargs)
    for header in ("WAITING ON YOU", "TODAY'S SPEND", "AGENTS NOW", "OPEN LOOPS", "BLACKBOX", "REPOS"):
        assert header in html
    for _label, url in mission.LINKS:
        assert url in html


def test_render_mission_page_has_meta_refresh_and_viewport(tmp_path):
    html = mission.render_mission_page(waiting_path=tmp_path / "nope.json")
    assert 'http-equiv="refresh" content="120"' in html
    assert "viewport" in html


# --------------------------------------------------------------------------- #
# fail-soft: a thrown section never yields a 500
# --------------------------------------------------------------------------- #
def test_page_render_survives_a_throwing_section(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mission, "render_repos_section", _boom)
    html = mission.render_mission_page(waiting_path=tmp_path / "nope.json")
    assert "unavailable" in html


def _get(port, tries=3, timeout=10):
    """GET / with a couple of retries: this sandbox's very first socket
    connection in a process can be slow (cold network-stack setup), so a
    single short-timeout attempt is flaky in isolation even though the
    server itself responds instantly once warmed up."""
    last_exc = None
    for _ in range(tries):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
            conn.request("GET", "/")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            return resp.status, body
        except (TimeoutError, OSError) as exc:
            last_exc = exc
    raise last_exc


def test_http_server_returns_200_even_when_a_section_throws(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mission, "render_blackbox_section", _boom)
    handler = mission.make_handler(waiting_path=tmp_path / "nope.json")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port)
        assert status == 200
        assert "unavailable" in body
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_http_server_serves_ok_response(tmp_path):
    handler = mission.make_handler(waiting_path=tmp_path / "nope.json")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port)
        assert status == 200
        assert "Mission Control" in body
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_render_page_isolates_a_throwing_section(monkeypatch):
    # One section raising must not blank the others (fail-soft per section).
    from memsub import mission
    def _boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(mission, "render_links_section", _boom)
    page = mission.render_mission_page()
    assert "section unavailable" in page
    assert "AGENTS NOW" in page  # a sibling section still rendered


def test_scan_repos_caps_git_calls_before_spawning(tmp_path, monkeypatch):
    from memsub import mission
    for i in range(12):
        d = tmp_path / f"repo{i}"
        (d / ".git").mkdir(parents=True)
    calls = {"n": 0}
    real_git = mission._git
    def _counting_git(repo, *args):
        calls["n"] += 1
        return "" if args[0] == "status" else ("main" if args[0] == "rev-parse" else "0")
    monkeypatch.setattr(mission, "_git", _counting_git)
    out = mission.scan_repos(root=tmp_path, max_repos=8)
    assert len(out) == 8
    assert calls["n"] == 8 * 3  # exactly 3 git calls per kept repo, none wasted
