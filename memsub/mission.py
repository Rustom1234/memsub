"""Mission Control: a phone-first personal ops room, `memsub mission serve`.

One dark page that answers, at a glance: what happened, what is running,
what is waiting on me, what did it cost. Stdlib-only HTTP server (same
family as vault_serve.py's dashboard, bolder: this is an ops room, not a
read-only vault viewer), auto-refreshing via a plain `<meta refresh>` every
120s -- no client-side JS, no websockets, nothing to keep alive.

Seven sections, top to bottom, each independently fail-soft: WAITING ON
YOU (docs/examples/waiting-on-you.example.json documents the file format),
TODAY'S SPEND (a background-refreshed `npx ccusage` cache, at most once
per 30 minutes), AGENTS NOW (recent Claude Code session activity), OPEN
LOOPS (memsub.loops.scan_loops, import-guarded), BLACKBOX (last sweep from
the blackbox_sweeps table, read-only), REPOS (git status of ~/projects),
and a LINKS ROW to the other memsub servers.

Fail-soft everywhere: every render_*_section() function catches its own
exceptions and renders "unavailable" rather than raising, and
render_mission_page() wraps the whole assembly in one more try/except as a
last line of defense -- a broken section can never turn into a 500. See
docs/mission-2026-07-22.md for the full spec and the waiting-on-you.json
format.
"""

from __future__ import annotations

import datetime
import html
import json
import os
import socketserver
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path

MISSION_VERSION = "0.1.0"

# --------------------------------------------------------------------------- #
# Defaults (each overridable per-call so tests never touch real dotfiles)
# --------------------------------------------------------------------------- #
DEFAULT_WAITING_ON_YOU = Path.home() / ".memsub" / "waiting-on-you.json"

SPEND_REFRESH_INTERVAL_S = 1800  # `npx ccusage` runs at most this often
SPEND_STALE_RENDER_S = 3600  # a cache older than this renders "refreshing"

CLAUDE_PROJECT_DIRS_DEFAULT = [
    Path.home() / ".claude" / "projects",
    Path.home() / ".claude-remote" / "projects",
]
AGENTS_WINDOW_S = 24 * 3600
AGENTS_LIVE_S = 5 * 60
AGENTS_MAX_ROWS = 8

DEFAULT_PROJECTS_ROOT = Path.home() / "projects"
REPOS_MAX = 8
GIT_TIMEOUT_S = 2

LOOPS_URL = "http://127.0.0.1:8766/loops"
BLACKBOX_URL = "http://127.0.0.1:8766/blackbox"
LINKS = [
    ("dashboard", "http://127.0.0.1:8766"),
    ("garden preview", "http://127.0.0.1:8767"),
    ("bridges", "http://127.0.0.1:8768"),
    ("product site", "http://127.0.0.1:8765"),
]

# loops.py is a sibling module this file only ever reads from (never
# mutates); guarded so `import memsub.mission` still works standalone if
# loops.py is ever removed or fails to import for any reason.
try:
    from . import loops as _loops
except Exception:  # pragma: no cover - defensive, not exercised in this repo
    _loops = None


def default_db_path() -> Path:
    """Duplicated from cli.py's default_db_path (same $MEMSUB_DB env var,
    same ~/.memsub/memory.db fallback) rather than imported, so mission.py
    never has to import cli.py (cli.py imports mission.py, not the other
    way around)."""
    env = os.environ.get("MEMSUB_DB")
    return Path(env) if env else Path.home() / ".memsub" / "memory.db"


def default_cache_dir() -> Path:
    env = os.environ.get("MEMSUB_MISSION_CACHE_DIR")
    return Path(env) if env else Path.home() / ".memsub" / "mission-cache"


# --------------------------------------------------------------------------- #
# 1. WAITING ON YOU
# --------------------------------------------------------------------------- #
def load_waiting_on_you(path: Path) -> list[dict] | None:
    """None means "file absent, hide the section entirely". A list (maybe
    empty) means the file exists and parsed. Raises on malformed content
    (not a JSON list, or an entry missing "title") so the caller can render
    "unavailable" instead of silently showing garbage."""
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("waiting-on-you.json must be a JSON list")
    items = []
    for entry in data:
        if not isinstance(entry, dict) or "title" not in entry:
            raise ValueError('each waiting-on-you entry needs at least a "title"')
        items.append(entry)
    return items


