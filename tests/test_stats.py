"""`memsub stats`: read-only summary queries against the store."""

from memsub.cli import main
from memsub.store import Episode, EpisodicStore


def test_stats_no_store_reports_missing(tmp_path, capsys):
    db = tmp_path / "missing.db"
    rc = main(["stats", "--db", str(db)])
    assert rc == 1
    assert "no store at" in capsys.readouterr().out


def test_stats_reports_totals_active_and_namespaces(tmp_path, capsys):
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    store.append(Episode(content="a", run_id="r", session_id="1", namespace="work"))
    ep2 = store.append(Episode(content="b", run_id="r", session_id="1", namespace="work"))
    store.append(Episode(content="c", run_id="r", session_id="1", namespace="personal"))
    store.log_event(ep2.id, "superseded", {"reason": "duplicate", "by": "x"})
    store.set_extraction_checkpoint("transcript.jsonl", "hash1", 2)
    store.close()

    rc = main(["stats", "--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out

    assert "3 total, 2 active" in out
    assert "work: 1 active / 2 total" in out
    assert "personal: 1 active / 1 total" in out
    assert "extraction checkpoints: 1" in out
    assert "db file size:" in out
    assert "last consolidate/audit event:" in out
    assert "none" not in out.split("last consolidate/audit event:")[1]


def test_stats_reports_no_events_when_none_logged(tmp_path, capsys):
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    store.append(Episode(content="a", run_id="r", session_id="1"))
    store.close()

    rc = main(["stats", "--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "last consolidate/audit event: none" in out
