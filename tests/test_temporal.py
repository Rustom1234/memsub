"""Tests for the temporal upgrade: bi-temporal store fields, date-expression
resolution, query-time intent classification, and temporal-aware retrieval.
See docs/temporal-v1-2026-07-20.md and WORKLOG.md (2026-07-20 entry) for
design sources and stated assumptions."""

from datetime import date, datetime

from memsub.backends import MockBackend
from memsub.consolidate import consolidate
from memsub.hybrid import HybridRetriever
from memsub.store import Episode, EpisodicStore
from memsub.temporal import (
    TemporalResolver,
    annotate_episode,
    annotate_episodes,
    classify_intent,
    episode_matches_date,
    ordinal_direction,
    parse_absolute_date,
    resolve_relative,
)

REF = date(2026, 7, 17)  # a fixed reference date, matching "today" in the brief


def _epoch(d: date) -> float:
    return datetime.combine(d, datetime.min.time()).timestamp()


# --------------------------------------------------------------------------- #
# Migration + store round trip
# --------------------------------------------------------------------------- #
def test_episode_default_valid_fields_are_none():
    ep = Episode(content="x", run_id="r", session_id="s")
    assert ep.valid_from is None and ep.valid_to is None


def test_store_round_trips_valid_from_to():
    store = EpisodicStore(":memory:")
    ep = store.append(
        Episode(content="x", run_id="r", session_id="s", valid_from=100.0, valid_to=200.0)
    )
    got = store.get(ep.id)
    assert got.valid_from == 100.0 and got.valid_to == 200.0


def test_store_defaults_valid_fields_to_none_when_unset():
    store = EpisodicStore(":memory:")
    ep = store.append(Episode(content="x", run_id="r", session_id="s"))
    got = store.get(ep.id)
    assert got.valid_from is None and got.valid_to is None


def test_set_valid_to_updates_only_that_column():
    store = EpisodicStore(":memory:")
    ep = store.append(Episode(content="x", run_id="r", session_id="s", valid_from=100.0))
    store.set_valid_to(ep.id, 300.0)
    got = store.get(ep.id)
    assert got.valid_from == 100.0
    assert got.valid_to == 300.0
    assert got.content == "x"  # untouched


def test_migration_adds_columns_to_existing_db(tmp_path):
    db_path = tmp_path / "legacy.db"
    store = EpisodicStore(db_path)
    store.append(Episode(content="pre-migration row", run_id="r", session_id="s"))
    store.close()
    # Reopening runs _migrate() again; columns already present is a no-op,
    # and the pre-existing row reads back with valid_from/valid_to = None.
    reopened = EpisodicStore(db_path)
    rows = reopened.all()
    assert len(rows) == 1
    assert rows[0].valid_from is None and rows[0].valid_to is None
    reopened.close()


# --------------------------------------------------------------------------- #
# Absolute date parsing
# --------------------------------------------------------------------------- #
def test_parse_absolute_date_full():
    assert parse_absolute_date("8 May 2023") == date(2023, 5, 8)
    assert parse_absolute_date("we met on 25 December, 2022 downtown") == date(2022, 12, 25)


def test_parse_absolute_date_month_year_only():
    assert parse_absolute_date("May 2023") == date(2023, 5, 1)


def test_parse_absolute_date_none_without_year():
    assert parse_absolute_date("last March") is None
    assert parse_absolute_date("no date here") is None


# --------------------------------------------------------------------------- #
# Relative date resolution: the dozen-case table plus extras
# --------------------------------------------------------------------------- #
RELATIVE_CASES = [
    ("yesterday", date(2026, 7, 16)),
    ("tomorrow", date(2026, 7, 18)),
    ("today", date(2026, 7, 17)),
    ("3 days ago", date(2026, 7, 14)),
    ("two weeks ago", date(2026, 7, 3)),
    ("in 2 weeks", date(2026, 7, 31)),
    ("next month", date(2026, 8, 17)),
    ("last month", date(2026, 6, 17)),
    ("next year", date(2027, 7, 17)),
    ("last March", date(2026, 3, 17)),
    ("next December", date(2026, 12, 17)),
    ("a month ago", date(2026, 6, 17)),
]


