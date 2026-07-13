"""Obsidian vault export tests: rendering, idempotency, links, CLI wiring."""

from __future__ import annotations

import time

from memsub.cli import main
from memsub.consolidate import consolidate
from memsub.store import Episode, EpisodicStore
from memsub.vault import (
    apply_vault,
    content_hash,
    namespace_dirname,
    note_filename,
    slugify,
    sync_vault,
)

NOW = time.time()


def _edit_body(note_path, new_content: str, old_content: str) -> None:
    """Simulate a human editing a note's body text in Obsidian. Targets only
    the "## Content" section (the heading above it also echoes the original
    content, so a bare string replace would hit that instead)."""
    text = note_path.read_text(encoding="utf-8")
    old_block = f"## Content\n\n{old_content}\n\n## Provenance"
    assert old_block in text
    new_block = f"## Content\n\n{new_content}\n\n## Provenance"
    note_path.write_text(text.replace(old_block, new_block, 1), encoding="utf-8")


def _set_verdict(note_path, verdict: str) -> None:
    """Simulate a human setting `verdict: reject|confirm` in a note's frontmatter."""
    text = note_path.read_text(encoding="utf-8")
    marker = "\n---\n\n#"
    assert marker in text
    text = text.replace(marker, f'\nverdict: "{verdict}"{marker}', 1)
    note_path.write_text(text, encoding="utf-8")


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


# --------------------------------------------------------------------------- #
# Annotate/override loop: apply_vault
# --------------------------------------------------------------------------- #
def test_content_hash_stable_for_same_content():
    assert content_hash("same text") == content_hash("same text")
    assert content_hash("same text") != content_hash("different text")


def test_sync_writes_content_hash(tmp_path):
    store, (ep,) = _store_with(_ep("a fact worth remembering"))
    sync_vault(store, tmp_path / "vault")
    note = (tmp_path / "vault" / "default" / note_filename(ep)).read_text()
    assert f'content_hash: "{content_hash(ep.content)}"' in note


