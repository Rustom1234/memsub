"""Blackbox: the flight recorder as a product.

The one-sentence pitch (YC framing, 2026-07-22): your AI coding agents
produce and destroy more work than you can track; Blackbox records every
file state from every agent session, forever, so nothing an agent ever
wrote can be lost, and alarms when disk content silently regresses.

This module is the product layer over memsub/filehist.py: repo discovery,
the 60-second first-run scan, and the report that IS the first-run
experience. Kept import-light (stdlib + store + filehist) so the CLI can
ship standalone.
"""

from __future__ import annotations

import datetime
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import filehist
from .parsers import aider as aider_parser
from .parsers import codex as codex_parser
from .parsers import cursor as cursor_parser
from .store import EpisodicStore

# Where a stranger's code and agent transcripts most commonly live. Both
# lists are filtered to what actually exists at scan time (discover_repos
# skips missing roots, _default_transcript_dirs filters), so naming a dir
# that isn't there costs nothing.
# The current directory is always added to roots so `blackbox scan` run from
# inside any repo just works, wherever that repo lives.
_COMMON_CODE_DIRS = ["projects", "code", "src", "dev", "repos", "work",
                     "Documents/GitHub", "git", "go/src"]
DEFAULT_ROOTS = [Path.home() / d for d in _COMMON_CODE_DIRS]
DEFAULT_TRANSCRIPTS = [
    Path.home() / ".claude" / "projects",
    Path.home() / ".claude-remote" / "projects",
    Path.home() / ".config" / "claude" / "projects",
]

_SWEEP_SCHEMA = """
CREATE TABLE IF NOT EXISTS blackbox_sweeps (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    repos_checked INTEGER NOT NULL,
    states_indexed INTEGER NOT NULL,
    incidents INTEGER NOT NULL,
    elapsed_s REAL NOT NULL
);
"""


