"""Obsidian vault export tests: rendering, idempotency, links, CLI wiring."""

from __future__ import annotations

import time

from memsub.cli import main
from memsub.store import Episode, EpisodicStore
from memsub.vault import namespace_dirname, note_filename, slugify, sync_vault

NOW = time.time()


def _ep(content, ts=None, kind="fact", namespace="default", provenance=None, session_id="s1"):
    return Episode(
        content=content,
        run_id="r1",
        session_id=session_id,
        kind=kind,
        namespace=namespace,
        provenance=provenance or {},
        ts=ts if ts is not None else NOW,
    )


def _store_with(*episodes):
    store = EpisodicStore(":memory:")
    return store, [store.append(e) for e in episodes]


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #
def test_slugify_is_filesystem_safe():
    assert slugify("Rustom likes pytest.ini!") == "rustom-likes-pytest-ini"
    assert slugify("") == "memory"


def test_note_filename_stable_and_readable():
    ep = _ep("Rustom's repo uses pytest.ini to disable ROS plugins")
    name = note_filename(ep)
    assert name.endswith(".md")
    assert ep.id[:8] in name
    assert " " not in name


def test_namespace_dirname_slugified():
    assert namespace_dirname("shared:team") == "shared-team"


# --------------------------------------------------------------------------- #
# Basic sync
# --------------------------------------------------------------------------- #
def test_sync_writes_one_note_per_episode(tmp_path):
    store, eps = _store_with(_ep("fact one"), _ep("fact two"))
    result = sync_vault(store, tmp_path / "vault")
    assert result.written == 2
    ns_dir = tmp_path / "vault" / "default"
    md_files = [f for f in ns_dir.glob("*.md") if f.name != "index.md"]
    assert len(md_files) == 2
    assert (ns_dir / "index.md").exists()
    assert (tmp_path / "vault" / "index.md").exists()


def test_note_frontmatter_fields(tmp_path):
    store, (ep,) = _store_with(
        _ep("qwen3:8b runs extraction locally", provenance={"confidence": 0.8, "source": "session.jsonl"})
    )
    sync_vault(store, tmp_path / "vault")
    note = (tmp_path / "vault" / "default" / note_filename(ep)).read_text()
    assert f'id: "{ep.id}"' in note
    assert 'namespace: "default"' in note
    assert 'kind: "fact"' in note
    assert 'type: "observed"' in note
    assert 'status: "active"' in note
    assert "confidence: 0.8" in note
    assert "Source: `session.jsonl`" in note
    assert "qwen3:8b runs extraction locally" in note


def test_inference_note_type_is_inferred(tmp_path):
    store, (ep,) = _store_with(
        _ep("Rustom prefers local models", kind="inference", provenance={"inferred": True, "confidence": 0.6})
    )
    sync_vault(store, tmp_path / "vault")
    note = (tmp_path / "vault" / "default" / note_filename(ep)).read_text()
    assert 'type: "inferred"' in note


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
def test_rerun_with_no_changes_touches_nothing(tmp_path):
    store, eps = _store_with(_ep("fact one"), _ep("fact two"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    note_path = vault / "default" / note_filename(eps[0])
    mtime_before = note_path.stat().st_mtime_ns

    result = sync_vault(store, vault)
    assert result.written == 0
    assert result.updated == 0
    assert result.unchanged == 2
    assert note_path.stat().st_mtime_ns == mtime_before


def test_new_episode_updates_index_without_rewriting_old_notes(tmp_path):
    store, (a,) = _store_with(_ep("fact one"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    old_note = vault / "default" / note_filename(a)
    old_content = old_note.read_text()

    store.append(_ep("fact two"))
    result = sync_vault(store, vault)
    assert result.written == 1
    assert old_note.read_text() == old_content


def test_removed_episode_deletes_stray_note(tmp_path):
    store, (a,) = _store_with(_ep("fact one"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    stray = vault / "default" / "stray-not-a-real-memory--deadbeef.md"
    stray.write_text("orphaned note", encoding="utf-8")

    result = sync_vault(store, vault)
    assert result.removed == 1
    assert not stray.exists()


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #
def test_superseded_memory_links_forward_and_stays_in_vault(tmp_path):
    store, (old, new) = _store_with(_ep("old fact", ts=NOW - 100), _ep("new fact", ts=NOW))
    store.log_event(old.id, "superseded", {"reason": "contradiction", "by": new.id})

    sync_vault(store, tmp_path / "vault")
    old_note = (tmp_path / "vault" / "default" / note_filename(old)).read_text()
    new_note = (tmp_path / "vault" / "default" / note_filename(new)).read_text()
    assert 'status: "superseded"' in old_note
    assert f"superseded by [[{note_filename(new)[:-3]}]]" in old_note
    assert f"supersedes [[{note_filename(old)[:-3]}]]" in new_note


def test_shared_entity_links_two_notes(tmp_path):
    store, (a, b) = _store_with(
        _ep("Rustom prefers local models"), _ep("Rustom's repo has no remote")
    )
    sync_vault(store, tmp_path / "vault")
    note_a = (tmp_path / "vault" / "default" / note_filename(a)).read_text()
    assert f"shares entity **Rustom** with [[{note_filename(b)[:-3]}]]" in note_a


def test_no_shared_entity_no_link(tmp_path):
    store, (a,) = _store_with(_ep("a lone fact about nothing shared"))
    sync_vault(store, tmp_path / "vault")
    note_a = (tmp_path / "vault" / "default" / note_filename(a)).read_text()
    assert "## Links" in note_a
    assert "(none)" in note_a


# --------------------------------------------------------------------------- #
# Namespaces
# --------------------------------------------------------------------------- #
def test_namespace_filter_only_syncs_that_namespace(tmp_path):
    store, (a, b) = _store_with(_ep("a", namespace="ns1"), _ep("b", namespace="ns2"))
    sync_vault(store, tmp_path / "vault", namespace="ns1")
    assert (tmp_path / "vault" / "ns1").exists()
    assert not (tmp_path / "vault" / "ns2").exists()


def test_moc_lists_all_namespaces_even_when_synced_separately(tmp_path):
    store, (a, b) = _store_with(_ep("a", namespace="ns1"), _ep("b", namespace="ns2"))
    vault = tmp_path / "vault"
    sync_vault(store, vault, namespace="ns1")
    sync_vault(store, vault, namespace="ns2")
    moc = (vault / "index.md").read_text()
    assert "ns1" in moc
    assert "ns2" in moc
    assert "2 memories across 2 namespace(s)" in moc


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_vault_sync(tmp_path, capsys):
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    store.append(_ep("a fact via cli"))
    store.close()

    vault = tmp_path / "vault"
    rc = main(["vault", "--db", str(db), "sync", "--vault", str(vault)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 written" in out
    assert (vault / "default" / "index.md").exists()


def test_cli_vault_sync_no_store(tmp_path, capsys):
    rc = main(["vault", "--db", str(tmp_path / "missing.db"), "sync", "--vault", str(tmp_path / "vault")])
    assert rc == 1
    assert "no store at" in capsys.readouterr().out