def test_relative_date_table():
    for text, expected in RELATIVE_CASES:
        got = resolve_relative(text, REF)
        assert got == expected, f"{text!r}: expected {expected}, got {got}"


def test_relative_date_embedded_in_sentence():
    assert resolve_relative("I read that book two weeks ago at the cafe", REF) == date(2026, 7, 3)


def test_resolve_relative_none_for_unrelated_text():
    assert resolve_relative("we had a great time at the beach", REF) is None


# --------------------------------------------------------------------------- #
# TemporalResolver: rules first, LLM escalation only when needed, cached
# --------------------------------------------------------------------------- #
def test_resolver_uses_rules_with_no_backend():
    resolver = TemporalResolver(backend=None)
    assert resolver.resolve("I saw her yesterday", REF) == date(2026, 7, 16)
    assert resolver.llm_calls == 0


def test_resolver_no_backend_unresolvable_returns_none_not_a_guess():
    resolver = TemporalResolver(backend=None)
    assert resolver.resolve("I visited recently", REF) is None
    assert resolver.llm_calls == 0


def test_resolver_escalates_to_backend_when_marker_present_but_unresolvable():
    backend = MockBackend(responder=lambda s, m: '{"date": "2026-07-10"}')
    resolver = TemporalResolver(backend=backend)
    got = resolver.resolve("I visited recently", REF)
    assert got == date(2026, 7, 10)
    assert resolver.llm_calls == 1


def test_resolver_never_calls_backend_when_no_marker_at_all():
    backend = MockBackend(responder=lambda s, m: '{"date": "2026-07-10"}')
    resolver = TemporalResolver(backend=backend)
    resolver.resolve("we chatted about the weather", REF)
    assert resolver.llm_calls == 0


def test_resolver_caches_repeat_calls():
    backend = MockBackend(responder=lambda s, m: '{"date": "2026-07-10"}')
    resolver = TemporalResolver(backend=backend)
    resolver.resolve("I visited recently", REF)
    resolver.resolve("I visited recently", REF)
    assert resolver.llm_calls == 1


# --------------------------------------------------------------------------- #
# Write-time annotation
# --------------------------------------------------------------------------- #
def _ep(content, ts=1000.0, session_date="", id_="e1"):
    return Episode(
        content=content, run_id="r", session_id="s", ts=ts, id=id_,
        provenance={"session_date": session_date} if session_date else {},
    )


def test_annotate_episode_resolves_relative_against_session_date():
    ep = _ep("Alice: I went to the beach yesterday.", session_date="17 July 2026")
    resolver = TemporalResolver(backend=None)
    annotated = annotate_episode(ep, resolver)
    assert annotated.valid_from == _epoch(date(2026, 7, 16))
    assert annotated.valid_to is None
    assert annotated.content == ep.content  # nothing else changed


def test_annotate_episode_noop_when_nothing_resolves():
    ep = _ep("Alice: I like pottery.", session_date="17 July 2026")
    resolver = TemporalResolver(backend=None)
    annotated = annotate_episode(ep, resolver)
    assert annotated is ep  # same object, true no-op
    assert annotated.valid_from is None


def test_annotate_episode_falls_back_to_ts_without_session_date():
    ep = _ep("Alice: I went to the beach yesterday.", ts=_epoch(date(2026, 7, 17)))
    resolver = TemporalResolver(backend=None)
    annotated = annotate_episode(ep, resolver)
    assert annotated.valid_from == _epoch(date(2026, 7, 16))