def render_waiting_section(path: Path | None = None) -> str:
    path = Path(path) if path is not None else DEFAULT_WAITING_ON_YOU
    try:
        items = load_waiting_on_you(path)
    except Exception:
        return _section("WAITING ON YOU", '<p class="unavailable">unavailable</p>')
    if items is None:
        return ""  # file absent = section hidden, not even a header
    if not items:
        return _section("WAITING ON YOU", '<p class="tile-text">nothing waiting</p>')
    rows = []
    for it in items:
        title = html.escape(str(it.get("title", "")))
        detail = html.escape(str(it.get("detail", "")))
        since = html.escape(str(it.get("since", "")))
        link = it.get("link")
        title_html = (
            f'<a href="{html.escape(str(link))}">{title}</a>' if link else title
        )
        rows.append(
            f'<li><div class="wy-title">{title_html}</div>'
            f'<div class="wy-detail">{detail}</div>'
            f'<div class="wy-since">since {since}</div></li>'
        )
    return _section("WAITING ON YOU", f'<ul class="waiting-list">{"".join(rows)}</ul>')


# --------------------------------------------------------------------------- #
# 2. TODAY'S SPEND (background-refreshed cache)
# --------------------------------------------------------------------------- #
def spend_cache_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "spend.json"


def _run_ccusage(argv: list[str]) -> str:
    out = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=True)
    return out.stdout


def _summarize_spend(data: dict) -> dict:
    """Best-effort shape reader for `npx ccusage@latest daily --json`'s
    {"daily": [{"period": "YYYY-MM-DD", "totalCost": ..., "modelBreakdowns":
    [{"modelName": ..., "cost": ...}, ...]}], ...} -- picks today's entry by
    date, falling back to the most recent entry present if today's isn't
    there yet (e.g. right after local midnight)."""
    daily = data.get("daily") or []
    today_str = datetime.date.today().isoformat()
    today = next((d for d in daily if d.get("period") == today_str), None)
    if today is None and daily:
        today = daily[-1]
    if today is None:
        return {"total_cost": 0.0, "top_model": None, "found": False}
    total_cost = today.get("totalCost", 0.0)
    breakdowns = today.get("modelBreakdowns") or []
    top_model = None
    if breakdowns:
        top = max(breakdowns, key=lambda m: m.get("cost", 0.0))
        top_model = top.get("modelName")
    return {"total_cost": total_cost, "top_model": top_model, "found": True}


def refresh_spend_cache(cache_dir: Path, runner=None) -> None:
    """Runs the ccusage command (via `runner`, injectable for tests) and
    writes the summarized result to cache_dir/spend.json. Never raises: a
    runner failure or unparseable output is recorded as {"error": ...} so
    the render side still has a cache file to read (and shows "refreshing"
    rather than crashing)."""
    runner = runner or _run_ccusage
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw = runner(["npx", "ccusage@latest", "daily", "--json"])
        summary = _summarize_spend(json.loads(raw))
    except Exception as exc:
        summary = {"error": str(exc)}
    summary["ts"] = time.time()
    tmp = cache_dir / "spend.json.tmp"
    tmp.write_text(json.dumps(summary))
    tmp.replace(spend_cache_path(cache_dir))


def maybe_refresh_spend_async(
    cache_dir: Path,
    runner=None,
    max_age_s: float = SPEND_REFRESH_INTERVAL_S,
    thread_cls=threading.Thread,
) -> bool:
    """Kicks off a refresh (via thread_cls, default a real background
    thread -- page render never blocks on it) if the cache is missing or
    older than max_age_s. Returns whether a refresh was started, so tests
    can assert "no npx call when cache fresh" without touching threads at
    all (pass a fresh cache and a runner that raises if called)."""
    path = spend_cache_path(cache_dir)
    try:
        fresh = (time.time() - path.stat().st_mtime) < max_age_s
    except OSError:
        fresh = False
    if fresh:
        return False
    t = thread_cls(target=refresh_spend_cache, args=(cache_dir, runner), daemon=True)
    t.start()
    return True


def render_spend_section(cache_dir: Path | None = None) -> str:
    cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    try:
        maybe_refresh_spend_async(cache_dir)
    except Exception:
        pass  # kicking off a refresh must never block or break page render
    return _section("TODAY'S SPEND", _spend_body(cache_dir))


