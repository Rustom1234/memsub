"""Extraction tests. All LLM calls go through MockBackend, zero cost."""

import json

from memsub.backends import MockBackend
from memsub.cli import main
from memsub.extraction import (
    CandidateMemory,
    chunk_turns,
    extract_memories,
    load_transcript,
    Turn,
)
from memsub.store import EpisodicStore


CANDIDATES_JSON = json.dumps(
    [
        {"content": "The repo uses pytest.ini to disable ROS plugins.", "kind": "fact", "confidence": 0.9},
        {"content": "Rustom prefers plain markdown deliverables.", "kind": "preference", "confidence": 0.8},
    ]
)


def _mock(response: str = CANDIDATES_JSON) -> MockBackend:
    return MockBackend(responder=lambda s, m: response)


# --------------------------------------------------------------------------- #
# Transcript parsing
# --------------------------------------------------------------------------- #
def test_load_jsonl_role_content(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        '{"role": "user", "content": "hi", "ts": 100.0}\n'
        '{"role": "assistant", "content": "hello"}\n'
    )
    turns = load_transcript(f)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].ts == 100.0
    assert turns[1].index == 1


def test_load_jsonl_claude_code_shape(tmp_path):
    f = tmp_path / "t.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "run the tests"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "25 passed"}, {"type": "tool_use", "id": "x"}],
            },
            "timestamp": "2026-07-08T10:00:00Z",
        },
        {"type": "summary", "summary": "no message here"},
    ]
    f.write_text("\n".join(json.dumps(x) for x in lines))
    turns = load_transcript(f)
    assert len(turns) == 2
    assert turns[1].content == "25 passed"
    assert turns[1].ts > 0


def test_load_jsonl_skips_bad_lines(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('not json\n{"role": "user", "content": "ok"}\n')
    assert len(load_transcript(f)) == 1


def test_load_plain_text(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("First paragraph.\n\nSecond paragraph.\n")
    turns = load_transcript(f)
    assert len(turns) == 2
    assert turns[0].role == "unknown"


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
def test_chunk_turns_respects_limit():
    turns = [Turn(role="user", content="x" * 100, index=i) for i in range(10)]
    chunks = chunk_turns(turns, max_chars=250)
    assert len(chunks) > 1
    assert sum(len(c) for c in chunks) == 10
    for c in chunks:
        assert sum(len(t.content) + len(t.role) + 4 for t in c) <= 250 or len(c) == 1


def test_chunk_turns_single_oversize_turn():
    turns = [Turn(role="user", content="y" * 500, index=0)]
    assert chunk_turns(turns, max_chars=100) == [turns]


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def test_extract_memories_provenance():
    backend = _mock()
    turns = [Turn(role="user", content="the repo uses pytest.ini", index=0)]
    out = extract_memories(turns, backend, source="conv.jsonl")
    assert len(out) == 2
    c = out[0]
    assert c.kind == "fact" and c.confidence == 0.9
    assert c.provenance["origin"] == "extraction"
    assert c.provenance["source"] == "conv.jsonl"
    assert c.provenance["turn_span"] == [0, 0]
    assert c.provenance["backend"] == "mock"
    assert c.provenance["extracted_at"] > 0


def test_extract_tolerates_prose_and_bad_fields():
    resp = 'Sure! Here are the memories:\n[{"content": "a fact", "kind": "banana", "confidence": "high"}, {"kind": "fact"}]'
    out = extract_memories([Turn(role="u", content="x", index=0)], _mock(resp), source="s")
    assert len(out) == 1
    assert out[0].kind == "fact"  # invalid kind coerced
    assert out[0].confidence == 0.5  # unparseable confidence defaulted


def test_extract_empty_response():
    out = extract_memories([Turn(role="u", content="x", index=0)], _mock("[]"), source="s")
    assert out == []
    out2 = extract_memories([Turn(role="u", content="x", index=0)], _mock("no json"), source="s")
    assert out2 == []


def test_extract_one_call_per_chunk():
    backend = _mock("[]")
    turns = [Turn(role="user", content="z" * 100, index=i) for i in range(4)]
    extract_memories(turns, backend, source="s", max_chars=150)
    assert len(backend.calls) == 4


def test_candidate_to_episode():
    c = CandidateMemory(content="a", kind="fact", confidence=0.7, provenance={"origin": "extraction"})
    ep = c.to_episode(run_id="r", session_id="s")
    assert ep.importance == 0.7 and ep.kind == "fact" and ep.run_id == "r"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_extract_writes_store(tmp_path, capsys):
    f = tmp_path / "conv.jsonl"
    f.write_text('{"role": "user", "content": "we use pytest here"}\n')
    db = tmp_path / "mem.db"
    rc = main(["extract", str(f), "--backend", "mock", "--db", str(db)])
    assert rc == 0
    store = EpisodicStore(db)
    eps = store.all()
    assert len(eps) == 0  # bare MockBackend returns "OK", which parses to no candidates
    out = capsys.readouterr().out
    assert "no candidate memories" in out


def test_cli_extract_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    import memsub.cli as cli

    f = tmp_path / "conv.jsonl"
    f.write_text('{"role": "user", "content": "we use pytest here"}\n')
    db = tmp_path / "mem.db"
    monkeypatch.setattr(cli, "build_backend", lambda prefer, model: _mock())
    rc = main(["extract", str(f), "--backend", "mock", "--db", str(db), "--dry-run"])
    assert rc == 0
    assert not db.exists()
    assert "dry run" in capsys.readouterr().out


def test_cli_extract_end_to_end(tmp_path, monkeypatch):
    import memsub.cli as cli

    f = tmp_path / "conv.jsonl"
    f.write_text('{"role": "user", "content": "we use pytest here"}\n')
    db = tmp_path / "mem.db"
    monkeypatch.setattr(cli, "build_backend", lambda prefer, model: _mock())
    rc = main(["extract", str(f), "--db", str(db), "--run-id", "r1"])
    assert rc == 0
    store = EpisodicStore(db)
    eps = store.all(run_id="r1")
    assert len(eps) == 2
    assert eps[0].session_id == "conv"
    assert eps[0].provenance["origin"] == "extraction"
