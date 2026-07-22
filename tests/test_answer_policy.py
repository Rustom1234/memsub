"""Tests for the abstention calibration policy (2026-07-20). See
WORKLOG.md's 2026-07-20 entry and docs/rerank-abstain-2026-07-20.md for the
calibration run and pre-registered success criteria."""

from __future__ import annotations

from memsub.answer_policy import (
    ABSTAIN_MESSAGE,
    FEATURE_NAMES,
    AbstentionPolicy,
    answer_with_policy,
    evidence_features,
    evidence_vector,
)
from memsub.hybrid import HybridRetriever
from memsub.rerank import train_logistic
from memsub.store import Episode


def _ep(content: str, ts: float, id_: str) -> Episode:
    return Episode(content=content, run_id="t", session_id="s1", kind="turn", ts=ts, id=id_)


def _retriever(eps: list[Episode]) -> HybridRetriever:
    hy = HybridRetriever(temporal=False, session_weight=0.0)
    hy.ingest(eps)
    return hy


# --------------------------------------------------------------------------- #
# evidence_features
# --------------------------------------------------------------------------- #
def test_evidence_features_empty_store_is_all_zero():
    hy = _retriever([])
    feats = evidence_features(hy, "anything")
    assert feats == {"top1_rrf": 0.0, "margin": 0.0, "entity_coverage": 0.0, "agreement": 0.0}


def test_evidence_features_entity_free_query_is_neutral():
    hy = _retriever([_ep("Alice: I love hiking.", 1, "a")])
    feats = evidence_features(hy, "q")
    assert feats["entity_coverage"] == 0.5
    assert feats["agreement"] == 0.5


def test_evidence_features_strong_match_scores_higher_coverage():
    hy = _retriever(
        [
            _ep("Alice: I love hiking near the lake.", 1, "a"),
            _ep("Bob: my favorite food is pizza.", 2, "b"),
        ]
    )
    strong = evidence_features(hy, "What does Alice like to do near the lake?")
    weak = evidence_features(hy, "What does Bob eat that has nothing to do with the query terms?")
    assert strong["entity_coverage"] >= 0.5

    # never touches the gold answer -- evidence_features has no gold param at all
    import inspect

    assert "gold" not in inspect.signature(evidence_features).parameters


def test_evidence_vector_matches_feature_names_order():
    feats = {"top1_rrf": 0.1, "margin": 0.2, "entity_coverage": 0.3, "agreement": 0.4}
    assert evidence_vector(feats) == [feats[n] for n in FEATURE_NAMES]


# --------------------------------------------------------------------------- #
# AbstentionPolicy
# --------------------------------------------------------------------------- #
def test_policy_with_no_weights_always_answers():
    policy = AbstentionPolicy(weights=None)
    feats = {"top1_rrf": 0.0, "margin": 0.0, "entity_coverage": 0.0, "agreement": 0.0}
    assert policy.score(feats) == 1.0
    assert not policy.should_abstain(feats)


def test_policy_abstains_below_threshold():
    import numpy as np

    X = np.array([[1.0, 1.0, 1.0, 1.0], [0.0, 0.0, 0.0, 0.0]])
    y = np.array([1.0, 0.0])
    weights = train_logistic(X, y, epochs=500)
    policy = AbstentionPolicy(weights=weights, threshold=0.5)
    low_feats = {"top1_rrf": 0.0, "margin": 0.0, "entity_coverage": 0.0, "agreement": 0.0}
    high_feats = {"top1_rrf": 1.0, "margin": 1.0, "entity_coverage": 1.0, "agreement": 1.0}
    assert policy.should_abstain(low_feats)
    assert not policy.should_abstain(high_feats)


def test_policy_save_and_load_round_trip(tmp_path):
    import numpy as np

    weights = train_logistic(np.array([[1.0], [0.0]]), np.array([1.0, 0.0]), epochs=10)
    policy = AbstentionPolicy(weights=weights, threshold=0.42)
    path = tmp_path / "abstain.json"
    policy.save(path)
    loaded = AbstentionPolicy.load(path, threshold=0.42)
    assert loaded.weights == weights
    assert loaded.threshold == 0.42


def test_policy_save_without_weights_raises(tmp_path):
    policy = AbstentionPolicy(weights=None)
    try:
        policy.save(tmp_path / "x.json")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --------------------------------------------------------------------------- #
# answer_with_policy
# --------------------------------------------------------------------------- #
def test_answer_with_policy_abstains_without_calling_answer_fn():
    hy = _retriever([])  # empty store -> zero evidence, must abstain
    calls = []

    def answer_fn():
        calls.append(1)
        return "should not be called"

    import numpy as np

    weights = train_logistic(np.array([[1.0, 1.0, 1.0, 1.0], [0.0, 0.0, 0.0, 0.0]]), np.array([1.0, 0.0]), epochs=500)
    policy = AbstentionPolicy(weights=weights, threshold=0.5)
    result = answer_with_policy(hy, "q", answer_fn, policy)
    assert result["abstained"] is True
    assert result["answer"] == ABSTAIN_MESSAGE
    assert calls == []


def test_answer_with_policy_answers_when_sufficient():
    hy = _retriever([_ep("Alice: I love hiking near the lake.", 1, "a")])
    policy = AbstentionPolicy(weights=None)  # always answers
    result = answer_with_policy(hy, "What does Alice like?", lambda: "hiking", policy)
    assert result["abstained"] is False
    assert result["answer"] == "hiking"
