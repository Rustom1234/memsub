"""Bi-temporal support: date-expression resolution and query-time temporal intent.

Design sources: docs/research-salvage-2026-07-20.md (TSM semantic timelines +
temporal intent at retrieval, REMem time-qualified facts, Zep bi-temporal
edges) and docs/locomo-frontier-v2-2026-07-18.md (the LOCOMO temporal
category: 30% without a date flag, 45% with one surfaced, on n=20 -- this
module is the next step past surfacing: resolving relative dates into an
absolute valid window and using it to boost retrieval, not just display).

Two independent, cheap passes, both rule-based first, an LLM call only when
rules cannot resolve something:

  1. Write-time date resolution (annotate_episode(s)): finds a date
     expression in an episode's content, resolves it against a reference
     date (the episode's own session_date when known, else its ts), and
     returns a new Episode with valid_from set. Absolute expressions
     ("8 May 2023", "March 2023") and a fixed relative grammar (yesterday/
     tomorrow/today, N days/weeks/months ago, in N days/weeks/months,
     next/last week/month/year, last/next <Month>) are resolved by pure
     Python arithmetic, zero cost. A relative expression that IS detected
     (matches a relative-marker word) but does not fit the fixed grammar
     falls back to one qwen3:8b call, cached per (text, reference).

  2. Query-time intent classification (classify_intent): point_in_time |
     range | ordinal | current_state | atemporal, decided by keyword rules;
     one qwen3:8b call only when a temporal marker is present but the rules
     cannot decide the sub-type.

Nothing here runs unless a caller explicitly invokes it -- the default
episode/query paths (memsub.store.Episode, memsub.hybrid.HybridRetriever.
rank/search) never import or touch this module's write path.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .backends import LLMBackend
from .store import Episode

_MONTHS = "january february march april may june july august september october november december".split()
_MONTH_RE = "|".join(m.capitalize() for m in _MONTHS)
_MONTH_INDEX = {m: i + 1 for i, m in enumerate(_MONTHS)}

_WORD_NUM = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _num(token: str) -> int:
    return _WORD_NUM.get(token.lower(), 0) or int(token)


# --------------------------------------------------------------------------- #
# Absolute date parsing
# --------------------------------------------------------------------------- #
_ABS_RE = re.compile(rf"(?:(\d{{1,2}})\s+)?({_MONTH_RE})\s*,?\s+(\d{{4}})", re.IGNORECASE)


def parse_absolute_date(text: str) -> date | None:
    """'8 May 2023' or 'May 2023' -> a date (day defaults to 1 when absent).
    None when no absolute (year-bearing) date expression is present."""
    m = _ABS_RE.search(text)
    if not m:
        return None
    day = int(m.group(1)) if m.group(1) else 1
    month = _MONTH_INDEX[m.group(2).lower()]
    year = int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Relative date resolution (pure rules, dateutil-style, stdlib only)
# --------------------------------------------------------------------------- #
_YESTERDAY_RE = re.compile(r"\byesterday\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)
_TODAY_RE = re.compile(r"\b(today|this day)\b", re.IGNORECASE)
_NUM_WORD = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)"
_AGO_RE = re.compile(rf"\b{_NUM_WORD}\s+(day|week|month|year)s?\s+ago\b", re.IGNORECASE)
_IN_N_RE = re.compile(rf"\bin\s+{_NUM_WORD}\s+(day|week|month|year)s?\b", re.IGNORECASE)
_NEXT_LAST_UNIT_RE = re.compile(r"\b(next|last)\s+(day|week|month|year)\b", re.IGNORECASE)
_NEXT_LAST_MONTH_RE = re.compile(rf"\b(next|last)\s+({_MONTH_RE})\b", re.IGNORECASE)

# Any word that signals "this text contains a relative-date expression",
# used to decide whether an unresolvable phrase deserves the one LLM
# escalation call (rather than being silently treated as no expression).
_RELATIVE_MARKER_RE = re.compile(
    r"\b(yesterday|tomorrow|today|ago|next|last|since|until|recently|"
    r"a while (?:back|ago))\b",
    re.IGNORECASE,
)


def _add_months(d: date, n: int) -> date:
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _shift(ref: date, n: int, unit: str) -> date:
    if unit == "day":
        return ref + timedelta(days=n)
    if unit == "week":
        return ref + timedelta(weeks=n)
    if unit == "month":
        return _add_months(ref, n)
    return _add_months(ref, n * 12)  # year


def resolve_relative(text: str, ref: date) -> date | None:
    """Resolve a fixed relative-date grammar against `ref`. None when no
    known pattern matches (the caller decides whether to escalate)."""
    if _YESTERDAY_RE.search(text):
        return ref - timedelta(days=1)
    if _TOMORROW_RE.search(text):
        return ref + timedelta(days=1)
    if _TODAY_RE.search(text):
        return ref
    m = _AGO_RE.search(text)
    if m:
        return _shift(ref, -_num(m.group(1)), m.group(2).lower())
    m = _IN_N_RE.search(text)
    if m:
        return _shift(ref, _num(m.group(1)), m.group(2).lower())
    m = _NEXT_LAST_UNIT_RE.search(text)
    if m:
        n = 1 if m.group(1).lower() == "next" else -1
        return _shift(ref, n, m.group(2).lower())
    m = _NEXT_LAST_MONTH_RE.search(text)
    if m:
        direction, month_name = m.group(1).lower(), m.group(2).lower()
        target_month = _MONTH_INDEX[month_name]
        if direction == "last":
            year = ref.year if target_month < ref.month else ref.year - 1
        else:
            year = ref.year if target_month > ref.month else ref.year + 1
        day = min(ref.day, calendar.monthrange(year, target_month)[1])
        return date(year, target_month, day)
    return None


RESOLVE_SYSTEM = (
    "Resolve a relative date expression to an absolute calendar date, given "
    "a reference date. Output ONLY a JSON object: {\"date\": \"YYYY-MM-DD\"} "
    "or {\"date\": null} if it cannot be determined."
)
_RESOLVE_JSON_RE = re.compile(r'"date"\s*:\s*"(\d{4}-\d{2}-\d{2})"')


def _llm_resolve(text: str, ref: date, backend: LLMBackend) -> date | None:
    prompt = f"REFERENCE DATE: {ref.isoformat()}\nTEXT: {text}"
    try:
        response = backend.complete(RESOLVE_SYSTEM, [{"role": "user", "content": prompt}])
    except Exception:
        return None
    m = _RESOLVE_JSON_RE.search(response)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


@dataclass
class TemporalResolver:
    """Resolves a date expression found in text against a reference date.

    Rule-based first (zero cost, see parse_absolute_date/resolve_relative);
    an LLM call only for a detected-but-unresolvable relative expression,
    cached per (text, reference) pair for this resolver's lifetime (mirrors
    memsub.graph.EntityResolver's staged-funnel pattern)."""

    backend: LLMBackend | None = None
    _cache: dict[tuple[str, str], date | None] = field(default_factory=dict)
    llm_calls: int = 0

    def resolve(self, text: str, ref: date) -> date | None:
        abs_date = parse_absolute_date(text)
        if abs_date is not None:
            return abs_date
        rel_date = resolve_relative(text, ref)
        if rel_date is not None:
            return rel_date
        if not _RELATIVE_MARKER_RE.search(text):
            return None  # no temporal expression detected at all
        if self.backend is None:
            return None  # never guess without a backend
        key = (text.strip().lower(), ref.isoformat())
        if key in self._cache:
            return self._cache[key]
        resolved = _llm_resolve(text, ref, self.backend)
        self.llm_calls += 1
        self._cache[key] = resolved
        return resolved