def format_ts(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _effective_roots(roots: list[str | Path] | None) -> list[str | Path]:
    """Explicit roots when given, else the common code dirs plus the current
    directory (so a scan from inside a repo works wherever that repo lives)."""
    if roots is not None:
        return roots
    return [*DEFAULT_ROOTS, Path.cwd()]


def discover_repos(roots: list[str | Path], max_depth: int = 3) -> list[Path]:
    """Git repos under roots (top-level .git dirs only, no nesting descent).

    Deduplicated by resolved path: the broadened default roots can overlap
    (e.g. cwd sitting inside ~/projects), and a repo must be checked once."""
    repos: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        stack = [(root, 0)]
        while stack:
            d, depth = stack.pop()
            # A broadened root (or cwd) can descend into directories this user
            # cannot stat (e.g. /tmp systemd-private-* dirs). A single
            # unreadable dir must skip, never abort the whole scan.
            try:
                is_repo = (d / ".git").exists()
            except OSError:
                continue
            if is_repo:
                resolved = d.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    repos.append(d)
                continue
            if depth >= max_depth:
                continue
            try:
                for child in d.iterdir():
                    if child.is_dir() and not child.name.startswith("."):
                        stack.append((child, depth + 1))
            except OSError:
                continue
    return sorted(set(repos))


def _tracked_files(repo: Path) -> list[str]:
    import subprocess

    out = subprocess.run(["git", "-C", str(repo), "ls-files"],
                         capture_output=True, text=True)
    return [str((repo / p)) for p in out.stdout.splitlines()]


@dataclass
class ScanReport:
    repos_checked: int = 0
    states_indexed: int = 0
    files_known: int = 0
    incidents: list = field(default_factory=list)
    elapsed_s: float = 0.0

    def render(self) -> str:
        lines = [
            f"blackbox scan: {self.states_indexed:,} new file state(s) indexed, "
            f"{self.repos_checked} repo(s) checked in {self.elapsed_s:.1f}s",
        ]
        if not self.incidents:
            lines.append("CLEAN: no file on disk is older than what your agents' transcripts prove existed.")
            return "\n".join(lines)
        lines.append(f"RECOVERABLE LOSS FOUND: {len(self.incidents)} file(s) have silently "
                     "regressed to an older state:")
        for inc in self.incidents:
            old = format_ts(inc.matched_ts)
            new = format_ts(inc.latest_ts)
            lines.append(f"  {inc.path}")
            lines.append(f"    disk = {old} state; transcripts prove {new} existed")
            lines.append(f"    recover: memsub blackbox restore '{inc.path}' --at '{new}'")
        return "\n".join(lines)


def scan(
    store: EpisodicStore,
    roots: list[str | Path] | None = None,
    transcripts: list[str | Path] | None = None,
    codex_home: str | Path | None = None,
    cursor_home: str | Path | None = None,
) -> ScanReport:
    """The first-run experience: index everything, check everything.

    Discovery order: Claude Code transcripts first (native, highest
    confidence), then Codex CLI and Cursor (each guarded: the tool being
    absent on this machine is a silent no-op), then Aider commits per
    discovered repo (git history, also high confidence). See
    memsub/parsers/*.py for each parser's own confidence notes.
    """
    import time as _time

    t0 = _time.monotonic()
    report = ScanReport()
    for tdir in (transcripts or _default_transcript_dirs()):
        if Path(tdir).is_dir():
            report.states_indexed += filehist.index_transcripts(store, tdir)
    report.states_indexed += codex_parser.index_sessions(store, codex_home)
    report.states_indexed += cursor_parser.index_storage(store, cursor_home)
    repos = discover_repos(_effective_roots(roots))
    for repo in repos:
        report.states_indexed += aider_parser.index_commits(store, repo)
        paths = _tracked_files(repo)
        report.files_known += len(paths)
        report.incidents.extend(filehist.check_rollbacks(store, paths))
        report.repos_checked += 1
    report.elapsed_s = _time.monotonic() - t0
    _record_sweep(store, report)
    return report


def _default_transcript_dirs() -> list[Path]:
    return [d for d in DEFAULT_TRANSCRIPTS if d.is_dir()]


def _record_sweep(store: EpisodicStore, report: ScanReport) -> None:
    import time as _time

    conn = filehist._conn(store)
    conn.executescript(_SWEEP_SCHEMA)
    conn.execute(
        "INSERT INTO blackbox_sweeps (ts, repos_checked, states_indexed, incidents, elapsed_s)"
        " VALUES (?, ?, ?, ?, ?)",
        (_time.time(), report.repos_checked, report.states_indexed, len(report.incidents), report.elapsed_s),
    )
    conn.commit()


def parse_timestamp(text: str) -> float:
    """Parse a --at value: 'YYYY-MM-DD HH:MM' (the minute-precision format
    ScanReport.render()'s recovery commands print), or full ISO 8601.

    The minute form is checked first and, if matched, treated as inclusive
    of that whole minute (+59.999999s): pasting the exact string a recovery
    command printed must always find the write it was pointing at, even
    though the real write happened at some second within that minute, not
    exactly on it. This has to be checked before falling back to
    datetime.fromisoformat(), because Python's fromisoformat() also accepts
    a bare 'YYYY-MM-DD HH:MM' as valid ISO 8601 (no seconds), which would
    otherwise silently take the minute's first instant instead. Raises
    ValueError on anything else."""
    try:
        dt = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
        return dt.timestamp() + 59.999999
    except ValueError:
        pass
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def restore_file(
    store: EpisodicStore, path: str | Path, at_ts: float | None = None
) -> tuple[float, str] | None:
    """Reconstruct path's content as of at_ts (or the latest known state if
    at_ts is None). Returns (ts, content), or None if nothing is known."""
    if at_ts is None:
        return filehist.latest_known(store, path)
    content = filehist.reconstruct(store, path, at_ts)
    return (at_ts, content) if content is not None else None


@dataclass
class StatusReport:
    filestates: int = 0
    distinct_paths: int = 0
    sources_indexed: int = 0
    last_sweep_ts: float | None = None
    last_sweep_incidents: int = 0
    current_incidents: int = 0

    def render(self) -> str:
        lines = [
            f"index: {self.filestates:,} file state(s) across {self.distinct_paths:,} "
            f"file(s), {self.sources_indexed:,} source(s) checkpointed",
        ]
        if self.last_sweep_ts is None:
            lines.append("last sweep: never (run `memsub blackbox scan`)")
        else:
            lines.append(
                f"last sweep: {format_ts(self.last_sweep_ts)} "
                f"({self.last_sweep_incidents} incident(s) found)"
            )
        lines.append(f"current incidents (rechecked against disk now): {self.current_incidents}")
        return "\n".join(lines)


def status(store: EpisodicStore, roots: list[str | Path] | None = None) -> StatusReport:
    """Index stats, last sweep, and a fresh incident recheck (cheap: no
    re-indexing, just check_rollbacks over the repos discover_repos finds)."""
    conn = filehist._conn(store)
    conn.executescript(_SWEEP_SCHEMA)
    rep = StatusReport()
    rep.filestates = conn.execute("SELECT COUNT(*) FROM filestates").fetchone()[0]
    rep.distinct_paths = conn.execute("SELECT COUNT(DISTINCT path) FROM filestates").fetchone()[0]
    rep.sources_indexed = conn.execute("SELECT COUNT(*) FROM filehist_checkpoints").fetchone()[0]
    row = conn.execute("SELECT ts, incidents FROM blackbox_sweeps ORDER BY ts DESC LIMIT 1").fetchone()
    if row:
        rep.last_sweep_ts, rep.last_sweep_incidents = row
    incidents = []
    for repo in discover_repos(_effective_roots(roots)):
        incidents.extend(filehist.check_rollbacks(store, _tracked_files(repo)))
    rep.current_incidents = len(incidents)
    return rep


def main(argv: list[str] | None = None) -> int:
    """Standalone entry point (pyproject [project.scripts]): reuses the full
    `memsub` CLI's argument parser, pinned to the `blackbox` subcommand, so
    `blackbox scan` here is exactly `memsub blackbox scan` there. One place
    owns argument parsing for these commands; this is a thin passthrough.
    """
    from .cli import build_parser

    argv = list(argv if argv is not None else sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(["blackbox", *argv])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