def test_apply_unedited_note_is_noop(tmp_path):
    """The core safety property: apply must never act on a note that is
    exactly what sync wrote."""
    store, (ep,) = _store_with(_ep("fact one"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    before_count = store.count()

    result = apply_vault(store, vault)
    assert result == type(result)(unchanged=1)
    assert store.count() == before_count
    assert store.status(ep.id) == "active"


def test_apply_correction_supersedes_with_human_verified_provenance(tmp_path):
    store, (ep,) = _store_with(_ep("the deploy script lives in scripts/deploy.sh"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    note_path = vault / "default" / note_filename(ep)
    _edit_body(note_path, "the deploy script lives in bin/deploy.sh", ep.content)

    result = apply_vault(store, vault)
    assert result.corrected == 1
    assert result.rejected == result.confirmed == result.unchanged == 0

    assert store.status(ep.id) == "superseded"
    new_eps = [e for e in store.all() if e.id != ep.id]
    assert len(new_eps) == 1
    new_ep = new_eps[0]
    assert new_ep.content == "the deploy script lives in bin/deploy.sh"
    assert new_ep.provenance["human_verified"] is True
    assert new_ep.provenance["origin"] == "human_correction"
    assert new_ep.provenance["corrected_from"] == ep.id
    assert new_ep.provenance["confidence"] == 1.0
    assert store.status(new_ep.id) == "active"

    events = store.events(episode_id=ep.id)
    supersede_events = [e for e in events if e.event == "superseded"]
    assert len(supersede_events) == 1
    assert supersede_events[0].detail == {"reason": "human_correction", "by": new_ep.id}


def test_apply_correction_outranks_inferred_memory_on_contradiction():
    """The observed-beats-inferred consolidation rule already treats anything
    that is not kind="inference" / provenance.inferred as observed; a human
    correction must land on that side of the line with no special-casing."""
    from memsub.backends import MockBackend
    import json

    store = EpisodicStore(":memory:")
    corrected = store.append(
        Episode(
            content="the budget is $500",
            run_id="r", session_id="s", kind="fact", ts=NOW,
            provenance={"origin": "human_correction", "human_verified": True, "confidence": 1.0},
        )
    )
    inferred = store.append(
        Episode(
            content="the budget is $600",
            run_id="r", session_id="s", kind="inference", ts=NOW + 10,
            provenance={"inferred": True, "confidence": 0.6},
        )
    )
    backend = MockBackend(responder=lambda s, m: json.dumps([{"pair": 0, "contradicts": True}]))
    actions = consolidate(store, backend=backend, now=NOW + 20)
    assert len(actions) == 1
    assert actions[0].episode_id == inferred.id  # the inference loses, despite being newer
    assert store.status(corrected.id) == "active"


def test_apply_rejection_forgets_memory(tmp_path):
    store, (ep,) = _store_with(_ep("a memory that turns out to be wrong"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    note_path = vault / "default" / note_filename(ep)
    _set_verdict(note_path, "reject")

    result = apply_vault(store, vault)
    assert result.rejected == 1
    assert store.status(ep.id) == "forgotten"
    events = store.events(episode_id=ep.id)
    forgotten = [e for e in events if e.event == "forgotten"]
    assert forgotten[0].detail == {"reason": "human_reject"}


def test_apply_reject_wins_over_simultaneous_body_edit(tmp_path):
    store, (ep,) = _store_with(_ep("a fact"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    note_path = vault / "default" / note_filename(ep)
    _edit_body(note_path, "an edited fact", ep.content)
    _set_verdict(note_path, "reject")

    result = apply_vault(store, vault)
    assert result.rejected == 1
    assert result.corrected == 0
    assert store.status(ep.id) == "forgotten"
    assert store.count() == 1  # no correction episode was created


def test_apply_confirm_pins_and_protects_from_decay(tmp_path):
    old_ts = NOW - 90 * 86400
    store, (ep,) = _store_with(_ep("a low-importance but true fact", ts=old_ts))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    note_path = vault / "default" / note_filename(ep)
    _set_verdict(note_path, "confirm")

    result = apply_vault(store, vault)
    assert result.confirmed == 1
    assert store.status(ep.id) == "active"
    assert store.events(episode_id=ep.id, event="confirmed")

    # decay would otherwise forget a 90-day-old, unretrieved memory
    assert consolidate(store, backend=None, now=NOW) == []
    assert store.status(ep.id) == "active"


def test_apply_skips_orphan_note(tmp_path):
    store, (ep,) = _store_with(_ep("fact one"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    stray = vault / "default" / "orphan--deadbeef00.md"
    stray.write_text(
        '---\nid: "deadbeef00-not-real"\n---\n\n# x\n\n## Content\n\nghost\n\n## Provenance\n',
        encoding="utf-8",
    )
    result = apply_vault(store, vault)
    assert result.skipped == 1
    assert result.corrected == result.rejected == result.confirmed == 0


def test_apply_skips_malformed_note(tmp_path):
    store, (ep,) = _store_with(_ep("fact one"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    junk = vault / "default" / "junk--00000000.md"
    junk.write_text("not a note at all, no frontmatter", encoding="utf-8")
    result = apply_vault(store, vault)
    assert result.skipped == 1


def test_apply_is_safe_to_rerun_without_resync(tmp_path):
    """A second apply, run before the vault is resynced, must not
    double-apply the same edit."""
    store, (ep,) = _store_with(_ep("fact to correct"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    note_path = vault / "default" / note_filename(ep)
    _edit_body(note_path, "corrected fact", ep.content)

    first = apply_vault(store, vault)
    assert first.corrected == 1

    second = apply_vault(store, vault)  # same stale note file, no resync between calls
    assert second.corrected == 0
    assert second.skipped == 1  # ep.id is no longer active
    assert len([e for e in store.all() if store.status(e.id) == "active"]) == 1


def test_round_trip_sync_edit_apply_sync_converges(tmp_path):
    store, (ep,) = _store_with(_ep("the CI runner uses ubuntu-22.04"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    note_path = vault / "default" / note_filename(ep)
    _edit_body(note_path, "the CI runner uses ubuntu-24.04", ep.content)

    apply_result = apply_vault(store, vault)
    assert apply_result.corrected == 1

    resync = sync_vault(store, vault)
    assert resync.written == 1  # the new corrected note
    assert resync.updated == 1  # the old note now shows status: superseded
    old_note = note_path.read_text()
    assert 'status: "superseded"' in old_note
    new_ep = next(e for e in store.all() if e.id != ep.id)
    assert f"superseded by [[{note_filename(new_ep)[:-3]}]]" in old_note

    # a second sync with no further store changes touches nothing
    resync2 = sync_vault(store, vault)
    assert resync2.written == 0 and resync2.updated == 0

    # and apply on the now-current vault has nothing left to do
    final_apply = apply_vault(store, vault)
    assert final_apply.corrected == final_apply.rejected == final_apply.confirmed == 0


def test_apply_namespace_filter(tmp_path):
    store, (a, b) = _store_with(_ep("a", namespace="ns1"), _ep("b", namespace="ns2"))
    vault = tmp_path / "vault"
    sync_vault(store, vault)
    _set_verdict(vault / "ns1" / note_filename(a), "reject")
    _set_verdict(vault / "ns2" / note_filename(b), "reject")

    result = apply_vault(store, vault, namespace="ns1")
    assert result.rejected == 1
    assert store.status(a.id) == "forgotten"
    assert store.status(b.id) == "active"  # ns2 untouched


def test_cli_vault_apply(tmp_path, capsys):
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    ep = store.append(_ep("cli-applied fact"))
    store.close()

    vault = tmp_path / "vault"
    assert main(["vault", "--db", str(db), "sync", "--vault", str(vault)]) == 0
    capsys.readouterr()
    _set_verdict(vault / "default" / note_filename(ep), "confirm")

    rc = main(["vault", "--db", str(db), "apply", "--vault", str(vault)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 confirmed" in out

    store2 = EpisodicStore(db)
    assert store2.events(episode_id=ep.id, event="confirmed")


def test_cli_vault_apply_no_store(tmp_path, capsys):
    rc = main(["vault", "--db", str(tmp_path / "missing.db"), "apply", "--vault", str(tmp_path / "vault")])
    assert rc == 1
    assert "no store at" in capsys.readouterr().out
