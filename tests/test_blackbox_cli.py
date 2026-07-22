"""`memsub blackbox scan|restore|status`: CLI wiring over memsub/blackbox.py.

Reuses test_blackbox.py's synthetic world (one clean repo, one rolled-back
repo) but drives it through memsub.cli.main so the argparse wiring itself
is exercised, not just the library functions.
"""

import datetime
import json
import subprocess

import pytest

from memsub.cli import main
from memsub.filehist import versions
from memsub.store import EpisodicStore

T0 = 1_784_500_000.0


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True)


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture()
def world(tmp_path):
    projects = tmp_path / "projects"
    repo = projects / "alpha"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "main.py").write_text("v1 = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")

    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    lines = [
        json.dumps({"type": "assistant", "timestamp": _iso(T0 + 5), "message": {"content": [
            {"type": "tool_use", "id": "w0", "name": "Write",
             "input": {"file_path": str(repo / "main.py"), "content": "v1 = 1\n"}}]}}),
        json.dumps({"type": "assistant", "timestamp": _iso(T0 + 600), "message": {"content": [
            {"type": "tool_use", "id": "w1", "name": "Write",
             "input": {"file_path": str(repo / "main.py"), "content": "v2 = 2\n"}}]}}),
    ]
    (tdir / "s.jsonl").write_text("\n".join(lines) + "\n")

    db = tmp_path / "bb.db"
    # Pre-index via the library so the CLI scan only needs to detect the
    # rollback (keeps this test about CLI wiring, not indexing itself).
    store = EpisodicStore(db)
    from memsub.filehist import index_transcripts

    index_transcripts(store, tdir)
    store.close()
    return projects, repo, db


def test_cli_scan_reports_incident_and_exits_nonzero(world, capsys):
    projects, repo, db = world
    rc = main(["blackbox", "scan", "--root", str(projects), "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "RECOVERABLE LOSS FOUND" in out
    assert "memsub blackbox restore" in out


def test_cli_scan_clean_world_exits_zero(world, capsys):
    projects, repo, db = world
    (repo / "main.py").write_text("v2 = 2\n")  # disk catches up
    rc = main(["blackbox", "scan", "--root", str(projects), "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "CLEAN" in out


def test_cli_restore_writes_latest_known_state(world, capsys):
    projects, repo, db = world
    target = str(repo / "main.py")
    rc = main(["blackbox", "restore", target, "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "restored" in out
    assert (repo / "main.py").read_text() == "v2 = 2\n"


def test_cli_restore_at_timestamp(world, capsys):
    projects, repo, db = world
    target = str(repo / "main.py")
    when = datetime.datetime.fromtimestamp(T0 + 5).strftime("%Y-%m-%d %H:%M")
    rc = main(["blackbox", "restore", target, "--at", when, "--db", str(db)])
    assert rc == 0
    assert (repo / "main.py").read_text() == "v1 = 1\n"


def test_cli_restore_unknown_file_fails(world, capsys):
    projects, repo, db = world
    rc = main(["blackbox", "restore", str(repo / "nope.py"), "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "no known history" in out


def test_cli_status_reports_index_stats(world, capsys):
    projects, repo, db = world
    main(["blackbox", "scan", "--root", str(projects), "--db", str(db)])
    capsys.readouterr()
    rc = main(["blackbox", "status", "--root", str(projects), "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 1  # the alpha rollback is still on disk
    assert "file state(s)" in out
    assert "last sweep:" in out
    assert "current incidents" in out


def test_cli_status_no_store_fails_cleanly(tmp_path, capsys):
    rc = main(["blackbox", "status", "--db", str(tmp_path / "nope.db")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "no store at" in out


def test_cli_restore_refuses_symlink_target(world, capsys, tmp_path):
    projects, repo, db = world
    real = repo / "main.py"
    outside = tmp_path / "victim.txt"
    outside.write_text("do not overwrite\n")
    real.unlink()
    real.symlink_to(outside)
    rc = main(["blackbox", "restore", str(real), "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "symlink" in out
    assert outside.read_text() == "do not overwrite\n"  # write was refused


def test_cli_restore_force_overrides_symlink(world, capsys, tmp_path):
    projects, repo, db = world
    real = repo / "main.py"
    outside = tmp_path / "victim.txt"
    outside.write_text("old\n")
    real.unlink()
    real.symlink_to(outside)
    rc = main(["blackbox", "restore", str(real), "--force", "--db", str(db)])
    assert rc == 0
    assert outside.read_text() == "v2 = 2\n"  # --force followed the symlink
