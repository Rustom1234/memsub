"""Graph layer tests: schema migration, extraction determinism, the staged
dedup funnel, bounded traversal, RRF fold, and the default-path regression
guarantee. LLM calls (dedup funnel only) go through MockBackend, zero cost.
"""

import sqlite3
import time

from memsub.backends import MockBackend
from memsub.consolidate import consolidate
from memsub.graph import (
    EntityResolver,
    entity_id,
    extract_episode_entities,
    graph_episode_order,
    graph_store,
    normalize_entity,
)
from memsub.hybrid import HybridRetriever
from memsub.store import Episode, EpisodicStore


def _ep(content, ts, id, session="s1", run_id="r"):
    return Episode(content=content, run_id=run_id, session_id=session, kind="turn", ts=ts, id=id)


# --------------------------------------------------------------------------- #
# Migration: additive, safe against a pre-existing store
# --------------------------------------------------------------------------- #
def test_migration_adds_graph_tables_to_pre_existing_store(tmp_path):
    db = tmp_path / "old.db"
    # Build a store with only the pre-graph-layer schema (episodes +
    # memory_events + extraction_checkpoints), mimicking a real store
    # created before this feature existed.
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE episodes (
            id TEXT PRIMARY KEY, ts REAL NOT NULL, run_id TEXT NOT NULL,
            session_id TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
            importance REAL NOT NULL DEFAULT 1.0, provenance TEXT NOT NULL DEFAULT '{}',
            namespace TEXT NOT NULL DEFAULT 'default'
        );
        """
    )
    conn.execute(
        "INSERT INTO episodes (id, ts, run_id, session_id, kind, content) "
        "VALUES ('e1', 1.0, 'r', 's', 'turn', 'Alice: hello')"
    )
    conn.commit()
    conn.close()

    store = EpisodicStore(db)
    tables = {
        r[0] for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"entities", "entity_links"} <= tables
    # pre-existing data survived the migration untouched
    assert store.get("e1").content == "Alice: hello"
    # the new tables are usable immediately
    assert store.entity_count() == 0
    assert store.entity_link_count() == 0
    store.close()


# --------------------------------------------------------------------------- #
# Extraction determinism
# --------------------------------------------------------------------------- #
def test_extraction_is_deterministic():
    ep = _ep("Melanie: I love pottery and the Lisbon trip with Caroline.", 1, "a")
    first = extract_episode_entities(ep)
    second = extract_episode_entities(ep)
    assert first == second
    assert "Melanie" in first
    assert "Caroline" in first


# --------------------------------------------------------------------------- #
# Dedup funnel: clean match, clean miss, ambiguous (stubbed backend)
# --------------------------------------------------------------------------- #
def test_resolver_clean_exact_match_makes_zero_llm_calls():
    store = EpisodicStore(":memory:")
    resolver = EntityResolver(backend=None)
    id1, type1 = resolver.resolve("Melanie", "default", store)
    store.upsert_entity(id1, "default", type1, 1.0)
    id2, type2 = resolver.resolve("Melanie", "default", store)
    assert id1 == id2 == entity_id("default", "melanie")
    assert resolver.llm_calls == 0
    store.close()


def test_resolver_clean_miss_makes_zero_llm_calls_and_creates_new_entity():
    store = EpisodicStore(":memory:")
    resolver = EntityResolver(backend=None)
    id1, _ = resolver.resolve("Melanie", "default", store)
    store.upsert_entity(id1, "default", "PERSON", 1.0)
    # "Bulgaria" is nowhere near "Melanie" by difflib ratio: a clean miss,
    # not an ambiguous pair, so it must never reach the LLM funnel.
    id2, _ = resolver.resolve("Bulgaria", "default", store)
    assert id1 != id2
    assert id2 == entity_id("default", "bulgaria")
    assert resolver.llm_calls == 0
    store.close()


def test_resolver_ambiguous_pair_costs_exactly_one_llm_call_and_is_cached():
    store = EpisodicStore(":memory:")
    calls = []

    def responder(system, messages):
        calls.append(messages[0]["content"])
        return '{"same": true}'

    backend = MockBackend(responder=responder)
    resolver = EntityResolver(backend=backend)
    id1, type1 = resolver.resolve("Melanie", "default", store)
    store.upsert_entity(id1, "default", type1, 1.0)

    # "Melaine" is a near-miss of "Melanie" by difflib ratio (0.857, above
    # NEAR_MISS_THRESHOLD) -> ambiguous, resolved by exactly one LLM call,
    # and merged into the same entity.
    id2, _ = resolver.resolve("Melaine", "default", store)
    assert id2 == id1
    assert resolver.llm_calls == 1
    assert len(calls) == 1

    # A second episode mentioning "Melaine" again must hit the cache, not
    # the backend a second time: same pair, resolved once per resolver run.
    id3, _ = resolver.resolve("Melaine", "default", store)
    assert id3 == id1
    assert resolver.llm_calls == 1
    assert len(calls) == 1
    store.close()


def test_resolver_ambiguous_pair_without_backend_degrades_to_clean_miss():
    store = EpisodicStore(":memory:")
    resolver = EntityResolver(backend=None)
    id1, type1 = resolver.resolve("Melanie", "default", store)
    store.upsert_entity(id1, "default", type1, 1.0)
    # near-miss text, but no backend given: never guess, treat as new entity
    id2, _ = resolver.resolve("Melaine", "default", store)
    assert id2 != id1
    assert resolver.llm_calls == 0
    store.close()


# --------------------------------------------------------------------------- #
# graph_store: write path, idempotency
# --------------------------------------------------------------------------- #
def test_graph_store_upserts_entities_and_links_and_is_idempotent():
    store = EpisodicStore(":memory:")
    store.append(_ep("Melanie: I love pottery on weekends.", 1, "a"))
    store.append(_ep("Caroline: Melanie you would enjoy the gallery!", 2, "b"))
    result = graph_store(store)
    assert result.episodes_processed == 2
    assert result.entities_upserted == 2  # Melanie, Caroline
    assert result.links_created == 3  # Melanie-a, Melanie-b, Caroline-b
    assert result.llm_calls == 0

    # Idempotent rerun: already-graphed episodes are skipped
    result2 = graph_store(store)
    assert result2.episodes_processed == 0
    assert result2.entities_upserted == 0
    assert result2.links_created == 0
    assert store.entity_count() == 2
    assert store.entity_link_count() == 3
    store.close()


def test_graph_store_dry_run_writes_nothing():
    store = EpisodicStore(":memory:")
    store.append(_ep("Melanie: I love pottery.", 1, "a"))
    result = graph_store(store, dry_run=True)
    assert result.episodes_processed == 1
    assert store.entity_count() == 0
    assert store.entity_link_count() == 0
    store.close()


def test_consolidate_enable_graph_false_by_default_populates_nothing():
    store = EpisodicStore(":memory:")
    store.append(_ep("Melanie: I love pottery.", time.time(), "a"))
    consolidate(store)
    assert store.entity_count() == 0
    assert store.entity_link_count() == 0
    store.close()


def test_consolidate_enable_graph_true_populates_the_index():
    store = EpisodicStore(":memory:")
    store.append(_ep("Melanie: I love pottery.", time.time(), "a"))
    consolidate(store, enable_graph=True)
    assert store.entity_count() == 1
    assert store.entity_link_count() == 1
    store.close()


# --------------------------------------------------------------------------- #
# Traversal boundedness: 1-hop vs 2-hop
# --------------------------------------------------------------------------- #
def _chained_episodes():
    # No "Speaker: " prefix (all lowercase apart from the entity names
    # themselves), so the only extracted entities are Xenon/Yara/Zorro --
    # a shared speaker label across every episode would act as a hub entity
    # and defeat hop-bounding, which is exactly what this test checks for.
    #
    # a: entity Xenon only.  b: entities Xenon and Yara (bridges a and c).
    # c: entity Yara and Zorro.  d: entity Zorro only. A 1-hop query for
    # "Xenon" reaches {a, b} (episodes directly linked to Xenon). A 2-hop
    # query for "Xenon" additionally reaches entity Yara's other episode (c)
    # via b, but never reaches d (that would be a third hop, out of bounds).
    return [
        _ep("the story mentioned Xenon and nothing else.", 1, "a"),
        _ep("later Xenon appeared again alongside Yara in the tale.", 2, "b"),
        _ep("afterward Yara joined forces with Zorro on the quest.", 3, "c"),
        _ep("finally Zorro completed the quest alone.", 4, "d"),
    ]


def test_graph_episode_order_1hop_vs_2hop_boundedness():
    store = EpisodicStore(":memory:")
    eps = [store.append(e) for e in _chained_episodes()]
    graph_store(store)

    one_hop = graph_episode_order("What about Xenon?", store, eps, hops=1)
    two_hop = graph_episode_order("What about Xenon?", store, eps, hops=2)
    one_ids = {eps[i].id for i in one_hop}
    two_ids = {eps[i].id for i in two_hop}

    assert one_ids == {"a", "b"}
    assert two_ids == {"a", "b", "c"}  # one more entity-episode hop, not open-ended
    assert "d" not in two_ids  # third hop is out of bounds
    assert one_ids <= two_ids
    store.close()


def test_graph_episode_order_empty_when_no_entities_match():
    store = EpisodicStore(":memory:")
    eps = [store.append(e) for e in _chained_episodes()]
    graph_store(store)
    assert graph_episode_order("nothing relevant here", store, eps) == []
    store.close()


# --------------------------------------------------------------------------- #
# RRF fold: rank_graph actually moves ranking when the graph signal disagrees
# --------------------------------------------------------------------------- #
def test_rank_graph_promotes_co_mentioned_episode_over_base_rank():
    # "filler" is lexically closer to the query than "bridge", so base rank()
    # puts filler first. "bridge" shares entity "Zephyr" with "anchor" (an
    # episode that strongly matches the query), so the graph signal should
    # promote bridge above filler once folded in via rank_graph.
    store = EpisodicStore(":memory:")
    eps = [
        store.append(_ep("A: Tell me about Zephyr the project kickoff.", 1, "anchor")),
        store.append(_ep("A: Zephyr had a status update today.", 2, "bridge")),
        store.append(_ep("A: kickoff kickoff kickoff filler unrelated text.", 3, "filler")),
    ]
    graph_store(store)
    hy = HybridRetriever()
    hy.ingest(eps)

    base_order = hy.rank("Tell me about the kickoff")
    base_ids = [eps[i].id for i in base_order]
    assert base_ids.index("filler") < base_ids.index("bridge")  # base rank prefers filler

    graph_order = hy.rank_graph("Tell me about the kickoff and Zephyr", store)
    graph_ids = [eps[i].id for i in graph_order]
    assert graph_ids.index("bridge") < graph_ids.index("filler")  # graph signal flips it
    store.close()


# --------------------------------------------------------------------------- #
# Default-path regression: rank()/search() are byte-identical regardless of
# graph population
# --------------------------------------------------------------------------- #
def test_default_rank_and_search_are_unaffected_by_graph_population():
    store = EpisodicStore(":memory:")
    eps = [
        store.append(_ep("Melanie: I love pottery on weekends.", 1, "a")),
        store.append(_ep("Caroline: Melanie you would enjoy the gallery!", 2, "b")),
    ]
    hy = HybridRetriever()
    hy.ingest(eps)

    rank_before = hy.rank("What does Melanie enjoy?")
    search_before = hy.search("What does Melanie enjoy?")

    graph_store(store)  # populate the graph index after the fact

    rank_after = hy.rank("What does Melanie enjoy?")
    search_after = hy.search("What does Melanie enjoy?")

    assert rank_before == rank_after
    assert search_before == search_after
    store.close()


def test_rank_graph_falls_back_to_plain_rank_when_store_has_no_graph_data():
    store = EpisodicStore(":memory:")
    eps = [store.append(_ep("Melanie: I love pottery.", 1, "a"))]
    hy = HybridRetriever()
    hy.ingest(eps)
    # graph_store() never called: store has no entities/entity_links rows
    assert hy.rank_graph("What does Melanie enjoy?", store) == hy.rank("What does Melanie enjoy?")
    store.close()
