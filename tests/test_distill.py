"""Tests for memsub/distill.py: Layer 1 (distillation) and Layer 4 (idea
capture). See docs/second-brain-spec-2026-07-20.md and WORKLOG.md's
2026-07-20 "Second brain Layers 1 + 4" entry for design + assumptions."""

from __future__ import annotations

import json
import time

from memsub.backends import MockBackend
from memsub.cli import main
from memsub.distill import (
    MACHINE_BEGIN,
    MACHINE_END,
    MIN_TOPIC_SIZE,
    Topic,
    _refine_component,
    cluster_topics,
    distill,
    match_entities_and_topics,
    write_idea,
)
from memsub.graph import graph_store
from memsub.provenance import CHANNEL_USER_STATED, channel_status, load_or_create_key
from memsub.store import Episode, EpisodicStore
from memsub.vault import note_wikilink


def _seed_pottery_cluster(store: EpisodicStore) -> None:
    """3 episodes sharing entity "Melanie" (2 also share "Caroline"), a
    clean 3-member cluster with no other entity interference."""
    store.append(Episode(content="Melanie: I love pottery classes on Tuesdays.",
                          run_id="r1", session_id="s1", kind="turn", ts=1.0))
    store.append(Episode(content="Caroline: Melanie says pottery relaxes her.",
                          run_id="r1", session_id="s2", kind="turn", ts=2.0))
    store.append(Episode(content="Melanie signed up for another pottery workshop.",
                          run_id="r1", session_id="s3", kind="turn", ts=3.0))


def _seed_and_graph(store: EpisodicStore) -> None:
    graph_store(store, backend=None)


def test_cluster_topics_forms_expected_cluster(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)

    topics = cluster_topics(store)
    assert len(topics) == 1
    assert len(topics[0].members) == 3
    store.close()


