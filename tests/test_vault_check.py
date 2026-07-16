"""`memsub vault check`: unresolved [[wikilinks]] scan."""

from __future__ import annotations

from memsub.cli import main
from memsub.store import Episode, EpisodicStore
from memsub.vault import check_vault, sync_vault


def _seed_store(db) -> EpisodicStore:
    store = EpisodicStore(db)
    store.append(Episode(content="Rustom likes pytest.", run_id="r", session_id="1"))
    store.append(Episode(content="Rustom prefers markdown.", run_id="r", session_id="1"))
    return store


def test_check_vault_clean_sync_has_no_broken_links(tmp_path):
    store = _seed_store(tmp_path / "mem.db")
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    store.close()

    result = check_vault(vault)
    assert result.broken == []
    assert result.checked > 0  # the two notes do share the "Rustom" entity link


def test_check_vault_flags_link_to_deleted_note(tmp_path):
    store = _seed_store(tmp_path / "mem.db")
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    store.close()

    ns_dir = next(d for d in vault.iterdir() if d.is_dir() and d.name != "templates")
    notes = sorted(p for p in ns_dir.glob("*.md") if p.name != "index.md")
    assert len(notes) == 2
    notes[1].unlink()  # break whatever linked to it

    result = check_vault(vault)
    assert result.broken_count >= 1
    assert result.checked > 0


def test_check_vault_flags_stray_link_to_nonexistent_note(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("See [[does-not-exist]] for more.\n", encoding="utf-8")

    result = check_vault(vault)
    assert result.broken_count == 1
    assert result.broken[0] == ("note.md", "does-not-exist")


def test_check_vault_folder_note_convention_not_flagged(tmp_path):
    """A link matching an existing directory name (no note of that exact
    name) is Obsidian's folder-note convention, treated as resolved rather
    than broken (judgment call, see check_vault docstring)."""
    vault = tmp_path / "vault"
    (vault / "work").mkdir(parents=True)
    (vault / "work" / "index.md").write_text("# work\n", encoding="utf-8")
    (vault / "note.md").write_text("See [[work]] for context.\n", encoding="utf-8")

    result = check_vault(vault)
    assert result.broken == []


def test_check_vault_missing_vault_dir_returns_empty_result(tmp_path):
    result = check_vault(tmp_path / "does-not-exist")
    assert result.checked == 0
    assert result.broken == []


def test_cli_vault_check_exits_zero_when_clean(tmp_path, capsys):
    store = _seed_store(tmp_path / "mem.db")
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    store.close()

    rc = main(["vault", "check", "--vault", str(vault)])
    assert rc == 0
    assert "0 broken links" in capsys.readouterr().out


def test_cli_vault_check_exits_nonzero_and_lists_broken_links(tmp_path, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("See [[ghost]].\n", encoding="utf-8")

    rc = main(["vault", "check", "--vault", str(vault)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "1 broken link" in out
    assert "note.md: [[ghost]]" in out
