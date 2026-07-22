"""Codex parser: schema is UNVERIFIED (no real ~/.codex on this machine,
confirmed absent while building this). Built from Codex's own apply_patch
text convention, exercised only against a synthetic fixture shaped like it.
See memsub/parsers/codex.py's module docstring for the honesty caveats."""

import json

from memsub.filehist import versions
from memsub.parsers.codex import index_sessions
from memsub.store import EpisodicStore

ADD_PATCH = (
    "*** Begin Patch\n"
    "*** Add File: /tmp/proj/new.py\n"
    "+line one\n"
    "+line two\n"
    "*** End Patch"
)

UPDATE_PATCH = (
    "*** Begin Patch\n"
    "*** Update File: /tmp/proj/new.py\n"
    "@@\n"
    "-line one\n"
    "+line ONE\n"
    "*** End Patch"
)


def _rollout(codex_home, lines):
    d = codex_home / "sessions" / "2026" / "07" / "22"
    d.mkdir(parents=True)
    p = d / "rollout-abc.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return p


def test_add_file_patch_indexed_as_write(tmp_path):
    codex_home = tmp_path / "codex"
    _rollout(codex_home, [{"timestamp": "2026-07-22T00:00:00Z", "text": ADD_PATCH}])

    store = EpisodicStore(tmp_path / "m.db")
    added = index_sessions(store, codex_home)
    assert added == 1
    vs = versions(store, "/tmp/proj/new.py")
    assert vs[0].kind == "write"


def test_update_file_patch_indexed_as_coarse_edit(tmp_path):
    codex_home = tmp_path / "codex"
    _rollout(codex_home, [
        {"timestamp": "2026-07-22T00:00:00Z", "text": ADD_PATCH},
        {"timestamp": "2026-07-22T00:01:00Z", "text": UPDATE_PATCH},
    ])

    store = EpisodicStore(tmp_path / "m.db")
    index_sessions(store, codex_home)
    vs = versions(store, "/tmp/proj/new.py")
    assert [v.kind for v in vs] == ["write", "edit"]


def test_incremental_index_only_parses_new_bytes(tmp_path):
    codex_home = tmp_path / "codex"
    path = _rollout(codex_home, [{"timestamp": "2026-07-22T00:00:00Z", "text": ADD_PATCH}])

    store = EpisodicStore(tmp_path / "m.db")
    assert index_sessions(store, codex_home) == 1
    assert index_sessions(store, codex_home) == 0  # nothing new

    with open(path, "a") as fh:
        fh.write(json.dumps({"timestamp": "2026-07-22T00:01:00Z", "text": UPDATE_PATCH}) + "\n")
    assert index_sessions(store, codex_home) == 1


def test_absent_codex_home_is_a_silent_noop(tmp_path):
    store = EpisodicStore(tmp_path / "m.db")
    assert index_sessions(store, tmp_path / "nope") == 0


def test_malformed_lines_are_skipped_not_fatal(tmp_path):
    codex_home = tmp_path / "codex"
    d = codex_home / "sessions"
    d.mkdir(parents=True)
    p = d / "rollout-x.jsonl"
    p.write_text(
        "not json at all\n"
        + json.dumps({"timestamp": "not-a-date", "text": "no patch here"}) + "\n"
    )

    store = EpisodicStore(tmp_path / "m.db")
    assert index_sessions(store, codex_home) == 0
