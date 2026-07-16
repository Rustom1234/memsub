"""Staging / approve-before-apply for consolidate (memsub consolidate --stage).

All LLM-free: the fixtures use a duplicate pair (dedup supersede, no backend
needed) and a stale low-value memory (decay forget), so consolidation produces
both a supersede and a forget action with the mock/None backend.
"""

import time

from memsub.cli import main
from memsub.consolidate import consolidate
from memsub.store import Episode, EpisodicStore
from memsub.vault import (
    STAGING_DIRNAME,
    apply_vault,
    stage_actions,
)

NOW = time.time()


def _ep(content, ts, kind="fact", importance=1.0):
    return Episode(
        content=content, run_id="r", session_id="s", kind=kind, importance=importance, ts=ts
    )


def _fixture_store():
    """A store whose consolidation yields one supersede (dedup) and one forget
    (decay)."""
    store = EpisodicStore(":memory:")
    old = store.append(_ep("the repo uses pytest.ini to disable ROS plugins", NOW))
    new = store.append(_ep("the repo uses pytest.ini to disable ROS plugins", NOW + 10))
    stale = store.append(_ep("some low value note", NOW - 90 * 86400, importance=0.3))
    return store, old, new, stale


def _staging_files(vault):
    root = vault / STAGING_DIRNAME
    return sorted(p.name for p in root.rglob("*.md")) if root.exists() else []


# --------------------------------------------------------------------------- #
# --stage computes but does not apply
# --------------------------------------------------------------------------- #
def test_stage_writes_proposals_without_touching_store(tmp_path):
    store, old, new, stale = _fixture_store()
    actions = consolidate(store, backend=None, now=NOW, dry_run=True)
    assert {a.action for a in actions} == {"supersede", "forget"}

    result = stage_actions(store, actions, tmp_path)
    assert result.staged == 2
    assert result.existing == 0 and result.skipped == 0

    # store untouched: every episode still active
    assert store.status(old.id) == "active"
    assert store.status(stale.id) == "active"

    files = _staging_files(tmp_path)
    assert f"supersede--{old.id[:8]}.md" in files
    assert f"forget--{stale.id[:8]}.md" in files

    # proposal note carries the target text and a blank verdict
    note = (tmp_path / STAGING_DIRNAME / "default" / f"forget--{stale.id[:8]}.md").read_text()
    assert 'status: "pending"' in note
    assert 'verdict: ""' in note
    assert "some low value note" in note


# --------------------------------------------------------------------------- #
# apply accept executes exactly the staged action
# --------------------------------------------------------------------------- #
def test_apply_accept_executes_action(tmp_path):
    store, old, new, stale = _fixture_store()
    actions = consolidate(store, backend=None, now=NOW, dry_run=True)
    stage_actions(store, actions, tmp_path)

    # accept the supersede, leave the forget pending
    sup = tmp_path / STAGING_DIRNAME / "default" / f"supersede--{old.id[:8]}.md"
    sup.write_text(sup.read_text().replace('verdict: ""', 'verdict: "accept"'))

    result = apply_vault(store, tmp_path)
    assert result.staged_applied == 1
    assert result.staged_rejected == 0

    # the accepted supersede is now a real store event
    assert store.status(old.id) == "superseded"
    ev = store.events(episode_id=old.id, event="superseded")[0]
    assert ev.detail["reason"] == "duplicate"
    assert ev.detail["by"] == new.id
    # detail type round-trips: similarity is a float, not a string
    assert isinstance(ev.detail["similarity"], float)

    # the pending forget was left alone
    assert store.status(stale.id) == "active"

    # accepted note is marked applied
    assert 'status: "applied"' in sup.read_text()


# --------------------------------------------------------------------------- #
# apply reject drops the proposal without touching the episode
# --------------------------------------------------------------------------- #
def test_apply_reject_drops_without_touching_episode(tmp_path):
    store, old, new, stale = _fixture_store()
    actions = consolidate(store, backend=None, now=NOW, dry_run=True)
    stage_actions(store, actions, tmp_path)

    forget = tmp_path / STAGING_DIRNAME / "default" / f"forget--{stale.id[:8]}.md"
    forget.write_text(forget.read_text().replace('verdict: ""', 'verdict: "reject"'))

    result = apply_vault(store, tmp_path)
    assert result.staged_rejected == 1
    assert result.staged_applied == 0

    # episode untouched
    assert store.status(stale.id) == "active"
    assert store.events(episode_id=stale.id, event="forgotten") == []
    assert 'status: "rejected"' in forget.read_text()


