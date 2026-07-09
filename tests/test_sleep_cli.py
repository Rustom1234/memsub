"""`memsub sleep` CLI tests. LLM calls go through MockBackend, zero cost."""

from memsub.backends import MockBackend
from memsub.cli import main
from memsub.store import Episode, EpisodicStore


def _seed_store(db_path, n_turns=3):
    store = EpisodicStore(db_path)
    for i in range(n_turns):
        store.append(Episode(content=f"a: turn {i}", run_id="r1", session_id="s1",
                              kind="turn", ts=float(i)))
    store.close()


def _mock_sleep_backend():
    def responder(system, messages):
        if "inferences" in system.lower():
            return "[]"
        return "a session summary"

    return MockBackend(responder=responder)


def test_cli_sleep_writes_summary_and_marks_slept(tmp_path, monkeypatch, capsys):
    import memsub.cli as cli

    db = tmp_path / "mem.db"
    _seed_store(db)
    monkeypatch.setattr(cli, "build_backend", lambda prefer, model: _mock_sleep_backend())
    rc = main(["sleep", "--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "summaries: 1" in out
    store = EpisodicStore(db)
    assert any(ep.kind == "summary" for ep in store.active())
    store.close()


def test_cli_sleep_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    import memsub.cli as cli

    db = tmp_path / "mem.db"
    _seed_store(db)
    monkeypatch.setattr(cli, "build_backend", lambda prefer, model: _mock_sleep_backend())
    rc = main(["sleep", "--db", str(db), "--dry-run"])
    assert rc == 0
    assert "proposed (dry run)" in capsys.readouterr().out
    store = EpisodicStore(db)
    assert not any(ep.kind == "summary" for ep in store.active())
    store.close()


def test_cli_sleep_no_store(tmp_path, capsys):
    db = tmp_path / "missing.db"
    rc = main(["sleep", "--db", str(db)])
    assert rc == 1
    assert "no store at" in capsys.readouterr().out
