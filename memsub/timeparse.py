"""Deterministic relative/absolute date parsing for temporal queries.

The I1 temporal query layer (docs/eng-review-2026-07-21.md): "what did I do
yesterday" must become a date-range filter, not a lexical token. No LLM in
the parse path; unrecognized phrasings return None and search proceeds
untouched. All resolution is against an explicit `now` so tests freeze the
clock.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


@dataclass(frozen=True)
class TimeRange:
    start: datetime.date  # inclusive
    end: datetime.date    # inclusive
    matched: str          # the phrase consumed from the query

    def cleaned(self, query: str) -> str:
        """The query with the matched time phrase removed (so the phrase
        does not pollute lexical matching)."""
        out = query.replace(self.matched, " ")
        return re.sub(r"\s+", " ", out).strip(" ?.,")


_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(the day before yesterday)\b", re.I), "day_before_yesterday"),
    (re.compile(r"\b(yesterday|last night)\b", re.I), "yesterday"),
    (re.compile(r"\b(today|this morning|this afternoon|this evening|tonight)\b", re.I), "today"),
    (re.compile(r"\b(\d+)\s+days?\s+ago\b", re.I), "n_days_ago"),
    (re.compile(r"\b(two|three|four|five|six)\s+days\s+ago\b", re.I), "word_days_ago"),
    (re.compile(r"\blast\s+week\b", re.I), "last_week"),
    (re.compile(r"\bthis\s+week\b", re.I), "this_week"),
    (re.compile(r"\blast\s+month\b", re.I), "last_month"),
    (re.compile(r"\bthis\s+month\b", re.I), "this_month"),
    (re.compile(r"\blast\s+(" + "|".join(_WEEKDAYS) + r")\b", re.I), "last_weekday"),
    (re.compile(r"\b(?:on\s+)?(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I), "month_day"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso_date"),
]

_WORD_NUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def parse(query: str, now: datetime.date | None = None) -> TimeRange | None:
    """Return the first recognized time range in `query`, or None."""
    today = now or datetime.date.today()
    for pattern, kind in _PATTERNS:
        m = pattern.search(query)
        if not m:
            continue
        matched = m.group(0)
        if kind == "day_before_yesterday":
            d = today - datetime.timedelta(days=2)
            return TimeRange(d, d, matched)
        if kind == "yesterday":
            d = today - datetime.timedelta(days=1)
            return TimeRange(d, d, matched)
        if kind == "today":
            return TimeRange(today, today, matched)
        if kind == "n_days_ago":
            d = today - datetime.timedelta(days=int(m.group(1)))
            return TimeRange(d, d, matched)
        if kind == "word_days_ago":
            d = today - datetime.timedelta(days=_WORD_NUM[m.group(1).lower()])
            return TimeRange(d, d, matched)
        if kind == "last_week":
            start = today - datetime.timedelta(days=today.weekday() + 7)
            return TimeRange(start, start + datetime.timedelta(days=6), matched)
        if kind == "this_week":
            start = today - datetime.timedelta(days=today.weekday())
            return TimeRange(start, today, matched)
        if kind == "last_month":
            first_this = today.replace(day=1)
            last_prev = first_this - datetime.timedelta(days=1)
            return TimeRange(last_prev.replace(day=1), last_prev, matched)
        if kind == "this_month":
            return TimeRange(today.replace(day=1), today, matched)
        if kind == "last_weekday":
            target = _WEEKDAYS.index(m.group(1).lower())
            delta = (today.weekday() - target) % 7 or 7
            d = today - datetime.timedelta(days=delta)
            return TimeRange(d, d, matched)
        if kind == "month_day":
            month = _MONTHS.index(m.group(1).lower()) + 1
            day = int(m.group(2))
            try:
                d = datetime.date(today.year, month, day)
            except ValueError:
                return None
            if d > today:  # "on July 20" said in January means last year's
                d = d.replace(year=today.year - 1)
            return TimeRange(d, d, matched)
        if kind == "iso_date":
            try:
                d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
            return TimeRange(d, d, matched)
    return None


def range_ts(tr: TimeRange) -> tuple[float, float]:
    """Local-midnight epoch bounds [start, end) for filtering Episode.ts."""
    start = datetime.datetime.combine(tr.start, datetime.time.min).timestamp()
    end = datetime.datetime.combine(
        tr.end + datetime.timedelta(days=1), datetime.time.min
    ).timestamp()
    return start, end