# --------------------------------------------------------------------------- #
# Write-time annotation (opt-in: caller invokes before store.append())
# --------------------------------------------------------------------------- #
def episode_reference_date(ep: Episode) -> date:
    """The 'world time' `ep` was written relative to: its own session_date
    when known (LOCOMO/dated sources), else its ts."""
    session_date = (ep.provenance or {}).get("session_date", "")
    if session_date:
        parsed = parse_absolute_date(session_date)
        if parsed is not None:
            return parsed
    return datetime.fromtimestamp(ep.ts).date() if ep.ts else datetime.now().date()


def annotate_episode(ep: Episode, resolver: TemporalResolver) -> Episode:
    """Opt-in: returns a new Episode with valid_from set when a date
    expression is found and resolved in ep.content; returns `ep` unchanged
    (same object) when nothing resolves or valid_from is already set, so a
    caller that never invokes this sees no difference at all."""
    if ep.valid_from is not None:
        return ep
    ref = episode_reference_date(ep)
    resolved = resolver.resolve(ep.content, ref)
    if resolved is None:
        return ep
    valid_from = datetime.combine(resolved, datetime.min.time()).timestamp()
    return Episode(
        content=ep.content, run_id=ep.run_id, session_id=ep.session_id, kind=ep.kind,
        importance=ep.importance, provenance=ep.provenance, namespace=ep.namespace,
        ts=ep.ts, id=ep.id, valid_from=valid_from, valid_to=ep.valid_to,
    )


def annotate_episodes(episodes: list[Episode], backend: LLMBackend | None = None) -> list[Episode]:
    """Batch form of annotate_episode, one shared TemporalResolver (and
    cache) across the whole list."""
    resolver = TemporalResolver(backend=backend)
    return [annotate_episode(ep, resolver) for ep in episodes]