# --------------------------------------------------------------------------- #
# idempotent re-staging: no duplicate proposals, prior verdict respected
# --------------------------------------------------------------------------- #
def test_restage_is_idempotent(tmp_path):
    store, old, new, stale = _fixture_store()
    actions = consolidate(store, backend=None, now=NOW, dry_run=True)

    first = stage_actions(store, actions, tmp_path)
    assert first.staged == 2

    # re-stage: same actions, nothing new written
    second = stage_actions(store, actions, tmp_path)
    assert second.staged == 0
    assert second.existing == 2
    assert len(_staging_files(tmp_path)) == 2

    # a prior rejection survives a re-stage (proposal not clobbered)
    forget = tmp_path / STAGING_DIRNAME / "default" / f"forget--{stale.id[:8]}.md"
    forget.write_text(forget.read_text().replace('verdict: ""', 'verdict: "reject"'))
    apply_vault(store, tmp_path)
    third = stage_actions(store, actions, tmp_path)
    assert third.staged == 0
    assert 'status: "rejected"' in forget.read_text()


# --------------------------------------------------------------------------- #
# a pending proposal is inert across repeated apply runs
# --------------------------------------------------------------------------- #
def test_pending_proposal_inert_across_applies(tmp_path):
    store, old, new, stale = _fixture_store()
    actions = consolidate(store, backend=None, now=NOW, dry_run=True)
    stage_actions(store, actions, tmp_path)

    # no verdicts set: apply twice, nothing happens either time
    r1 = apply_vault(store, tmp_path)
    r2 = apply_vault(store, tmp_path)
    assert r1.staged_applied == r1.staged_rejected == 0
    assert r2.staged_applied == r2.staged_rejected == 0
    assert store.status(old.id) == "active"
    assert store.status(stale.id) == "active"


# --------------------------------------------------------------------------- #
# an accepted action whose episode is already resolved is not double-applied
# --------------------------------------------------------------------------- #
def test_accept_on_already_resolved_episode_is_safe(tmp_path):
    store, old, new, stale = _fixture_store()
    actions = consolidate(store, backend=None, now=NOW, dry_run=True)
    stage_actions(store, actions, tmp_path)

    # resolve the target out-of-band, then accept the (now stale) proposal
    store.log_event(old.id, "superseded", {"reason": "duplicate", "by": new.id})
    sup = tmp_path / STAGING_DIRNAME / "default" / f"supersede--{old.id[:8]}.md"
    sup.write_text(sup.read_text().replace('verdict: ""', 'verdict: "accept"'))

    result = apply_vault(store, tmp_path)
    assert result.staged_applied == 0
    assert result.skipped >= 1
    # exactly one superseded event (no double-log)
    assert len(store.events(episode_id=old.id, event="superseded")) == 1


# --------------------------------------------------------------------------- #
# regression guard: default consolidate (no --stage) unchanged
# --------------------------------------------------------------------------- #
def test_default_consolidate_unchanged_and_no_staging_dir(tmp_path):
    """Running `memsub consolidate` without --stage applies exactly the actions
    the library consolidate() computes, and creates no staging folder."""
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    old = store.append(_ep("the repo uses pytest.ini to disable ROS plugins", NOW))
    new = store.append(_ep("the repo uses pytest.ini to disable ROS plugins", NOW + 10))
    store.close()

    rc = main(["consolidate", "--db", str(db), "--no-llm"])
    assert rc == 0

    store = EpisodicStore(db)
    # exactly the dedup supersede the library path produces, applied to the store
    assert store.status(old.id) == "superseded"
    assert store.status(new.id) == "active"
    ev = store.events(episode_id=old.id, event="superseded")[0]
    assert ev.detail == {"reason": "duplicate", "by": new.id,
                         "similarity": ev.detail["similarity"]}
    store.close()

    # no staging artifacts anywhere near the db
    assert not (tmp_path / STAGING_DIRNAME).exists()


# --------------------------------------------------------------------------- #
# CLI: consolidate --stage end to end
# --------------------------------------------------------------------------- #
def test_cli_stage_and_apply(tmp_path, capsys):
    db = tmp_path / "mem.db"
    vault = tmp_path / "vault"
    store = EpisodicStore(db)
    old = store.append(_ep("the repo uses pytest.ini to disable ROS plugins", NOW))
    new = store.append(_ep("the repo uses pytest.ini to disable ROS plugins", NOW + 10))
    store.close()

    rc = main(["consolidate", "--db", str(db), "--no-llm", "--stage", "--vault", str(vault)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 proposal(s) staged" in out and "store unchanged" in out

    # store not mutated by --stage
    store = EpisodicStore(db)
    assert store.status(old.id) == "active"
    store.close()

    # accept and apply
    sup = vault / STAGING_DIRNAME / "default" / f"supersede--{old.id[:8]}.md"
    sup.write_text(sup.read_text().replace('verdict: ""', 'verdict: "accept"'))
    rc = main(["vault", "--db", str(db), "apply", "--vault", str(vault)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 staged applied" in out

    store = EpisodicStore(db)
    assert store.status(old.id) == "superseded"
    store.close()


def test_cli_stage_requires_vault(tmp_path, capsys):
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    store.append(_ep("a note", NOW))
    store.close()
    rc = main(["consolidate", "--db", str(db), "--no-llm", "--stage"])
    assert rc == 1
    assert "--stage requires --vault" in capsys.readouterr().out