def _spend_body(cache_dir: Path) -> str:
    try:
        path = spend_cache_path(cache_dir)
        if not path.exists():
            return '<p class="tile-text">spend: refreshing</p>'
        age = time.time() - path.stat().st_mtime
        data = json.loads(path.read_text())
        if age > SPEND_STALE_RENDER_S or data.get("error") or not data.get("found", True):
            return '<p class="tile-text">spend: refreshing</p>'
        cost = float(data.get("total_cost", 0.0))
        model = html.escape(str(data.get("top_model") or "unknown"))
        return (
            f'<div class="tile"><div class="tile-value accent">${cost:.2f}</div>'
            f'<div class="tile-label">today -- top model: {model}</div></div>'
        )
    except Exception:
        return '<p class="unavailable">unavailable</p>'


# --------------------------------------------------------------------------- #
# 3. AGENTS NOW
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AgentSession:
    project: str
    mtime: float


def _dedup_dirs(dirs: list[Path]) -> list[Path]:
    """Dedup by (device, inode): ~/.claude/projects and ~/.claude-remote/projects
    can be the same directory via a symlink or bind mount, and a session
    file must only be counted once."""
    seen: set[tuple[int, int]] = set()
    out: list[Path] = []
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        try:
            st = os.stat(d)
            key = (st.st_dev, st.st_ino)
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def scan_agent_sessions(
    dirs: list[Path] | None = None, now: float | None = None
) -> list[AgentSession]:
    now = now if now is not None else time.time()
    search_dirs = _dedup_dirs(dirs if dirs is not None else CLAUDE_PROJECT_DIRS_DEFAULT)
    cutoff = now - AGENTS_WINDOW_S
    sessions: list[AgentSession] = []
    for d in search_dirs:
        for path in d.rglob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                continue
            sessions.append(AgentSession(project=path.parent.name, mtime=mtime))
    sessions.sort(key=lambda s: s.mtime, reverse=True)
    return sessions


