"""Opt-in abstention/answer policy (2026-07-20): calibrated evidence-sufficiency
gate, reframing "I don't know" as a feature rather than a judge-penalized
refusal (docs/research-salvage-2026-07-20.md, MemGuard's abstention
calibration; docs/locomo-frontier-v2-2026-07-18.md's refusal findings: a
best-effort prompt fixes over-refusal, but calibrated abstention is the
proper answer for the genuinely-unanswerable remainder).

Two pieces:

  evidence_features()   a cheap, query/retrieval-only signal (never sees the
                         gold answer): top-1 RRF-derived score, the margin to
                         the #2 candidate, how much of the query's entities
                         are covered anywhere in the top-k retrieved text,
                         and how consistently individual top-k items agree
                         (each touches the query's entities) -- built on top
                         of `HybridRetriever.candidate_features()`
                         (memsub/hybrid.py), so no new retrieval-side
                         computation is duplicated here.
  AbstentionPolicy       a tiny logistic model (memsub.rerank.train_logistic/
                         apply_logistic, same generic trainer the learned
                         reranker uses) mapping those 4 features to
                         P(answerable-from-retrieved), calibrated against a
                         dev-slice threshold (scripts/calibrate_abstention.py)
                         to satisfy this task's pre-registered abstention
                         target (WORKLOG.md 2026-07-20).

Wiring is opt-in only: `answer_with_policy()` is a thin wrapper an eval
script (or a real caller) chooses to call; nothing in memsub's default
answer path changes unless a caller does so explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path

from .hybrid import HybridRetriever
from .rerank import apply_logistic
from .sleep import _extract_entities
from .store import EpisodicStore

ABSTAIN_MESSAGE = "Not enough stored context to answer this confidently."

# Calibrated on the dev-slice run, scripts/calibrate_abstention.py ->
# results/abstain-calibration-2026-07-20.jsonl; see docs/rerank-abstain-2026-07-20.md
# for the sweep table this value was picked from. Overridable per-call.
DEFAULT_THRESHOLD = 0.5

FEATURE_NAMES = ("top1_rrf", "margin", "entity_coverage", "agreement")


def evidence_features(
    retriever: HybridRetriever, query: str, store: EpisodicStore | None = None, k: int = 6,
) -> dict[str, float]:
    """Aggregate, gold-blind evidence-sufficiency features over the top-k of
    `retriever.rank(query)`. Pure function of (retriever state, query, k) --
    never touches a gold answer, so it is safe to compute at real query time,
    not just during calibration."""
    candidates = retriever.candidate_features(query, store=store, pool_size=max(k, 10))
    topk = candidates[:k]
    if not topk:
        return {"top1_rrf": 0.0, "margin": 0.0, "entity_coverage": 0.0, "agreement": 0.0}

    top1_rrf = topk[0]["rrf_recip"]
    margin = (topk[0]["rrf_recip"] - topk[1]["rrf_recip"]) if len(topk) > 1 else top1_rrf

    if not _extract_entities(query):
        # entity-free query (candidate_features' entity_overlap is 0.0 for
        # ALL candidates in this case, by construction, which would read as
        # "zero coverage" -- neither rewarded nor punished by this signal,
        # so both entity features get a neutral 0.5 instead.
        entity_coverage = 0.5
        agreement = 0.5
    else:
        # query-side: how much of the query's own entities show up ANYWHERE
        # in the top-k retrieved text.
        entity_overlaps = [c["entity_overlap"] for c in topk]
        entity_coverage = max(entity_overlaps)
        # item-side: how consistently do INDIVIDUAL top-k items touch those
        # entities (a lone hit vs corroborating agreement across sources).
        agreement = sum(entity_overlaps) / len(entity_overlaps)

    return {
        "top1_rrf": top1_rrf,
        "margin": margin,
        "entity_coverage": entity_coverage,
        "agreement": agreement,
    }


def evidence_vector(feats: dict[str, float]) -> list[float]:
    return [feats[name] for name in FEATURE_NAMES]


class AbstentionPolicy:
    """A calibrated evidence-sufficiency gate.

    weights: the dict `memsub.rerank.train_logistic` produces over
    `FEATURE_NAMES`-ordered feature vectors (None -> the policy always
    answers, i.e. a documented no-op, not a crash -- lets callers construct
    an "always answer" policy for comparison runs without a special case).
    threshold: P(answerable) below which the policy abstains.
    """

    def __init__(self, weights: dict | None = None, threshold: float = DEFAULT_THRESHOLD) -> None:
        self.weights = weights
        self.threshold = threshold

    def score(self, feats: dict[str, float]) -> float:
        if self.weights is None:
            return 1.0
        return apply_logistic(self.weights, evidence_vector(feats))

    def should_abstain(self, feats: dict[str, float]) -> bool:
        return self.score(feats) < self.threshold

    @classmethod
    def load(cls, path: str | Path, threshold: float = DEFAULT_THRESHOLD) -> "AbstentionPolicy":
        return cls(weights=json.loads(Path(path).read_text()), threshold=threshold)

    def save(self, path: str | Path) -> None:
        if self.weights is None:
            raise ValueError("cannot save a policy with no trained weights")
        Path(path).write_text(json.dumps(self.weights, indent=2))


def answer_with_policy(
    retriever: HybridRetriever,
    query: str,
    answer_fn,
    policy: AbstentionPolicy,
    store: EpisodicStore | None = None,
    k: int = 6,
) -> dict:
    """Compute evidence features, abstain (no `answer_fn` call at all) below
    the policy's threshold, otherwise call `answer_fn()` (a zero-arg
    callable so the actual LLM call is only ever made when the policy
    decides to answer) and return its result unchanged.

    Returns {"answer": str, "abstained": bool, "score": float,
    "features": dict}.
    """
    feats = evidence_features(retriever, query, store=store, k=k)
    score = policy.score(feats)
    if score < policy.threshold:
        return {"answer": ABSTAIN_MESSAGE, "abstained": True, "score": score, "features": feats}
    answer = answer_fn()
    return {"answer": answer, "abstained": False, "score": score, "features": feats}