def test_annotate_episodes_batch():
    eps = [
        _ep("Alice: I went to Bali last month.", session_date="17 July 2026", id_="a"),
        _ep("Bob: nothing temporal here.", session_date="17 July 2026", id_="b"),
    ]
    out = annotate_episodes(eps, backend=None)
    assert out[0].valid_from == _epoch(date(2026, 6, 17))
    assert out[1].valid_from is None


# --------------------------------------------------------------------------- #
# Intent classification
# --------------------------------------------------------------------------- #
def test_classify_point_in_time():
    assert classify_intent("What did I do on 8 May 2023?") == "point_in_time"
    assert classify_intent("What happened yesterday?") == "point_in_time"


def test_classify_range():
    assert classify_intent("What did I do before the trip?") == "range"
    assert classify_intent("What happened between May and June?") == "range"


def test_classify_ordinal():
    assert classify_intent("When did I first visit Paris?") == "ordinal"
    assert classify_intent("When did I last see the dentist?") == "ordinal"


def test_classify_current_state():
    assert classify_intent("Is she still working at that company?") == "current_state"


def test_classify_atemporal():
    assert classify_intent("What is Alice's favorite color?") == "atemporal"


def test_classify_bare_when_is_point_in_time_by_rule_no_backend_call():
    # The dominant LOCOMO temporal-question phrasing ("When did X happen?")
    # is unambiguous by rule -- never escalated, see WORKLOG.md 2026-07-20.
    backend = MockBackend(responder=lambda s, m: '{"intent": "ordinal"}')
    assert classify_intent("When did that happen?", backend=backend) == "point_in_time"
    assert len(backend.calls) == 0


def test_classify_ambiguous_escalates_to_backend():
    backend = MockBackend(responder=lambda s, m: '{"intent": "range"}')
    result = classify_intent("It happened recently, tell me more", backend=backend)
    assert result == "range"
    assert len(backend.calls) == 1


def test_classify_ambiguous_no_backend_defaults_conservatively():
    assert classify_intent("It happened recently, tell me more", backend=None) == "point_in_time"


def test_ordinal_direction():
    assert ordinal_direction("When did I first visit?") == "earliest"
    assert ordinal_direction("What is the most recent update?") == "latest"


# --------------------------------------------------------------------------- #
# episode_matches_date
# --------------------------------------------------------------------------- #
def test_matches_by_valid_window():
    ep = Episode(
        content="x", run_id="r", session_id="s",
        valid_from=_epoch(date(2026, 1, 1)), valid_to=_epoch(date(2026, 6, 1)),
    )
    assert episode_matches_date(ep, date(2026, 3, 1))
    assert not episode_matches_date(ep, date(2026, 7, 1))


def test_matches_open_ended_window():
    ep = Episode(content="x", run_id="r", session_id="s", valid_from=_epoch(date(2026, 1, 1)))
    assert episode_matches_date(ep, date(2030, 1, 1))  # still open, matches far future
    assert not episode_matches_date(ep, date(2025, 1, 1))  # before it started


def test_matches_falls_back_to_session_date_without_valid_from():
    ep = Episode(content="x", run_id="r", session_id="s", provenance={"session_date": "8 May 2023"})
    assert episode_matches_date(ep, date(2023, 5, 8))
    assert not episode_matches_date(ep, date(2023, 5, 9))


def test_matches_false_with_no_temporal_info_at_all():
    ep = Episode(content="x", run_id="r", session_id="s")
    assert not episode_matches_date(ep, date(2023, 5, 8))


# --------------------------------------------------------------------------- #
# consolidate(): contradiction-supersede sets valid_to when both temporal
# --------------------------------------------------------------------------- #
def _contradiction_backend():
    return MockBackend(responder=lambda s, m: '[{"pair": 0, "contradicts": true}]')