# --------------------------------------------------------------------------- #
# Query-time temporal intent classification
# --------------------------------------------------------------------------- #
INTENTS = ("point_in_time", "range", "ordinal", "current_state", "atemporal")

_ORDINAL_RE = re.compile(
    r"\b(earliest|initially|most recent|latest|finally)\b|"
    r"\b(last|first)\b(?!\s+(?:" + _MONTH_RE + r"|day|week|month|year)s?\b)",
    re.IGNORECASE,
)
_CURRENT_RE = re.compile(r"\b(still|currently|these days|at present)\b", re.IGNORECASE)
_RANGE_MARKER_RE = re.compile(r"\b(between|since|until|before|after)\b", re.IGNORECASE)
# Bare "when" (LOCOMO's dominant temporal-question phrasing, "When did X
# happen?") is unambiguously point_in_time-shaped once ordinal/range/current
# have already had first crack at it above -- resolved by rule, not the LLM
# escalation below, both for frugality and because it is genuinely not
# ambiguous (dev-slice iteration, docs/temporal-v1-2026-07-20.md: routing
# bare "when" to the LLM occasionally misclassified it as ordinal, which
# always reorders retrieval by raw timestamp and corrupted relevance for
# plain point-in-time questions with no ordinal signal at all).
_WHEN_RE = re.compile(r"\bwhen\b", re.IGNORECASE)
_ANY_TEMPORAL_RE = re.compile(
    r"\b(when|before|after|last|first|still|yesterday|tomorrow|today|ago|since|"
    r"until|recently|currently|now|earliest|latest|initially|finally|" + _MONTH_RE + r")\b",
    re.IGNORECASE,
)

CLASSIFY_SYSTEM = (
    "Classify the temporal intent of a QUESTION into exactly one of: "
    "point_in_time (asks about a specific moment/date), range (asks about a "
    "span or a before/after boundary), ordinal (asks about the first or most "
    "recent occurrence of something), current_state (asks what is true now "
    "or still true), atemporal (no meaningful time dimension). "
    "Output ONLY a JSON object: {\"intent\": \"<one of the five>\"}"
)
_CLASSIFY_JSON_RE = re.compile(r'"intent"\s*:\s*"(\w+)"')


def _llm_classify(query: str, backend: LLMBackend) -> str:
    try:
        response = backend.complete(CLASSIFY_SYSTEM, [{"role": "user", "content": query}])
    except Exception:
        return "point_in_time"
    m = _CLASSIFY_JSON_RE.search(response)
    if m and m.group(1) in INTENTS:
        return m.group(1)
    return "point_in_time"


def classify_intent(query: str, backend: LLMBackend | None = None) -> str:
    """Rule-based first; one LLM call only when a temporal marker word is
    present but the rules cannot decide the sub-type (never called for a
    query with zero temporal markers -- those are atemporal at zero cost)."""
    has_date = parse_absolute_date(query) is not None or resolve_relative(query, date.today()) is not None
    has_range_marker = bool(_RANGE_MARKER_RE.search(query))
    if has_date:
        return "range" if has_range_marker else "point_in_time"
    if has_range_marker:
        return "range"
    if _ORDINAL_RE.search(query):
        return "ordinal"
    if _CURRENT_RE.search(query):
        return "current_state"
    if _WHEN_RE.search(query):
        return "point_in_time"
    if not _ANY_TEMPORAL_RE.search(query):
        return "atemporal"
    return _llm_classify(query, backend) if backend is not None else "point_in_time"


def ordinal_direction(query: str) -> str:
    """'earliest' or 'latest' -- which end of time an ordinal query wants."""
    if re.search(r"\b(earliest|first|initially)\b", query, re.IGNORECASE):
        return "earliest"
    return "latest"


# --------------------------------------------------------------------------- #
# Retrieval-side matching (memsub.hybrid.HybridRetriever.rank_temporal)
# --------------------------------------------------------------------------- #
def episode_matches_date(ep: Episode, target: date) -> bool:
    """True when `target` falls inside ep's [valid_from, valid_to) window,
    or (when ep carries no valid_from at all) ep's session_date equals
    `target` exactly -- the fallback for episodes never run through
    annotate_episode/s, e.g. every LOCOMO turn by default."""
    if ep.valid_from is not None:
        day_start = datetime.combine(target, datetime.min.time()).timestamp()
        day_end = datetime.combine(target, datetime.max.time()).timestamp()
        hi = ep.valid_to if ep.valid_to is not None else float("inf")
        return ep.valid_from <= day_end and day_start <= hi
    session_date = (ep.provenance or {}).get("session_date", "")
    if session_date:
        return parse_absolute_date(session_date) == target
    return False
