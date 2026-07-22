"""Tests for memsub/resonance.py: distance-selection logic (find_resonances,
pure, no LLM) and the bridge orchestration (resonate, stubbed LLM via
MockBackend). See memsub/resonance.py's module docstring for the design."""

from __future__ import annotations

import numpy as np

from memsub.backends import MockBackend
from memsub.resonance import (
    DEFAULT_FLOOR,
    NOTHING_MESSAGE,
    ResonanceMatch,
    _cluster_key,
    find_resonances,
    generate_bridge,
    resonate,
)
from memsub.store import Episode, EpisodicStore


class FakeEmbedder:
    """Deterministic, fully controlled embedder for tests: content
    containing a given keyword gets that keyword's fixed vector, so
    cosine-similarity outcomes are exact and independent of any real
    embedder's hashing/lexical noise."""

    dim = 3

    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors

    def embed(self, text: str) -> np.ndarray:
        for key, vec in self.vectors.items():
            if key in text:
                return np.array(vec, dtype=float)
        return np.zeros(self.dim)


def _store() -> EpisodicStore:
    return EpisodicStore(":memory:")


def _add(store: EpisodicStore, content: str, ns: str = "test") -> Episode:
    return store.append(Episode(content=content, run_id="r", session_id="s", namespace=ns))


def _link(store: EpisodicStore, ep: Episode, *entity_names: str) -> None:
    for name in entity_names:
        eid = f"{ep.namespace}::{name}"
        store.upsert_entity(eid, ep.namespace, "TOPIC", ep.ts)
        store.link_entity(eid, ep.id, ep.ts)


# --------------------------------------------------------------------------- #
# find_resonances: distance selection, no LLM
# --------------------------------------------------------------------------- #
def test_excludes_top_matchs_cluster_and_diversifies_across_remaining_clusters():
    store = _store()
    embedder = FakeEmbedder({
        "IDEA": [1.0, 0.0, 0.0],
        "obvious": [0.95, 0.05, 0.0],   # near-duplicate of the idea, same cluster as top match
        "distant_a": [0.6, 0.4, 0.0],   # resonant but a different cluster
        "distant_b": [0.5, 0.5, 0.0],   # resonant, yet another cluster
        "unrelated": [0.0, 0.0, 1.0],   # below floor
    })

    obvious = _add(store, "obvious lighthouse fact")
    _link(store, obvious, "lighthouse")
    distant_a = _add(store, "distant_a sound design fact")
    _link(store, distant_a, "sound")
    distant_b = _add(store, "distant_b running habit fact")
    _link(store, distant_b, "running")
    unrelated = _add(store, "unrelated freelance invoice fact")
    _link(store, unrelated, "invoice")

    matches = find_resonances("IDEA text", store, embedder, floor=0.15, top_n=3)

    ids = {m.episode.id for m in matches}
    assert obvious.id not in ids  # excluded: same cluster as the single closest match
    assert unrelated.id not in ids  # excluded: below the resonance floor
    assert ids == {distant_a.id, distant_b.id}
    # ranked by score descending
    assert matches[0].episode.id == distant_a.id
    assert matches[0].score > matches[1].score


def test_nothing_resonates_when_everything_is_below_the_floor():
    store = _store()
    embedder = FakeEmbedder({"IDEA": [1.0, 0.0], "far": [0.0, 1.0]})
    _add(store, "far off-topic fact")

    assert find_resonances("IDEA text", store, embedder, floor=0.5) == []


def test_nothing_resonates_when_every_candidate_shares_the_top_clusters_topic():
    """Honesty rule: if every resonant memory is the same obvious topic as
    the single closest match, there is no surprising angle left -- report
    nothing rather than fabricate a "distant" connection that isn't one."""
    store = _store()
    embedder = FakeEmbedder({
        "IDEA": [1.0, 0.0],
        "a": [0.9, 0.1],
        "b": [0.8, 0.2],
    })
    ep_a = _add(store, "a fact")
    _link(store, ep_a, "lighthouse")
    ep_b = _add(store, "b fact")
    _link(store, ep_b, "lighthouse")  # same cluster as the top match

    assert find_resonances("IDEA text", store, embedder, floor=0.15) == []


