"""Tests for the tiny learned reranker (2026-07-20): the generic logistic
trainer in memsub/rerank.py, HybridRetriever.candidate_features(), and the
opt-in rank_reranked/search_reranked path. See WORKLOG.md 2026-07-20 entry
and docs/rerank-abstain-2026-07-20.md for design sources."""

from __future__ import annotations

import numpy as np

from memsub.graph import graph_store
from memsub.hybrid import HybridRetriever
from memsub.rerank import (
    RERANK_FEATURE_NAMES,
    apply_logistic,
    load_weights,
    rank_by_model,
    rerank_feature_vector,
    save_weights,
    score_candidate,
    train_logistic,
)
from memsub.store import Episode, EpisodicStore


def _ep(content: str, ts: float, id_: str, session: str = "s1") -> Episode:
    return Episode(content=content, run_id="t", session_id=session, kind="turn", ts=ts, id=id_)


# --------------------------------------------------------------------------- #
# train_logistic / apply_logistic
# --------------------------------------------------------------------------- #
def test_train_logistic_separates_linearly_separable_data():
    X = np.array([[1.0, 1.0], [0.9, 0.8], [0.0, 0.1], [0.1, 0.0]])
    y = np.array([1.0, 1.0, 0.0, 0.0])
    weights = train_logistic(X, y, epochs=800)
    p_pos = apply_logistic(weights, [1.0, 0.9])
    p_neg = apply_logistic(weights, [0.0, 0.05])
    assert p_pos > 0.7
    assert p_neg < 0.3


def test_train_logistic_returns_json_serializable_shape():
    X = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.0, 0.0]])
    y = np.array([1.0, 0.0, 1.0, 0.0])
    weights = train_logistic(X, y, epochs=50)
    assert set(weights) == {"mean", "std", "w", "b", "n_train"}
    assert len(weights["mean"]) == len(weights["std"]) == len(weights["w"]) == 2
    assert weights["n_train"] == 4


def test_apply_logistic_is_bounded_probability():
    weights = {"mean": [0.0], "std": [1.0], "w": [10.0], "b": 0.0}
    assert 0.0 < apply_logistic(weights, [5.0]) <= 1.0
    assert 0.0 <= apply_logistic(weights, [-5.0]) < 1.0


def test_save_and_load_weights_round_trip(tmp_path):
    weights = train_logistic(np.array([[1.0], [0.0]]), np.array([1.0, 0.0]), epochs=10)
    path = tmp_path / "w.json"
    save_weights(weights, path)
    loaded = load_weights(path)
    assert loaded == weights


# --------------------------------------------------------------------------- #
# rerank_feature_vector / score_candidate / rank_by_model
# --------------------------------------------------------------------------- #
def test_rerank_feature_vector_orders_by_feature_names():
    feat = {name: float(i) for i, name in enumerate(RERANK_FEATURE_NAMES)}
    feat["index"] = 0
    assert rerank_feature_vector(feat) == [float(i) for i in range(len(RERANK_FEATURE_NAMES))]


def test_rank_by_model_prefers_higher_scoring_candidate():
    weights = {
        "mean": [0.0] * len(RERANK_FEATURE_NAMES),
        "std": [1.0] * len(RERANK_FEATURE_NAMES),
        "w": [1.0] + [0.0] * (len(RERANK_FEATURE_NAMES) - 1),  # only bm25_norm matters
        "b": 0.0,
    }
    low = {name: 0.0 for name in RERANK_FEATURE_NAMES}
    high = {name: 0.0 for name in RERANK_FEATURE_NAMES}
    low["index"], high["index"] = 0, 1
    low["bm25_norm"], high["bm25_norm"] = 0.1, 0.9
    order = rank_by_model(weights, [low, high])
    assert order == [1, 0]
    assert score_candidate(weights, high) > score_candidate(weights, low)


# --------------------------------------------------------------------------- #
# HybridRetriever.candidate_features
# --------------------------------------------------------------------------- #
def test_candidate_features_empty_store_returns_empty_list():
    hy = HybridRetriever(temporal=False, session_weight=0.0)
    hy.ingest([])
    assert hy.candidate_features("anything") == []


