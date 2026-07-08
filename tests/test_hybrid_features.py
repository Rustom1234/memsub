"""Tests for the retrieval-v2 features: stemming, dates, speaker and session signals."""

from memsub.bench import HybridSystem, parse_session_date
from memsub.lexical import stem, tokenize
from memsub.store import Episode


def _ep(content, ts, id, session="s1", date=""):
    return Episode(
        content=content, run_id="r", session_id=session, kind="turn",
        provenance={"session_date": date} if date else {}, ts=ts, id=id,
    )


def test_stem_unifies_morphology():
    assert stem("researching") == stem("research")
    assert stem("camping") == stem("camped") == stem("camp")
    assert stem("activities") == stem("activity")
    assert stem("swimming") == stem("swim")


def test_tokenize_stems_by_default():
    assert tokenize("What did Caroline research?") == tokenize("Caroline researching")


def test_parse_session_date():
    assert parse_session_date("1:56 pm on 8 May, 2023") == "8 May 2023"
    assert parse_session_date("4 pm on 25 December, 2022") == "25 December 2022"
    assert parse_session_date("sometime, no date here") == ""


def test_date_match_boost_prefers_matching_session():
    eps = [
        _ep("Alice: I went hiking near the lake.", 1, "a", session="s1", date="8 May 2023"),
        _ep("Alice: I went hiking near the ridge.", 2, "b", session="s2", date="12 June 2023"),
    ]
    hy = HybridSystem(temporal=True)
    hy.ingest(eps)
    packed = hy.inject("What did Alice do on 8 May 2023?", budget_tokens=15)
    assert packed[0].id == "a"


def test_speaker_boost_prefers_mentioned_speakers_turns():
    eps = [
        _ep("Melanie: I love pottery and painting.", 1, "mel"),
        _ep("Caroline: Melanie you would enjoy the gallery!", 2, "car"),
    ]
    hy = HybridSystem(speaker_boost=1.0)
    hy.ingest(eps)
    packed = hy.inject("What hobbies does Melanie enjoy?", budget_tokens=12)
    assert packed[0].id == "mel"


def test_session_weight_pulls_in_session_neighbors():
    # session s1 is about the trip; the generic turn in s1 should outrank
    # the generic turn in the unrelated session when session_weight is high
    eps = [
        _ep("A: The trip to Lisbon was amazing, the trams especially.", 1, "t1", session="s1"),
        _ep("A: It really was a great time.", 2, "t2", session="s1"),
        _ep("A: It really was a great time.", 3, "t3", session="s2"),
        _ep("A: Work meetings all day today.", 4, "t4", session="s2"),
    ]
    hy = HybridSystem(session_weight=3.0)
    hy.ingest(eps)
    order = hy.rank("How was the trip to Lisbon?")
    ids = [eps[i].id for i in order]
    assert ids.index("t2") < ids.index("t3")


def test_prf_disabled_by_default_and_flat_ranking_ok():
    eps = [_ep(f"A: filler {i}", i, f"f{i}") for i in range(5)]
    hy = HybridSystem()
    hy.ingest(eps)
    assert hy.prf_weight == 0.0
    assert len(hy.rank("filler")) == 5
