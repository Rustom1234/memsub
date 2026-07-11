"""Opt-in local LLM rerank of a retrieval shortlist (extensions.md candidate 1).

Takes the hybrid retriever's top-K candidates for a query, batches them into
one LLM call asking for a relevance-ordered permutation, and returns that
order. Falls back to the original (hybrid) order on any backend failure or
unparseable response, so this mode can only change ranking, never drop a
candidate or crash the caller. Every call is logged (query, n, backend,
model, status, resulting order) for the audit trail this project treats as
its real edge; callers pass a shared list to `log` to collect the trail.
"""

from __future__ import annotations

import json
import re

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