def test_empty_idea_and_empty_store_are_honest_empties():
    store = _store()
    embedder = FakeEmbedder({})
    assert find_resonances("   ", store, embedder) == []
    assert find_resonances("something", store, embedder) == []  # empty store


def test_cluster_key_falls_back_to_namespace_kind_without_graph_links():
    store = _store()
    ep = _add(store, "no entities linked yet")
    assert _cluster_key(store, ep) == f"{ep.namespace}:{ep.kind}"


def test_cluster_key_prefers_the_lowest_degree_entity():
    """A generic entity linked to many episodes (e.g. the vault owner's own
    name) must never win over a specific one linked to few -- otherwise
    every episode in a single-person vault collapses into one giant
    cluster and nothing distant ever survives exclusion."""
    store = _store()
    generic = _add(store, "generic")
    specific = _add(store, "specific")
    other = _add(store, "other")
    _link(store, generic, "maya")
    _link(store, specific, "maya", "priya")  # "maya" links 3 episodes, "priya" links 1
    _link(store, other, "maya")

    assert _cluster_key(store, specific) == f"entity:{specific.namespace}::priya"


# --------------------------------------------------------------------------- #
# resonate(): find_resonances + one LLM bridge call per match
# --------------------------------------------------------------------------- #
def test_resonate_calls_backend_once_per_match_and_fills_in_bridges():
    store = _store()
    embedder = FakeEmbedder({
        "IDEA": [1.0, 0.0, 0.0],
        "top": [0.9, 0.1, 0.0],
        "other": [0.6, 0.4, 0.0],
    })
    top = _add(store, "top match fact")
    _link(store, top, "lighthouse")
    other = _add(store, "other cluster fact")
    _link(store, other, "sound")

    calls = []

    def responder(system, messages):
        calls.append(messages[0]["content"])
        return "These connect because both are about attention. "

    backend = MockBackend(responder=responder)
    result = resonate("IDEA text", store, embedder, backend, floor=0.15, top_n=3)

    assert len(result.matches) == 1  # only `other` survives cluster exclusion
    assert result.matches[0].bridge.startswith("These connect because")
    assert len(calls) == 1
    assert result.message == ""
    assert result.bridge_error == ""


def test_resonate_reports_nothing_resonates_yet_without_calling_backend():
    store = _store()
    embedder = FakeEmbedder({"IDEA": [1.0, 0.0]})
    calls = []
    backend = MockBackend(responder=lambda s, m: calls.append(1) or "should not be called")

    result = resonate("IDEA text", store, embedder, backend, floor=0.5)

    assert result.matches == []
    assert result.message == NOTHING_MESSAGE
    assert calls == []


def test_resonate_backend_none_keeps_matches_but_reports_bridge_error():
    store = _store()
    embedder = FakeEmbedder({
        "IDEA": [1.0, 0.0, 0.0], "top": [0.9, 0.1, 0.0], "other": [0.6, 0.4, 0.0],
    })
    top = _add(store, "top fact")
    _link(store, top, "lighthouse")
    other = _add(store, "other fact")
    _link(store, other, "sound")

    result = resonate("IDEA text", store, embedder, None, floor=0.15)

    assert len(result.matches) == 1
    assert result.matches[0].bridge == ""
    assert "ollama" in result.bridge_error


def test_resonate_backend_exception_is_caught_gracefully():
    store = _store()
    embedder = FakeEmbedder({
        "IDEA": [1.0, 0.0, 0.0], "top": [0.9, 0.1, 0.0], "other": [0.6, 0.4, 0.0],
    })
    top = _add(store, "top fact")
    _link(store, top, "lighthouse")
    other = _add(store, "other fact")
    _link(store, other, "sound")

    class ExplodingBackend:
        name = "boom"

        def complete(self, system, messages):
            raise RuntimeError("connection refused")

    result = resonate("IDEA text", store, embedder, ExplodingBackend(), floor=0.15)

    assert len(result.matches) == 1
    assert result.matches[0].bridge == ""
    assert "connection refused" in result.bridge_error


def test_generate_bridge_strips_think_blocks():
    backend = MockBackend(responder=lambda s, m: "<think>reasoning</think>These connect because of X.")
    bridge = generate_bridge(backend, "idea", "memory")
    assert bridge == "These connect because of X."
