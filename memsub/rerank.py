"""Opt-in local LLM rerank of a retrieval shortlist (extensions.md candidate 1),
plus (2026-07-20) a tiny local learned reranker and the generic logistic
trainer it and `memsub/answer_policy.py` both share.

Takes the hybrid retriever's top-K candidates for a query, batches them into
one LLM call asking for a relevance-ordered permutation, and returns that
order. Falls back to the original (hybrid) order on any backend failure or
unparseable response, so this mode can only change ranking, never drop a
candidate or crash the caller. Every call is logged (query, n, backend,
model, status, resulting order) for the audit trail this project treats as
its real edge; callers pass a shared list to `log` to collect the trail.

The 2026-07-20 addition is a different mechanism entirely: a small
cross-features logistic-regression model (pure numpy, no sklearn -- not a
project dependency, see pyproject.toml) trained offline on candidate-level
features (BM25, dense cosine, RRF-derived rank, entity overlap, recency,
temporal-window match, graph-boost -- built by
`HybridRetriever.candidate_features()` in `memsub/hybrid.py`) to predict
whether a retrieved item is gold-supporting. `train_logistic`/
`apply_logistic` are intentionally generic (operate on a raw feature vector,
no hardcoded names/count) so `memsub/answer_policy.py`'s abstention score
(a different, 4-feature model) reuses the exact same trainer rather than a
second copy-pasted implementation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

RERANK_SYSTEM = (
    "You rank retrieved memory snippets by relevance to a query. You will "
    "see a QUERY and a numbered list of CANDIDATES (one per line). Return "
    "ONLY a JSON array containing EVERY candidate number exactly once, "
    "ordered most relevant to the query first. Do not omit any number, even "
    "ones that seem irrelevant: put those last. No other text, no markdown "
    "fences, no explanation."
)

_ARRAY_RE = re.compile(r"\[[^\[\]]*\]", re.DOTALL)


def build_rerank_prompt(query: str, contents: list[str]) -> str:
    lines = [f"QUERY: {query}", "", "CANDIDATES:"]
    for i, c in enumerate(contents):
        lines.append(f"{i}: {c}")
    return "\n".join(lines)


def _parse_order(raw: str, n: int) -> tuple[list[int], bool] | None:
    """Parse a JSON array of candidate indices.

    A local 8B model frequently returns only the candidates it judges most
    relevant rather than a full permutation (observed in practice: asked to
    rank 25, qwen3:8b sometimes returns 3). Any indices it does return are
    trusted as "most relevant first"; whatever it omits is appended after,
    in original order, so no candidate is ever dropped. Returns None only
    when nothing usable was parsed at all (full fallback to identity order).
    Second tuple element is True when the model returned a full permutation
    (a "clean" rerank) vs a partial list that had to be completed.
    """
    m = _ARRAY_RE.search(raw)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    seen: list[int] = []
    for x in parsed:
        try:
            xi = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= xi < n and xi not in seen:
            seen.append(xi)
    if not seen:
        return None  # nothing usable
    complete = len(seen) == n
    if not complete:
        seen.extend(i for i in range(n) if i not in seen)
    return seen, complete


def llm_rerank(
    query: str,
    contents: list[str],
    backend,
    log: list[dict] | None = None,
) -> list[int]:
    """Return a permutation of range(len(contents)), most relevant first.

    On any backend error or an unparseable/incomplete response, returns the
    identity order (i.e. the caller's original order is unchanged) and logs
    why, rather than raising or silently dropping a candidate.
    """
    n = len(contents)
    identity = list(range(n))
    if n == 0:
        return identity
    entry: dict = {
        "query": query,
        "n": n,
        "backend": getattr(backend, "name", "?"),
        "model": getattr(backend, "model", "?"),
    }
    try:
        prompt = build_rerank_prompt(query, contents)
        raw = backend.complete(RERANK_SYSTEM, [{"role": "user", "content": prompt}])
        parsed = _parse_order(raw, n)
        if parsed is None:
            entry["status"] = "fallback_unparseable"
            order = identity
        else:
            order, complete = parsed
            entry["status"] = "reranked" if complete else "reranked_partial"
    except Exception as exc:  # noqa: BLE001 - any backend failure must degrade, not crash
        entry["status"] = "fallback_error"
        entry["error"] = str(exc)[:200]
        order = identity
    entry["order"] = order
    if log is not None:
        log.append(entry)
    return order


# --------------------------------------------------------------------------- #
# Generic logistic-regression trainer (2026-07-20), shared by the learned
# reranker below and memsub/answer_policy.py's abstention score.
# --------------------------------------------------------------------------- #
def train_logistic(
    X: np.ndarray,
    y: np.ndarray,
    lr: float = 0.5,
    epochs: int = 500,
    l2: float = 1e-3,
) -> dict:
    """Fit a small standardized logistic regression, pure numpy, no
    dependency beyond the project's existing numpy requirement.

    X: (n_samples, n_features) raw feature matrix. y: (n_samples,) in {0, 1}.
    Standardizes features (mean/std baked into the returned weights so
    `apply_logistic` never needs the training data again) and trains with
    plain batch gradient descent plus L2 regularization -- "local, seconds"
    at the scale this project's training sets run at (hundreds to low
    thousands of rows). Returns a JSON-serializable dict: {"mean": [...],
    "std": [...], "w": [...], "b": float, "n_train": int}.
    """
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n, d = X.shape
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std = np.where(std == 0, 1.0, std)
    Z = (X - mean) / std
    w = np.zeros(d)
    b = 0.0
    for _ in range(epochs):
        logits = Z @ w + b
        p = 1.0 / (1.0 + np.exp(-logits))
        grad_w = Z.T @ (p - y) / n + l2 * w
        grad_b = float(np.mean(p - y))
        w -= lr * grad_w
        b -= lr * grad_b
    return {"mean": mean.tolist(), "std": std.tolist(), "w": w.tolist(), "b": float(b), "n_train": n}


def apply_logistic(weights: dict, x: list[float]) -> float:
    """Sigmoid probability for one raw feature vector `x`, same order/length
    used at training time. Never raises on a length mismatch -- the caller's
    own tests catch that; at inference time a shape error is a programming
    bug, not a runtime degrade case, so it is allowed to raise via numpy."""
    xv = np.asarray(x, dtype=np.float64)
    mean = np.asarray(weights["mean"], dtype=np.float64)
    std = np.asarray(weights["std"], dtype=np.float64)
    z = (xv - mean) / std
    logit = float(np.dot(weights["w"], z) + weights["b"])
    return 1.0 / (1.0 + np.exp(-logit))


def save_weights(weights: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(weights, indent=2))


def load_weights(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


# --------------------------------------------------------------------------- #
# Tiny learned reranker: candidate-level features -> P(gold-supporting).
# --------------------------------------------------------------------------- #
RERANK_FEATURE_NAMES = (
    "bm25_norm",
    "dense_norm",
    "rrf_recip",
    "entity_overlap",
    "recency",
    "temporal_match",
    "graph_boost",
)


def rerank_feature_vector(feat: dict) -> list[float]:
    """Pull `RERANK_FEATURE_NAMES` out of a candidate-feature dict (the shape
    `HybridRetriever.candidate_features` returns) in a fixed order."""
    return [feat[name] for name in RERANK_FEATURE_NAMES]


def score_candidate(weights: dict, feat: dict) -> float:
    """P(gold-supporting) for one candidate-feature dict."""
    return apply_logistic(weights, rerank_feature_vector(feat))


def rank_by_model(weights: dict, candidates: list[dict]) -> list[int]:
    """Re-order a list of candidate-feature dicts (each carrying an
    "index" key back into the caller's episode list) by descending
    P(gold-supporting). Ties broken by the candidate's original order
    (stable sort), matching every other ranking function in this repo."""
    scored = sorted(
        range(len(candidates)),
        key=lambda pos: -score_candidate(weights, candidates[pos]),
    )
    return [candidates[pos]["index"] for pos in scored]
