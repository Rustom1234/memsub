"""X: flight recorder. Tests written first (superpowers TDD).

Fixture builds a synthetic Claude Code transcript: Write -> Edit -> Read ->
Edit on one file, plus a second file, then exercises indexing (incremental),
as-of reconstruction, and content-rollback detection: the exact signature of
the 2026-07-21 NFS incident (disk content matching an OLDER known state
while newer states exist in transcripts).
"""

import hashlib
import json

import pytest

from memsub.filehist import (
    check_rollbacks,
    index_transcripts,
    reconstruct,
    versions,
)
from memsub.store import EpisodicStore

T0 = 1_784_500_000.0


def _iso(ts):
    import datetime

    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _write_transcript(path, target, second):
    """Write -> Edit(v2) -> Read(v2 full) -> Edit(v3); second file: Write."""
    def tu(ts, name, tuid, **inp):
        return json.dumps({"type": "assistant", "timestamp": _iso(ts), "message": {"content": [
            {"type": "tool_use", "id": tuid, "name": name, "input": inp}]}})

    def tr(ts, tuid, text):
        return json.dumps({"type": "user", "timestamp": _iso(ts), "message": {"content": [
            {"type": "tool_result", "tool_use_id": tuid, "content": text}]}})

    v2 = "line one\nline two changed\nline three\n"
    numbered = "".join(f"{i+1}\t{l}\n" for i, l in enumerate(v2.rstrip("\n").split("\n")))
    lines = [
        tu(T0 + 10, "Write", "t1", file_path=str(target), content="line one\nline two\nline three\n"),
        tr(T0 + 11, "t1", "ok"),
        tu(T0 + 20, "Edit", "t2", file_path=str(target), old_string="line two", new_string="line two changed"),
        tr(T0 + 21, "t2", "ok"),
        tu(T0 + 30, "Read", "t3", file_path=str(target)),
        tr(T0 + 31, "t3", numbered),
        tu(T0 + 40, "Edit", "t4", file_path=str(target), old_string="line three", new_string="line three final"),
        tr(T0 + 41, "t4", "ok"),
        tu(T0 + 50, "Write", "t5", file_path=str(second), content="other file\n"),
        tr(T0 + 51, "t5", "ok"),
    ]
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture()
def env(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    target = tmp_path / "proj" / "a.py"
    second = tmp_path / "proj" / "b.py"
    target.parent.mkdir()
    _write_transcript(tdir / "s1.jsonl", target, second)
    store = EpisodicStore(tmp_path / "m.db")
    return store, tdir, target, second


def test_index_and_versions(env):
    store, tdir, target, second = env
    n = index_transcripts(store, tdir)
    assert n >= 5
    vs = versions(store, target)
    assert [v.kind for v in vs] == ["write", "edit", "read", "edit"]
    assert versions(store, second)[0].kind == "write"


def test_incremental_index_only_parses_new_bytes(env):
    store, tdir, target, second = env
    index_transcripts(store, tdir)
    assert index_transcripts(store, tdir) == 0  # nothing new
    # append one more edit
    with open(tdir / "s1.jsonl", "a") as f:
        f.write(json.dumps({"type": "assistant", "timestamp": _iso(T0 + 60), "message": {"content": [
            {"type": "tool_use", "id": "t6", "name": "Edit",
             "input": {"file_path": str(target), "old_string": "one", "new_string": "1"}}]}}) + "\n")
    assert index_transcripts(store, tdir) == 1


def test_reconstruct_as_of(env):
    store, tdir, target, second = env
    index_transcripts(store, tdir)
    assert reconstruct(store, target, T0 + 15) == "line one\nline two\nline three\n"
    assert reconstruct(store, target, T0 + 25) == "line one\nline two changed\nline three\n"
    assert reconstruct(store, target, T0 + 45) == "line one\nline two changed\nline three final\n"
    assert reconstruct(store, target, T0 + 5) is None  # before first knowledge


def test_rollback_detection(env):
    store, tdir, target, second = env
    index_transcripts(store, tdir)
    latest = "line one\nline two changed\nline three final\n"
    older = "line one\nline two\nline three\n"

    target.write_text(latest)
    second.write_text("other file\n")
    assert check_rollbacks(store, [target, second]) == []

    target.write_text(older)  # the NFS-incident signature
    incidents = check_rollbacks(store, [target, second])
    assert len(incidents) == 1
    assert incidents[0].path == str(target)
    assert incidents[0].kind == "rollback"

    target.write_text("hand-edited something new entirely\n")
    assert check_rollbacks(store, [target, second]) == []  # diverged != rolled back