def test_contradiction_supersede_sets_valid_to_when_both_temporal():
    now = datetime.now().timestamp()
    store = EpisodicStore(":memory:")
    old = store.append(
        Episode(
            content="the project database is postgres", run_id="r", session_id="s",
            kind="fact", ts=now, valid_from=_epoch(date(2026, 1, 1)),
        )
    )
    new = store.append(
        Episode(
            content="the project database is sqlite", run_id="r", session_id="s",
            kind="fact", ts=now + 10, valid_from=_epoch(date(2026, 6, 1)),
        )
    )
    actions = consolidate(store, backend=_contradiction_backend())
    assert [a.reason for a in actions] == ["contradiction"]
    got_old = store.get(old.id)
    assert got_old.valid_to == _epoch(date(2026, 6, 1))  # closed at winner's valid_from
    got_new = store.get(new.id)
    assert got_new.valid_to is None  # winner's window stays open


def test_contradiction_supersede_leaves_valid_to_alone_when_one_side_untimed():
    now = datetime.now().timestamp()
    store = EpisodicStore(":memory:")
    old = store.append(
        Episode(content="the project database is postgres", run_id="r", session_id="s", kind="fact", ts=now)
    )
    store.append(
        Episode(content="the project database is sqlite", run_id="r", session_id="s", kind="fact", ts=now + 10)
    )
    consolidate(store, backend=_contradiction_backend())
    got_old = store.get(old.id)
    assert got_old.valid_to is None  # no temporal scope on either side: untouched


# --------------------------------------------------------------------------- #
# HybridRetriever.rank_temporal: boost correctness + composition
# --------------------------------------------------------------------------- #
def _turn(content, ts, id_, valid_from=None):
    return Episode(content=content, run_id="r", session_id="s", ts=ts, id=id_, kind="turn", valid_from=valid_from)


def test_rank_temporal_boosts_matching_valid_window():
    # "wrong" is a closed (already-superseded) window that ended before the
    # queried date; "right" is open-ended starting on the queried date --
    # only "right" is valid on 8 May 2023.
    right = _turn("Alice: I visited the museum.", 1, "right", valid_from=_epoch(date(2023, 5, 8)))
    wrong = Episode(
        content="Alice: I visited the museum too.", run_id="r", session_id="s", ts=2, id="wrong",
        kind="turn", valid_from=_epoch(date(2023, 1, 1)), valid_to=_epoch(date(2023, 3, 1)),
    )
    hy = HybridRetriever(temporal=False, speaker_boost=0.0, session_weight=0.0)
    hy.ingest([right, wrong])
    order = hy.rank_temporal("What did Alice do on 8 May 2023?", intent="point_in_time")
    assert hy.episodes[order[0]].id == "right"


def test_rank_temporal_point_in_time_prefers_session_date_match():
    a = Episode(
        content="Alice: I went hiking.", run_id="r", session_id="s1", ts=1,
        id="a", kind="turn", provenance={"session_date": "8 May 2023"},
    )
    b = Episode(
        content="Alice: I went hiking again.", run_id="r", session_id="s2", ts=2,
        id="b", kind="turn", provenance={"session_date": "12 June 2023"},
    )
    hy = HybridRetriever(temporal=False, speaker_boost=0.0, session_weight=0.0)
    hy.ingest([a, b])
    order = hy.rank_temporal("What did Alice do on 8 May 2023?", intent="point_in_time")
    assert hy.episodes[order[0]].id == "a"


def test_rank_temporal_ordinal_earliest():
    old = _turn("Alice: I moved to Boston.", 1, "old", valid_from=_epoch(date(2020, 1, 1)))
    new = _turn("Alice: I moved to Boston again.", 2, "new", valid_from=_epoch(date(2026, 1, 1)))
    hy = HybridRetriever(temporal=False, speaker_boost=0.0, session_weight=0.0)
    hy.ingest([old, new])
    order = hy.rank_temporal("When did Alice first move to Boston?", intent="ordinal")
    assert hy.episodes[order[0]].id == "old"


