"""Wiki mode: markdown pages as a memory source, and the consolidation payoff.

All LLM calls go through MockBackend. The mock's responder branches on the
system prompt: the contradiction judge gets a fixed "contradicts" verdict, and
wiki extraction echoes each non-heading page paragraph back as one claim, so
the stored claim text is deterministic and controllable from the fixture.
"""

import json

from memsub.backends import MockBackend
from memsub.cli import main
from memsub.consolidate import consolidate
from memsub.store import EpisodicStore
from memsub.wiki import ingest_wiki


def _responder(system, messages):
    user = messages[-1]["content"]
    if "contradict" in system.lower():
        return json.dumps([{"pair": 0, "contradicts": True}])
    claims = []
    for block in user.split("\n\n"):
        text = block.split("]:", 1)[-1].strip() if "]:" in block else block.strip()
        if not text or text.startswith("#"):
            continue
        claims.append({"content": text, "kind": "fact", "confidence": 0.9})
    return json.dumps(claims)


def _backend():
    return MockBackend(responder=_responder)


def _write(pages, name, text):
    p = pages / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# --------------------------------------------------------------------------- #
# Ingest: claims stored with wiki provenance
# --------------------------------------------------------------------------- #
def test_ingest_writes_claims_with_provenance(tmp_path):
    pages = tmp_path / "wiki"
    _write(pages, "db.md", "# Database\n\nThe production database is PostgreSQL.")
    store = EpisodicStore(":memory:")

    result = ingest_wiki(store, pages, _backend())
    assert result.pages_ingested == 1
    assert result.claims_written == 1
    assert result.prior_superseded == 0

    (ep,) = store.active()
    assert ep.content == "The production database is PostgreSQL."
    assert ep.provenance["origin"] == "wiki"
    assert ep.provenance["source_page"] == "db.md"
    assert ep.provenance["source_content_hash"]


# --------------------------------------------------------------------------- #
# Re-ingesting an unchanged page is a no-op
# --------------------------------------------------------------------------- #
def test_reingest_unchanged_is_noop(tmp_path):
    pages = tmp_path / "wiki"
    _write(pages, "db.md", "# Database\n\nThe production database is PostgreSQL.")
    store = EpisodicStore(":memory:")

    ingest_wiki(store, pages, _backend())
    before = store.count()

    second = ingest_wiki(store, pages, _backend())
    assert second.pages_unchanged == 1
    assert second.pages_ingested == 0
    assert second.claims_written == 0
    assert store.count() == before  # nothing appended


# --------------------------------------------------------------------------- #
# A changed page supersedes its own prior claims (append-only)
# --------------------------------------------------------------------------- #
def test_changed_page_supersedes_prior_claims(tmp_path):
    pages = tmp_path / "wiki"
    page = _write(pages, "db.md", "# Database\n\nThe production database is PostgreSQL.")
    store = EpisodicStore(":memory:")
    ingest_wiki(store, pages, _backend())
    (old,) = store.active()

    page.write_text("# Database\n\nThe production database is SQLite.")
    result = ingest_wiki(store, pages, _backend())
    assert result.pages_ingested == 1
    assert result.prior_superseded == 1
    assert result.claims_written == 1

    # old claim retired for audit, not deleted; new one active
    assert store.status(old.id) == "superseded"
    assert store.get(old.id) is not None
    ev = store.events(episode_id=old.id, event="superseded")[0]
    assert ev.detail == {"reason": "wiki_page_updated", "source_page": "db.md"}
    active = store.active()
    assert len(active) == 1
    assert active[0].content == "The production database is SQLite."


def test_emptied_page_retires_claims_and_checkpoints(tmp_path):
    pages = tmp_path / "wiki"
    page = _write(pages, "db.md", "# Database\n\nThe production database is PostgreSQL.")
    store = EpisodicStore(":memory:")
    ingest_wiki(store, pages, _backend())

    page.write_text("# Database\n")  # heading only, no claims
    result = ingest_wiki(store, pages, _backend())
    assert result.prior_superseded == 1
    assert result.claims_written == 0
    assert result.pages_empty == 1
    assert store.active() == []

    # emptied page is checkpointed, so a re-ingest is a no-op
    third = ingest_wiki(store, pages, _backend())
    assert third.pages_unchanged == 1


# --------------------------------------------------------------------------- #
# The payoff: cross-page contradictions surface as staged proposals
# --------------------------------------------------------------------------- #
def test_cross_page_contradiction_stages_a_proposal(tmp_path, monkeypatch):
    pages = tmp_path / "wiki"
    _write(pages, "arch.md", "The production database is PostgreSQL.")
    _write(pages, "ops.md", "The production database is SQLite.")
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    ingest_wiki(store, pages, _backend())
    assert len(store.active()) == 2
    store.close()

    # consolidate --stage: the two contradictory claims surface as a proposal.
    # The CLI builds its own backend, so inject the responder-driven mock.
    monkeypatch.setattr("memsub.cli.build_backend", lambda **kw: _backend())
    vault = tmp_path / "vault"
    rc = main([
        "consolidate", "--db", str(db), "--backend", "mock",
        "--stage", "--vault", str(vault),
    ])
    assert rc == 0

    staged = sorted((vault / "staging").rglob("*.md"))
    assert len(staged) == 1
    note = staged[0].read_text()
    assert 'action: "supersede"' in note
    assert 'reason: "contradiction"' in note

    # store untouched by staging
    store = EpisodicStore(db)
    assert len(store.active()) == 2
    store.close()


def test_contradiction_pair_uses_mock_backend_only(tmp_path):
    """Sanity: the same two claims consolidate to a contradiction supersede with
    the mock backend and the default deterministic hashing embedder, no ollama."""
    pages = tmp_path / "wiki"
    _write(pages, "arch.md", "The production database is PostgreSQL.")
    _write(pages, "ops.md", "The production database is SQLite.")
    store = EpisodicStore(":memory:")
    ingest_wiki(store, pages, _backend())

    actions = consolidate(store, backend=_backend(), dry_run=True)
    assert [a.reason for a in actions] == ["contradiction"]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_wiki_ingest(tmp_path, capsys, monkeypatch):
    pages = tmp_path / "wiki"
    _write(pages, "db.md", "# Database\n\nThe production database is PostgreSQL.")
    db = tmp_path / "mem.db"

    monkeypatch.setattr("memsub.cli.build_backend", lambda **kw: _backend())
    rc = main(["wiki", "ingest", "--db", str(db), "--pages", str(pages), "--backend", "mock"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 page(s) ingested" in out
    assert "1 claim(s) written" in out

    store = EpisodicStore(db)
    assert len(store.active()) == 1
    store.close()


def test_cli_wiki_ingest_missing_folder(tmp_path, capsys):
    rc = main(["wiki", "ingest", "--db", str(tmp_path / "m.db"),
               "--pages", str(tmp_path / "nope"), "--backend", "mock"])
    assert rc == 1
    assert "no wiki folder" in capsys.readouterr().out