def render_agents_section(dirs: list[Path] | None = None, now: float | None = None) -> str:
    try:
        now = now if now is not None else time.time()
        sessions = scan_agent_sessions(dirs, now)
        rows = []
        for s in sessions[:AGENTS_MAX_ROWS]:
            mins = int((now - s.mtime) // 60)
            live = ' <span class="badge-live">LIVE</span>' if (now - s.mtime) < AGENTS_LIVE_S else ""
            rows.append(f"<li>{html.escape(s.project)} -- {mins}m ago{live}</li>")
        body = (
            f'<p class="count">{len(sessions)} active session(s) in the last 24h</p>'
            f'<ul class="agents-list">{"".join(rows) or "<li>none</li>"}</ul>'
        )
        return _section("AGENTS NOW", body)
    except Exception:
        return _section("AGENTS NOW", '<p class="unavailable">unavailable</p>')


# --------------------------------------------------------------------------- #
# 4. OPEN LOOPS (reuses memsub.loops.scan_loops if importable)
# --------------------------------------------------------------------------- #
def render_loops_section() -> str:
    if _loops is None:
        return _section(
            "OPEN LOOPS", '<p class="unavailable">unavailable (loops module not present)</p>',
            link=LOOPS_URL,
        )
    try:
        findings = _loops.scan_loops()
        interrupt = [f for f in findings if f.severity == _loops.SEVERITY_INTERRUPT]
        top3 = interrupt[:3]
        items = "".join(f"<li>{html.escape(f.reason)}</li>" for f in top3)
        body = (
            f'<p class="count">{len(interrupt)} interrupt-tier finding(s)</p>'
            f'<ul class="loops-list">{items or "<li>none</li>"}</ul>'
        )
        return _section("OPEN LOOPS", body, link=LOOPS_URL)
    except Exception:
        return _section("OPEN LOOPS", '<p class="unavailable">unavailable</p>', link=LOOPS_URL)


# --------------------------------------------------------------------------- #
# 5. BLACKBOX (read-only query against blackbox_sweeps)
# --------------------------------------------------------------------------- #
def query_blackbox(db_path: Path) -> dict:
    """Read-only (mode=ro URI: never creates or writes the db file). Raises
    if the db or table doesn't exist -- caller renders "unavailable"."""
    uri = f"file:{Path(db_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2)
    try:
        row = conn.execute(
            "SELECT ts, incidents FROM blackbox_sweeps ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"ts": None, "incidents": 0}
    return {"ts": row[0], "incidents": row[1]}


def render_blackbox_section(db_path: Path | None = None) -> str:
    db_path = Path(db_path) if db_path is not None else default_db_path()
    try:
        info = query_blackbox(db_path)
        if info["ts"] is None:
            body = '<p class="tile-text">no sweep recorded yet</p>'
        else:
            when = datetime.datetime.fromtimestamp(info["ts"]).strftime("%Y-%m-%d %H:%M")
            body = (
                f'<div class="tile"><div class="tile-value">{info["incidents"]}</div>'
                f'<div class="tile-label">incident(s) -- last sweep {html.escape(when)}</div></div>'
            )
        return _section("BLACKBOX", body, link=BLACKBOX_URL)
    except Exception:
        return _section("BLACKBOX", '<p class="unavailable">unavailable</p>', link=BLACKBOX_URL)


# --------------------------------------------------------------------------- #
# 6. REPOS (git status of ~/projects, fail-soft per repo)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RepoStatus:
    name: str
    branch: str
    dirty: int  # -1 means "unknown, git status failed"
    age_s: float | None  # None means "unknown, no commit or git log failed"


def _git(repo: Path, *args: str) -> str | None:
    """None on any failure (missing git, timeout, non-zero exit) -- never
    raises, so one uncooperative repo can't take the whole section down."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def scan_repos(root: Path | None = None, max_repos: int = REPOS_MAX) -> list[RepoStatus]:
    root = Path(root) if root is not None else DEFAULT_PROJECTS_ROOT
    if not root.is_dir():
        return []
    try:
        candidates = [d for d in root.iterdir() if d.is_dir() and (d / ".git").exists()]
    except OSError:
        return []
    scored: list[tuple[float, RepoStatus]] = []
    for repo in candidates:
        ts_raw = _git(repo, "log", "-1", "--format=%ct")
        commit_ts = float(ts_raw) if ts_raw else 0.0
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "unavailable"
        status_out = _git(repo, "status", "--porcelain")
        dirty = len(status_out.splitlines()) if status_out is not None else -1
        age_s = (time.time() - commit_ts) if commit_ts else None
        scored.append((commit_ts, RepoStatus(name=repo.name, branch=branch, dirty=dirty, age_s=age_s)))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [rs for _, rs in scored[:max_repos]]


def _format_age(age_s: float | None) -> str:
    if age_s is None:
        return "unknown"
    days = age_s / 86400
    if days < 1:
        return f"{max(int(age_s // 3600), 0)}h ago"
    return f"{int(days)}d ago"


def render_repos_section(root: Path | None = None) -> str:
    try:
        repos = scan_repos(root)
        if not repos:
            return _section("REPOS", '<p class="tile-text">no repos found</p>')
        rows = []
        for r in repos:
            dirty_txt = f"{r.dirty} dirty" if r.dirty >= 0 else "dirty: unavailable"
            rows.append(
                f"<li>{html.escape(r.name)} -- {html.escape(r.branch)} -- "
                f"{dirty_txt} -- {_format_age(r.age_s)}</li>"
            )
        return _section("REPOS", f'<ul class="repos-list">{"".join(rows)}</ul>')
    except Exception:
        return _section("REPOS", '<p class="unavailable">unavailable</p>')


# --------------------------------------------------------------------------- #
# 7. LINKS ROW
# --------------------------------------------------------------------------- #
def render_links_section() -> str:
    pills = "".join(
        f'<a class="link-pill" href="{url}">{html.escape(label)}</a>' for label, url in LINKS
    )
    return f'<div class="links-row">{pills}</div>'


# --------------------------------------------------------------------------- #
# Page chrome: dark, phone-first (single column base, grid from 720px up)
# --------------------------------------------------------------------------- #
def _section(title: str, body: str, link: str | None = None) -> str:
    # title is always one of this module's own hardcoded section names
    # (never user input), so it is not HTML-escaped here.
    link_html = f' <a class="section-link" href="{link}">open -&gt;</a>' if link else ""
    return f'<section class="mc-section"><h2>{title}{link_html}</h2>{body}</section>'


_CSS = """
:root {
  color-scheme: dark;
  --bg: #0a0b0d; --bg-raised: #14161a; --bg-card: #191c22; --border: #262a33;
  --text: #d8dbe2; --text-dim: #8b909c; --text-faint: #5a5f6b;
  --accent: #6fd6ff; --mono: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  --sans: -apple-system, system-ui, sans-serif;
}
* { box-sizing: border-box; }
body {
  background: var(--bg); color: var(--text); font-family: var(--sans);
  line-height: 1.5; margin: 0; padding: 0.75rem 0.75rem 3rem;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1, h2 { font-family: var(--mono); color: #f0f2f6; letter-spacing: -0.01em; }
h1 { font-size: 1.3rem; margin: 0.2rem 0 1rem; }
h2 { font-size: 0.95rem; margin: 0 0 0.6rem; text-transform: uppercase; letter-spacing: 0.04em; }
.page { max-width: 900px; margin: 0 auto; }
.mc-grid { display: grid; grid-template-columns: 1fr; gap: 0.85rem; }
.mc-section {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px;
  padding: 1rem 1.1rem;
}
.section-link { float: right; font-size: 0.78rem; color: var(--text-dim); font-family: var(--sans); font-weight: normal; text-transform: none; letter-spacing: normal; }
.section-link:hover { color: var(--accent); }
.tile { padding: 0.4rem 0; }
.tile-value { font-family: var(--mono); font-size: 1.8rem; font-weight: 600; }
.tile-value.accent { color: var(--accent); }
.tile-label { font-size: 0.78rem; color: var(--text-dim); margin-top: 0.2rem; }
.tile-text { color: var(--text-dim); margin: 0; }
.unavailable { color: var(--text-faint); font-style: italic; margin: 0; }
.count { color: var(--text-dim); font-size: 0.85rem; margin: 0 0 0.5rem; }
ul { list-style: none; margin: 0; padding: 0; }
li { padding: 0.35rem 0; border-top: 1px solid var(--border); font-size: 0.9rem; }
li:first-child { border-top: none; }
.badge-live { color: #ff6b6b; font-family: var(--mono); font-size: 0.72rem; border: 1px solid #ff6b6b; border-radius: 4px; padding: 0 0.3rem; }
.wy-title { font-weight: 600; }
.wy-detail { color: var(--text-dim); font-size: 0.85rem; }
.wy-since { color: var(--text-faint); font-size: 0.75rem; }
.links-row { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.link-pill { border: 1px solid var(--border); border-radius: 999px; padding: 0.4rem 0.9rem; font-size: 0.85rem; font-family: var(--mono); }
.link-pill:hover { border-color: var(--accent); text-decoration: none; }
.stamp { text-align: center; color: var(--text-faint); font-size: 0.72rem; font-family: var(--mono); margin-top: 1.5rem; }
@media (min-width: 720px) {
  .mc-grid { grid-template-columns: repeat(2, 1fr); }
}
"""

_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120">
<title>Mission Control</title>
<style>{css}</style>
</head>
<body>
<div class="page">
<h1>Mission Control</h1>
<div class="mc-grid">{body}</div>
<div class="stamp">memsub mission v{version}</div>
</div>
</body>
</html>
"""


def render_mission_page(
    waiting_path: Path | None = None,
    cache_dir: Path | None = None,
    claude_project_dirs: list[Path] | None = None,
    projects_root: Path | None = None,
    db_path: Path | None = None,
) -> str:
    """Assembles all seven sections. Each render_*_section() is already
    fail-soft on its own; this wraps the whole assembly in one more
    try/except so a bug anywhere here still yields a 200 page, never a
    500 (the last line of defense the module docstring promises)."""
    try:
        sections = [
            render_waiting_section(waiting_path),
            render_spend_section(cache_dir),
            render_agents_section(claude_project_dirs),
            render_loops_section(),
            render_blackbox_section(db_path),
            render_repos_section(projects_root),
            render_links_section(),
        ]
        body = "".join(s for s in sections if s)
    except Exception:
        body = '<section class="mc-section"><p class="unavailable">mission control: unavailable</p></section>'
    return _PAGE_TEMPLATE.format(css=_CSS, body=body, version=MISSION_VERSION)


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #
def make_handler(**page_kwargs) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"memsub-mission/{MISSION_VERSION}"

        def log_message(self, fmt, *args):  # quieter default logging
            pass

        def do_GET(self) -> None:  # noqa: N802 (stdlib-mandated name)
            try:
                body = render_mission_page(**page_kwargs).encode("utf-8")
            except Exception:
                body = b"<html><body>mission control: unavailable</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve_mission(host: str = "127.0.0.1", port: int = 8769, **page_kwargs) -> int:
    handler = make_handler(**page_kwargs)
    with socketserver.ThreadingTCPServer((host, port), handler) as httpd:
        print(f"memsub mission serve: listening on http://{host}:{port} (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
