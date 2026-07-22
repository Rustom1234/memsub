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
from dataclasses import dataclass, field
from pathlib import Path

from . import filehist
from .store import EpisodicStore

DEFAULT_ROOTS = [Path.home() / "projects"]
DEFAULT_TRANSCRIPTS = [Path.home() / ".claude" / "projects"]
# Codex CLI keeps rollout transcripts here (parsed by filehist where the
# format matches tool_use/tool_result JSONL; divergent formats are skipped
# harmlessly by the parser's fail-open line handling).
CODEX_TRANSCRIPTS = Path.home() / ".codex" / "sessions"


def discover_repos(roots: list[str | Path], max_depth: int = 3) -> list[Path]:
    """Git repos under roots (top-level .git dirs only, no nesting descent)."""
    repos: list[Path] = []
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        stack = [(root, 0)]
        while stack:
            d, depth = stack.pop()
            if (d / ".git").exists():
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
            old = datetime.datetime.fromtimestamp(inc.matched_ts).strftime("%Y-%m-%d %H:%M")
            new = datetime.datetime.fromtimestamp(inc.latest_ts).strftime("%Y-%m-%d %H:%M")
            lines.append(f"  {inc.path}")
            lines.append(f"    disk = {old} state; transcripts prove {new} existed")
            lines.append(f"    recover: memsub filehist restore '{inc.path}' --as-of '{new}'")
        return "\n".join(lines)


def scan(
    store: EpisodicStore,
    roots: list[str | Path] | None = None,
    transcripts: list[str | Path] | None = None,
) -> ScanReport:
    """The first-run experience: index everything, check everything."""
    import time as _time

    t0 = _time.monotonic()
    report = ScanReport()
    for tdir in (transcripts or _default_transcript_dirs()):
        if Path(tdir).is_dir():
            report.states_indexed += filehist.index_transcripts(store, tdir)
    repos = discover_repos(roots or DEFAULT_ROOTS)
    for repo in repos:
        paths = _tracked_files(repo)
        report.files_known += len(paths)
        report.incidents.extend(filehist.check_rollbacks(store, paths))
        report.repos_checked += 1
    report.elapsed_s = _time.monotonic() - t0
    return report


def _default_transcript_dirs() -> list[Path]:
    dirs = [d for d in DEFAULT_TRANSCRIPTS if d.is_dir()]
    if CODEX_TRANSCRIPTS.is_dir():
        dirs.append(CODEX_TRANSCRIPTS)
    return dirs