def test_rank_temporal_ordinal_latest():
    old = _turn("Alice: I moved to Boston.", 1, "old", valid_from=_epoch(date(2020, 1, 1)))
    new = _turn("Alice: I moved to Boston again.", 2, "new", valid_from=_epoch(date(2026, 1, 1)))
    hy = HybridRetriever(temporal=False, speaker_boost=0.0, session_weight=0.0)
    hy.ingest([old, new])
    order = hy.rank_temporal("When did Alice most recently move to Boston?", intent="ordinal")
    assert hy.episodes[order[0]].id == "new"


def test_rank_temporal_atemporal_matches_plain_rank():
    a = _turn("Alice: I like pottery.", 1, "a")
    b = _turn("Bob: I like painting.", 2, "b")
    hy = HybridRetriever(temporal=False, speaker_boost=0.0, session_weight=0.0)
    hy.ingest([a, b])
    query = "What does Alice like?"
    assert hy.rank_temporal(query, intent="atemporal") == hy.rank(query)


# --------------------------------------------------------------------------- #
# Default-path byte-identical regression
# --------------------------------------------------------------------------- #
def test_rank_unaffected_by_valid_from_presence():
    a = _turn("Alice: I like pottery.", 1, "a", valid_from=_epoch(date(2020, 1, 1)))
    b = _turn("Bob: I like painting.", 2, "b", valid_from=_epoch(date(2026, 1, 1)))
    a_plain = _turn("Alice: I like pottery.", 1, "a")
    b_plain = _turn("Bob: I like painting.", 2, "b")
    hy_temporal = HybridRetriever()
    hy_temporal.ingest([a, b])
    hy_plain = HybridRetriever()
    hy_plain.ingest([a_plain, b_plain])
    query = "What does Alice like?"
    assert hy_temporal.rank(query) == hy_plain.rank(query)


def test_rank_temporal_accepts_precomputed_base_order():
    a = _turn("Alice: I like pottery.", 1, "a", valid_from=_epoch(date(2020, 1, 1)))
    b = _turn("Bob: I like painting.", 2, "b", valid_from=_epoch(date(2026, 1, 1)))
    hy = HybridRetriever(temporal=False, speaker_boost=0.0, session_weight=0.0)
    hy.ingest([a, b])
    query = "What does Alice like?"
    fixed_base = list(reversed(hy.rank(query)))  # a deliberately "wrong" base order
    got = hy.rank_temporal(query, intent="atemporal", base_order=fixed_base)
    assert got == fixed_base  # atemporal is a no-op on top of whatever base_order was given


def test_rank_decomposed_graph_temporal_accepts_precomputed_base_order():
    store = EpisodicStore(":memory:")
    a = store.append(_turn("Alice: I like pottery.", 1, "a"))
    b = store.append(_turn("Bob: I like painting.", 2, "b"))
    hy = HybridRetriever(temporal=False, speaker_boost=0.0, session_weight=0.0)
    hy.ingest([a, b])
    backend = MockBackend(responder=lambda s, m: "[]")
    query = "What does Alice like?"
    fixed_base = [1, 0]
    got = hy.rank_decomposed_graph_temporal(query, backend, store, intent="atemporal", base_order=fixed_base)
    assert got == fixed_base
    assert len(backend.calls) == 0  # no decompose call made: base_order skipped recomputation
    store.close()


def test_rank_decomposed_graph_temporal_falls_back_for_atemporal():
    store = EpisodicStore(":memory:")
    a = store.append(_turn("Alice: I like pottery.", 1, "a"))
    b = store.append(_turn("Bob: I like painting.", 2, "b"))
    hy = HybridRetriever(temporal=False, speaker_boost=0.0, session_weight=0.0)
    hy.ingest([a, b])
    backend = MockBackend(responder=lambda s, m: "[]")  # decompose yields nothing
    query = "What does Alice like?"
    base = hy.rank_decomposed_graph(query, backend, store)
    got = hy.rank_decomposed_graph_temporal(query, backend, store, intent="atemporal")
    assert got == base
    store.close()
