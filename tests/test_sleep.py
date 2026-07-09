"""Sleep consolidation tests: summaries, inferences, links. LLM calls go
through MockBackend, zero cost."""

import json

from memsub.backends import MockBackend
from memsub.consolidate import ConsolidationConfig, consolidate
from memsub.sleep import generate_inferences, generate_links, sleep
from memsub.store import Episode, EpisodicStore


def _ep(content, session_id, ts, run_id="r1", kind="turn"):
    return Episode(content=content, run_id=run_id, session_id=session_id, kind=kind, ts=ts)


def _summary(session_id, content="a summary", ts=100.0, run_id="r1"):
    return Episode(content=content, run_id=run_id, session_id=session_id, kind="summary", ts=ts)


# --------------------------------------------------------------------------- #
# generate_inferences
# --------------------------------------------------------------------------- #
def test_generate_inferences_parses_and_marks_inferred():
    summaries = [_summary("s1"), _summary("s2")]
    payload = json.dumps([{"content": "pattern found", "confidence": 0.8, "based_on": ["s1", "s2"]}])
    backend = MockBackend(responder=lambda s, m: payload)
    inferences = generate_inferences(summaries, backend)
    assert len(inferences) == 1
    inf = inferences[0]
    assert inf.kind == "inference"
    assert inf.content == "pattern found"
    assert inf.provenance["inferred"] is True
    assert inf.provenance["confidence"] == 0.8
    assert inf.provenance["derived_from"] == ["s1", "s2"]


def test_generate_inferences_empty_summaries_no_call():
    calls = []
    backend = MockBackend(responder=lambda s, m: calls.append(1) or "[]")
    assert generate_inferences([], backend) == []
    assert calls == []


def test_generate_inferences_caps_at_max():
    summaries = [_summary("s1")]
    payload = json.dumps([{"content": f"c{i}", "confidence": 0.5} for i in range(10)])
    backend = MockBackend(responder=lambda s, m: payload)
    assert len(generate_inferences(summaries, backend, max_inferences=3)) == 3


def test_generate_inferences_malformed_json_yields_nothing():
    backend = MockBackend(responder=lambda s, m: "not json at all")
    assert generate_inferences([_summary("s1")], backend) == []


# --------------------------------------------------------------------------- #
# generate_links (dependency-free entity co-occurrence)
# --------------------------------------------------------------------------- #
def test_generate_links_connects_shared_entity_across_sessions():
    episodes = [
        _ep("Alice: I love Paris in spring.", "s1", 1.0),
        _ep("Bob: Paris has great museums.", "s2", 2.0),
        _ep("Alice: My cat is orange.", "s3", 3.0),
    ]
    links = generate_links(episodes, min_sessions=2)
    contents = [l.content for l in links]
    assert any("Paris" in c for c in contents)
    assert all(l.kind == "link" for l in links)


def test_generate_links_respects_min_sessions_threshold():
    episodes = [
        _ep("Alice: I love Paris.", "s1", 1.0),
        _ep("Bob: unrelated stuff here.", "s2", 2.0),
    ]
    links = generate_links(episodes, min_sessions=2)
    assert not any("Paris" in l.content for l in links)


def test_generate_links_caps_at_max_links():
    episodes = []
    for i in range(20):
        episodes.append(_ep(f"Speaker{i}: Zephyr appears here too.", f"s{i}", float(i)))
    links = generate_links(episodes, min_sessions=2, max_links=3)
    assert len(links) <= 3


def test_generate_links_empty_input():
    assert generate_links([]) == []


# --------------------------------------------------------------------------- #
# sleep(): end to end
# --------------------------------------------------------------------------- #
def test_sleep_produces_all_three_outputs():
    episodes = [
        _ep("Alice: I visited Paris last week.", "s1", 1.0),
        _ep("Bob: How was Paris?", "s1", 2.0),
        _ep("Alice: Great, saw the Eiffel Tower.", "s1", 3.0),
        _ep("Alice: Paris was even better than last time.", "s2", 4.0),
        _ep("Bob: Nice, glad you went back.", "s2", 5.0),
        _ep("Alice: Yes, planning another trip too.", "s2", 6.0),
    ]

    def responder(system, messages):
        if "session summaries" in system.lower() or "inferences" in system.lower():
            return json.dumps([{"content": "Alice likes Paris", "confidence": 0.9, "based_on": ["s1", "s2"]}])
        return "a session summary"

    backend = MockBackend(responder=responder)
    result = sleep(episodes, backend, min_session_turns=3)
    assert len(result.summaries) == 2
    assert len(result.inferences) == 1
    assert any("Paris" in l.content for l in result.links)
    assert len(result.all_memories) == len(result.summaries) + len(result.inferences) + len(result.links)


# --------------------------------------------------------------------------- #
# consolidation rule: observed beats inferred on contradiction
# --------------------------------------------------------------------------- #
def test_observed_beats_inferred_on_contradiction_even_if_inference_is_newer():
    store = EpisodicStore(":memory:")
    observed = store.append(
        Episode(content="the user lives in Boston", run_id="r", session_id="s",
                kind="fact", importance=1.0, ts=100.0)
    )
    inferred = store.append(
        Episode(content="the user lives in Seattle", run_id="r", session_id="sleep:inference",
                kind="inference", importance=0.7, ts=200.0,  # newer than observed
                provenance={"inferred": True, "confidence": 0.7})
    )
    backend = MockBackend(
        responder=lambda s, m: json.dumps([{"pair": 0, "contradicts": True}])
    )
    actions = consolidate(
        store, backend=backend,
        config=ConsolidationConfig(contradiction_min_sim=0.0, dedup_threshold=0.999),
        namespace="default",
    )
    superseded = [a for a in actions if a.action == "supersede" and a.reason == "contradiction"]
    assert len(superseded) == 1
    assert superseded[0].episode_id == inferred.id  # the inference loses, not the observed fact
    assert superseded[0].detail["by"] == observed.id
    store.close()
