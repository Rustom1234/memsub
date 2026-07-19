"""`memsub graph backfill` CLI tests. Rule-based only (--backend none default),
zero LLM cost."""

from memsub.cli import main
from memsub.store import Episode, EpisodicStore


def _seed_store(db_path):
    store = EpisodicStore(db_path)
    store.append(Episode(content="Melanie: I love pottery.", run_id="r1", session_id="s1",
                          kind="turn", ts=1.0))
    store.append(Episode(content="Caroline: Melanie you would enjoy the gallery!",
                          run_id="r1", session_id="s2", kind="turn", ts=2.0))
    store.close()


def test_cli_graph_backfill_writes_entities_and_links(tmp_path, capsys):
    db = tmp_path / "mem.db"
    _seed_store(db)
    rc = main(["graph", "backfill", "--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 episode(s) processed" in out
    assert "0 LLM call(s)" in out
    store = EpisodicStore(db)
    assert store.entity_count() == 2
    assert store.entity_link_count() == 3
    store.close()


def test_cli_graph_backfill_is_idempotent(tmp_path):
    db = tmp_path / "mem.db"
    _seed_store(db)
    main(["graph", "backfill", "--db", str(db)])
    rc = main(["graph", "backfill", "--db", str(db)])
    assert rc == 0
    store = EpisodicStore(db)
    assert store.entity_count() == 2
    assert store.entity_link_count() == 3
    store.close()


def test_cli_graph_backfill_dry_run_writes_nothing(tmp_path, capsys):
    db = tmp_path / "mem.db"
    _seed_store(db)
    rc = main(["graph", "backfill", "--db", str(db), "--dry-run"])
    assert rc == 0
    assert "proposed (dry run)" in capsys.readouterr().out
    store = EpisodicStore(db)
    assert store.entity_count() == 0
    store.close()


def test_cli_graph_backfill_no_store(tmp_path, capsys):
    db = tmp_path / "missing.db"
    rc = main(["graph", "backfill", "--db", str(db)])
    assert rc == 1
    assert "no store at" in capsys.readouterr().out
