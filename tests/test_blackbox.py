"""Blackbox scan: the 60-second first-run experience. Tests first.

`blackbox scan` must: auto-discover git repos under given roots, index all
transcript sources incrementally, check every discovered repo, and produce
a report that either says "clean" or lists recoverable losses with exact
restore commands. The report is the product.
"""

import json
import subprocess

import pytest

from memsub.blackbox import discover_repos, scan
from memsub.store import EpisodicStore

T0 = 1_784_500_000.0


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True)


@pytest.fixture()
def world(tmp_path):
    """Two git repos (one will be rolled back), one non-repo dir, and a
    transcript recording newer content for the rolled-back file."""
    projects = tmp_path / "projects"
    (projects / "not-a-repo").mkdir(parents=True)
    repo_a = projects / "alpha"
    repo_b = projects / "beta"
    for r in (repo_a, repo_b):
        r.mkdir()
        _git(r, "init", "-q")
        (r / "main.py").write_text("v1 = 1\n")
        _git(r, "add", "-A")
        _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")

    import datetime

    def iso(ts):
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    lines = [
        json.dumps({"type": "assistant", "timestamp": iso(T0 + 5), "message": {"content": [
            {"type": "tool_use", "id": "w0", "name": "Write",
             "input": {"file_path": str(repo_a / "main.py"), "content": "v1 = 1\n"}}]}}),
        json.dumps({"type": "assistant", "timestamp": iso(T0 + 10), "message": {"content": [
            {"type": "tool_use", "id": "w1", "name": "Write",
             "input": {"file_path": str(repo_a / "main.py"), "content": "v2 = 2\n"}}]}}),
    ]
    (tdir / "s.jsonl").write_text("\n".join(lines) + "\n")
    # repo_a's disk still holds v1: a rollback vs transcript-known v2
    store = EpisodicStore(tmp_path / "bb.db")
    return store, projects, tdir, repo_a, repo_b


def test_discover_repos_finds_only_git_dirs(world):
    _store, projects, *_ = world
    repos = discover_repos([projects])
    names = sorted(p.name for p in repos)
    assert names == ["alpha", "beta"]


def test_scan_reports_recoverable_loss(world):
    store, projects, tdir, repo_a, repo_b = world
    report = scan(store, roots=[projects], transcripts=[tdir])
    assert report.repos_checked == 2
    assert report.states_indexed >= 1
    assert len(report.incidents) == 1
    inc = report.incidents[0]
    assert inc.path == str(repo_a / "main.py")
    assert "restore" in report.render()
    assert "alpha" in report.render()


def test_scan_clean_world_says_clean(world):
    store, projects, tdir, repo_a, repo_b = world
    (repo_a / "main.py").write_text("v2 = 2\n")  # disk catches up
    report = scan(store, roots=[projects], transcripts=[tdir])
    assert report.incidents == []
    assert "clean" in report.render().lower()


def test_scan_is_idempotent_and_fast_second_time(world):
    store, projects, tdir, *_ = world
    scan(store, roots=[projects], transcripts=[tdir])
    r2 = scan(store, roots=[projects], transcripts=[tdir])
    assert r2.states_indexed == 0  # incremental: nothing new to parse