def test_cluster_topics_drops_clusters_below_min_size(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    store.append(Episode(content="Derek: I tried a new recipe.", run_id="r", session_id="s1", ts=1.0))
    store.append(Episode(content="Derek: The recipe needs more salt.", run_id="r", session_id="s2", ts=2.0))
    _seed_and_graph(store)

    topics = cluster_topics(store)  # only 2 members sharing "Derek" -- below MIN_TOPIC_SIZE
    assert topics == []
    store.close()


def test_cluster_topics_is_deterministic(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)

    first = [t.member_ids for t in cluster_topics(store)]
    second = [t.member_ids for t in cluster_topics(store)]
    assert first == second
    store.close()


def test_cluster_topics_excludes_hub_entities(tmp_path):
    """35 episodes all sharing one entity ("Everyone", > HUB_CAP=30) and
    nothing else must NOT merge into one giant topic -- each stays an
    isolated singleton, correctly dropped for being below MIN_TOPIC_SIZE
    once the hub entity is excluded from edge-formation."""
    store = EpisodicStore(tmp_path / "mem.db")
    for i in range(35):
        store.append(Episode(content=f"Everyone agreed on item number {i}.",
                              run_id="r", session_id=f"s{i}", ts=float(i)))
    _seed_and_graph(store)

    topics = cluster_topics(store)
    assert topics == []
    store.close()


def test_refine_component_splits_by_lexical_similarity(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    by_id: dict[str, Episode] = {}
    ids = []
    for i in range(5):
        ep = store.append(Episode(content=f"pottery class workshop clay glaze session {i}",
                                   run_id="r", session_id=f"a{i}", ts=float(i)))
        by_id[ep.id] = ep
        ids.append(ep.id)
    for i in range(5):
        ep = store.append(Episode(content=f"tax filing deadline accountant paperwork document {i}",
                                   run_id="r", session_id=f"b{i}", ts=float(10 + i)))
        by_id[ep.id] = ep
        ids.append(ep.id)

    groups = _refine_component(sorted(ids), by_id, threshold=2)
    # the pottery half and the tax half must land in different groups
    pottery_ids = set(ids[:5])
    tax_ids = set(ids[5:])
    assert not any(g == pottery_ids | tax_ids for g in groups)
    matched_pottery = [g for g in groups if g & pottery_ids]
    matched_tax = [g for g in groups if g & tax_ids]
    assert all(g <= pottery_ids for g in matched_pottery)
    assert all(g <= tax_ids for g in matched_tax)
    store.close()


# --------------------------------------------------------------------------- #
# distill(): rendering, idempotence, sovereignty
# --------------------------------------------------------------------------- #
def _mock_content_backend() -> MockBackend:
    payload = json.dumps({
        "title": "Melanie's pottery hobby",
        "overview": "Melanie has taken up pottery and finds it relaxing.",
        "facts": [
            "Melanie attends pottery class on Tuesdays.",
            "Caroline mentioned pottery helps Melanie relax.",
            "Melanie enrolled in a second pottery workshop.",
        ],
        "open_questions": ["How long has Melanie been doing pottery?"],
    })
    return MockBackend(responder=lambda s, m: payload)


def test_distill_writes_topic_note_with_expected_structure(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)
    vault = tmp_path / "vault"

    result = distill(store, vault, backend=_mock_content_backend())
    assert result.topics_found == 1
    assert result.topics_written == 1
    assert result.topics_skipped_unchanged == 0
    assert result.llm_calls == 1

    topics_dir = vault / "topics"
    files = list(topics_dir.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")

    assert 'type: "topic"' in text
    assert 'namespace: "topics"' in text
    assert "member_count: 3" in text
    assert MACHINE_BEGIN in text and MACHINE_END in text
    assert "# Melanie's pottery hobby" in text
    assert "## Key facts" in text
    assert "## Open questions" in text
    assert "How long has Melanie been doing pottery?" in text
    # every member has a wikilink to its own note
    for ep in sorted(store.active(), key=lambda e: e.ts):
        assert f"[[{note_wikilink(ep)}]]" in text
    store.close()


def test_distill_timeline_section_appears_only_with_valid_from(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    now = time.time()
    store.append(Episode(content="Melanie: I love pottery classes on Tuesdays.",
                          run_id="r1", session_id="s1", ts=1.0, valid_from=now - 100))
    store.append(Episode(content="Caroline: Melanie says pottery relaxes her.",
                          run_id="r1", session_id="s2", ts=2.0))
    store.append(Episode(content="Melanie signed up for another pottery workshop.",
                          run_id="r1", session_id="s3", ts=3.0, valid_from=now - 10))
    _seed_and_graph(store)
    vault = tmp_path / "vault"

    distill(store, vault, backend=None)  # backend=None: deterministic fallback content
    text = next((vault / "topics").glob("*.md")).read_text(encoding="utf-8")
    assert "## Timeline" in text
    store.close()


def test_distill_no_timeline_section_without_any_valid_from(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)
    vault = tmp_path / "vault"

    distill(store, vault, backend=None)
    text = next((vault / "topics").glob("*.md")).read_text(encoding="utf-8")
    assert "## Timeline" not in text
    store.close()


def test_distill_fallback_content_with_no_backend_covers_every_member(tmp_path):
    """backend=None must still produce a complete, well-formed note (zero
    LLM calls, deterministic per-member fallback facts)."""
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)
    vault = tmp_path / "vault"

    result = distill(store, vault, backend=None)
    assert result.llm_calls == 0
    assert result.topics_written == 1
    text = next((vault / "topics").glob("*.md")).read_text(encoding="utf-8")
    assert text.count("- ") >= 3  # at least 3 fact bullets
    store.close()


def test_distill_is_idempotent_on_rerun(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)
    vault = tmp_path / "vault"

    distill(store, vault, backend=_mock_content_backend())
    path = next((vault / "topics").glob("*.md"))
    mtime_before = path.stat().st_mtime_ns
    text_before = path.read_text(encoding="utf-8")

    result = distill(store, vault, backend=_mock_content_backend())
    assert result.llm_calls == 0
    assert result.topics_written == 0
    assert result.topics_skipped_unchanged == 1
    assert path.stat().st_mtime_ns == mtime_before
    assert path.read_text(encoding="utf-8") == text_before
    store.close()


def test_distill_sovereignty_preserves_text_outside_markers(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)
    vault = tmp_path / "vault"

    distill(store, vault, backend=_mock_content_backend())
    path = next((vault / "topics").glob("*.md"))
    text = path.read_text(encoding="utf-8")
    human_line = "\n> Rustom's own note: check this against the Notion export.\n"
    edited = text.rstrip("\n") + human_line
    path.write_text(edited, encoding="utf-8")

    # add a 4th member so member_hash changes and regeneration actually runs
    store.append(Episode(content="Melanie mentioned a pottery exhibition too.",
                          run_id="r1", session_id="s4", ts=4.0))
    _seed_and_graph(store)

    distill(store, vault, backend=_mock_content_backend())
    new_text = path.read_text(encoding="utf-8")
    assert "Rustom's own note: check this against the Notion export." in new_text
    store.close()


def test_distill_preserves_unknown_frontmatter_fields_and_created(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)
    vault = tmp_path / "vault"

    distill(store, vault, backend=_mock_content_backend())
    path = next((vault / "topics").glob("*.md"))
    text = path.read_text(encoding="utf-8")
    original_created_line = next(l for l in text.splitlines() if l.startswith("created:"))
    text = text.replace('---\n', '---\nhuman_verified: "true"\n', 1)
    path.write_text(text, encoding="utf-8")

    store.append(Episode(content="Melanie mentioned a pottery exhibition too.",
                          run_id="r1", session_id="s4", ts=4.0))
    _seed_and_graph(store)
    distill(store, vault, backend=_mock_content_backend())

    new_text = path.read_text(encoding="utf-8")
    assert 'human_verified: "true"' in new_text
    assert original_created_line in new_text
    store.close()


def test_distill_verdict_reject_kills_topic_note(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)
    vault = tmp_path / "vault"

    distill(store, vault, backend=_mock_content_backend())
    path = next((vault / "topics").glob("*.md"))
    text = path.read_text(encoding="utf-8")
    text = text.replace('---\n', '---\nverdict: "reject"\n', 1)
    path.write_text(text, encoding="utf-8")

    result = distill(store, vault, backend=_mock_content_backend())
    assert result.topics_killed == 1
    assert not path.exists()
    store.close()


def test_distill_dry_run_writes_nothing(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)
    vault = tmp_path / "vault"

    result = distill(store, vault, backend=_mock_content_backend(), dry_run=True)
    assert result.topics_written == 1
    assert not (vault / "topics").exists()
    store.close()


# --------------------------------------------------------------------------- #
# Layer 4: idea capture
# --------------------------------------------------------------------------- #
def test_write_idea_is_user_stated_and_verified(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    key = load_or_create_key(tmp_path / "provenance.key")

    ep = write_idea(store, "Try the new pottery studio downtown.", namespace="default", key=key)
    assert ep.kind == "idea"
    status, channel = channel_status(ep, key)
    assert channel == CHANNEL_USER_STATED
    store.close()


def test_match_entities_and_topics_finds_known_entity(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)

    entities, topics = match_entities_and_topics(store, "Melanie mentioned pottery again.", namespace="default")
    assert "Melanie" in entities
    assert topics == []  # no vault path given, so no topic scan
    store.close()


def test_match_entities_and_topics_finds_matching_topic(tmp_path):
    store = EpisodicStore(tmp_path / "mem.db")
    _seed_pottery_cluster(store)
    _seed_and_graph(store)
    vault = tmp_path / "vault"
    distill(store, vault, backend=_mock_content_backend())

    entities, topics = match_entities_and_topics(store, "Melanie mentioned pottery again.",
                                                   namespace="default", vault_path=vault)
    assert "Melanie's pottery hobby" in topics
    store.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_distill_end_to_end(tmp_path, capsys):
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    _seed_pottery_cluster(store)
    store.close()

    rc = main(["graph", "backfill", "--db", str(db)])
    assert rc == 0
    capsys.readouterr()

    vault = tmp_path / "vault"
    rc = main(["distill", "--db", str(db), "--vault", str(vault), "--backend", "none"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 topic(s) found, 1 written" in out
    assert (vault / "topics").is_dir()
    assert len(list((vault / "topics").glob("*.md"))) == 1


def test_cli_idea_writes_episode_and_prints_matches(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("MEMSUB_PROVENANCE_KEY_PATH", str(tmp_path / "provenance.key"))
    db = tmp_path / "mem.db"
    store = EpisodicStore(db)
    _seed_pottery_cluster(store)
    store.close()
    main(["graph", "backfill", "--db", str(db)])
    capsys.readouterr()

    rc = main(["idea", "Melanie should try raku pottery firing.", "--db", str(db), "--namespace", "default"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "captured idea" in out
    assert "Melanie" in out

    store = EpisodicStore(db)
    ideas = [ep for ep in store.active() if ep.kind == "idea"]
    assert len(ideas) == 1
    assert ideas[0].content == "Melanie should try raku pottery firing."
    store.close()