def test_candidate_features_shape_and_bounds():
    eps = [
        _ep("Alice: I love hiking near the lake.", 1, "a"),
        _ep("Bob: my favorite food is pizza.", 2, "b"),
        _ep("Alice: hiking trails near the lake are beautiful in fall.", 3, "c"),
    ]
    hy = HybridRetriever(temporal=False, session_weight=0.0)
    hy.ingest(eps)
    feats = hy.candidate_features("What does Alice like to do near the lake?")
    assert len(feats) == 3
    for f in feats:
        assert set(f) == {"index"} | set(RERANK_FEATURE_NAMES)
        for name in RERANK_FEATURE_NAMES:
            assert 0.0 <= f[name] <= 1.0 + 1e-9
    # Alice's two hiking/lake episodes should score higher entity_overlap
    # than Bob's unrelated pizza episode.
    by_index = {f["index"]: f for f in feats}
    assert by_index[0]["entity_overlap"] >= by_index[1]["entity_overlap"]
    assert by_index[2]["entity_overlap"] >= by_index[1]["entity_overlap"]


def test_candidate_features_graph_boost_needs_store_with_entities():
    eps = [_ep("Alice: I love hiking near the lake.", 1, "a")]
    hy = HybridRetriever(temporal=False, session_weight=0.0)
    hy.ingest(eps)
    # No store passed at all -> graph_boost always 0.0.
    feats_no_store = hy.candidate_features("What does Alice like?")
    assert all(f["graph_boost"] == 0.0 for f in feats_no_store)

    store = EpisodicStore(":memory:")
    written = [store.append(ep) for ep in eps]
    graph_store(store)
    hy2 = HybridRetriever(temporal=False, session_weight=0.0)
    hy2.ingest(written)
    feats = hy2.candidate_features("What does Alice like?", store=store)
    assert any(f["graph_boost"] == 1.0 for f in feats)
    store.close()


def test_candidate_features_pool_size_caps_returned_rows():
    eps = [_ep(f"Alice: fact number {i} about hiking.", i, f"e{i}") for i in range(20)]
    hy = HybridRetriever(temporal=False, session_weight=0.0)
    hy.ingest(eps)
    feats = hy.candidate_features("hiking", pool_size=5)
    assert len(feats) == 5


# --------------------------------------------------------------------------- #
# rank_reranked / search_reranked (opt-in path)
# --------------------------------------------------------------------------- #
def test_rank_reranked_never_drops_a_candidate():
    eps = [_ep(f"topic {i} chatter about the weather", i, f"e{i}") for i in range(10)]
    hy = HybridRetriever(temporal=False, session_weight=0.0)
    hy.ingest(eps)
    weights = {
        "mean": [0.0] * len(RERANK_FEATURE_NAMES),
        "std": [1.0] * len(RERANK_FEATURE_NAMES),
        "w": [0.0] * len(RERANK_FEATURE_NAMES),
        "b": 0.0,
    }
    order = hy.rank_reranked("weather", weights, pool_size=4)
    assert sorted(order) == list(range(10))


def test_rank_reranked_reorders_pool_by_model_score():
    eps = [
        _ep("Alice: I love hiking near the lake.", 1, "a"),
        _ep("Bob: my favorite food is pizza.", 2, "b"),
    ]
    hy = HybridRetriever(temporal=False, session_weight=0.0)
    hy.ingest(eps)
    # A model that only cares about entity_overlap, weighted heavily so the
    # entity-matching episode always wins regardless of base rank() order.
    weights = {
        "mean": [0.0] * len(RERANK_FEATURE_NAMES),
        "std": [1.0] * len(RERANK_FEATURE_NAMES),
        "w": [0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0],
        "b": 0.0,
    }
    order = hy.rank_reranked("What does Alice like?", weights, pool_size=2)
    assert order[0] == 0  # Alice's episode, higher entity_overlap


def test_rank_reranked_empty_store_falls_back_to_plain_rank():
    hy = HybridRetriever(temporal=False, session_weight=0.0)
    hy.ingest([])
    weights = {"mean": [0.0] * 7, "std": [1.0] * 7, "w": [0.0] * 7, "b": 0.0}
    assert hy.rank_reranked("q", weights) == []


def test_search_reranked_returns_top_k_pairs():
    eps = [_ep(f"Alice: fact {i} about hiking", i, f"e{i}") for i in range(5)]
    hy = HybridRetriever(temporal=False, session_weight=0.0)
    hy.ingest(eps)
    weights = {"mean": [0.0] * 7, "std": [1.0] * 7, "w": [0.0] * 7, "b": 0.0}
    results = hy.search_reranked("hiking", weights, k=3)
    assert len(results) == 3
    assert all(isinstance(score, float) for _, score in results)
