"""`memsub vault promote`: human notes under vault/notes/ -> first-class memories."""

from __future__ import annotations

from memsub.cli import main
from memsub.consolidate import _is_inferred
from memsub.store import Episode, EpisodicStore
from memsub.vault import promote_notes, sync_vault


def _write_note(vault, name: str, text: str):
    notes_dir = vault / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / name).write_text(text, encoding="utf-8")


def test_promote_new_note_creates_human_authored_episode(tmp_path):
    db = tmp_path / "mem.db"
    vault = tmp_path / "vault"
    _write_note(vault, "rustom.md", "Rustom prefers plain markdown deliverables.")
    store = EpisodicStore(db)

    result = promote_notes(store, vault, namespace="default")
    assert result.promoted == 1
    assert result.updated == 0
    assert result.unchanged == 0

    eps = store.active(namespace="default")
    assert len(eps) == 1
    ep = eps[0]
    assert ep.content == "Rustom prefers plain markdown deliverables."
    assert ep.provenance["origin"] == "human_authored"
    assert ep.provenance["source_note"] == "notes/rustom.md"
    assert not _is_inferred(ep)  # ranks as observed, not inferred
    store.close()


def test_promote_is_idempotent_on_unchanged_note(tmp_path):
    db = tmp_path / "mem.db"
    vault = tmp_path / "vault"
    _write_note(vault, "rustom.md", "Rustom prefers plain markdown deliverables.")
    store = EpisodicStore(db)

    promote_notes(store, vault, namespace="default")
    result2 = promote_notes(store, vault, namespace="default")

    assert result2.promoted == 0
    assert result2.unchanged == 1
    assert len(store.active(namespace="default")) == 1  # no duplicate
    store.close()


def test_promote_changed_note_supersedes_old_episode(tmp_path):
    db = tmp_path / "mem.db"
    vault = tmp_path / "vault"
    _write_note(vault, "rustom.md", "Rustom prefers plain markdown deliverables.")
    store = EpisodicStore(db)
    promote_notes(store, vault, namespace="default")
    old_id = store.active(namespace="default")[0].id

    _write_note(vault, "rustom.md", "Rustom prefers plain markdown, no em dashes.")
    result = promote_notes(store, vault, namespace="default")

    assert result.updated == 1
    assert result.promoted == 0
    assert store.status(old_id) == "superseded"
    active = store.active(namespace="default")
    assert len(active) == 1
    assert active[0].content == "Rustom prefers plain markdown, no em dashes."
    store.close()


def test_promote_skips_empty_note(tmp_path):
    db = tmp_path / "mem.db"
    vault = tmp_path / "vault"
    _write_note(vault, "empty.md", "   \n\n  ")
    store = EpisodicStore(db)

    result = promote_notes(store, vault, namespace="default")
    assert result.skipped == 1
    assert store.count() == 0
    store.close()


def test_promote_missing_notes_dir_is_a_noop(tmp_path):
    db = tmp_path / "mem.db"
    vault = tmp_path / "vault"
    store = EpisodicStore(db)
    result = promote_notes(store, vault, namespace="default")
    assert result.promoted == 0
    assert store.count() == 0
    store.close()


def test_promoted_memory_round_trips_into_vault_linked_to_source_note(tmp_path):
    db = tmp_path / "mem.db"
    vault = tmp_path / "vault"
    _write_note(vault, "rustom.md", "Rustom prefers plain markdown deliverables.")
    store = EpisodicStore(db)
    promote_notes(store, vault, namespace="default")

    sync_result = sync_vault(store, vault, namespace="default")
    assert sync_result.written >= 1  # the promoted memory got a normal note

    ns_dir = vault / "default"
    notes = [p for p in ns_dir.glob("*.md") if p.name != "index.md"]
    assert len(notes) == 1
    note_text = notes[0].read_text(encoding="utf-8")
    assert "[[rustom]]" in note_text  # linked back to its source note (notes/rustom.md)
    store.close()


def test_sync_vault_rejects_namespace_colliding_with_notes_folder(tmp_path):
    db = tmp_path / "mem.db"
    vault = tmp_path / "vault"
    store = EpisodicStore(db)
    store.append(Episode(content="x", run_id="r", session_id="1", namespace="notes"))
    try:
        import pytest

        with pytest.raises(ValueError, match="reserved"):
            sync_vault(store, vault, namespace="notes")
    finally:
        store.close()


def test_cli_vault_promote_wiring(tmp_path, capsys):
    db = tmp_path / "mem.db"
    vault = tmp_path / "vault"
    _write_note(vault, "rustom.md", "Rustom prefers plain markdown deliverables.")
    store = EpisodicStore(db)
    store.close()

    rc = main(["vault", "--db", str(db), "promote", "--vault", str(vault), "--namespace", "default"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 promoted" in out

    store = EpisodicStore(db)
    assert len(store.active(namespace="default")) == 1
    store.close()
