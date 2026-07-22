"""Temporal parser + SDK date-window filter (eng-review I1)."""

import datetime

from memsub.sdk import MemoryClient
from memsub.store import Episode, EpisodicStore
from memsub.timeparse import parse, range_ts

NOW = datetime.date(2026, 7, 21)  # a Tuesday, frozen


def _r(q):
    return parse(q, now=NOW)


def test_yesterday_and_today():
    assert _r("what did I do yesterday").start == datetime.date(2026, 7, 20)
    assert _r("today's plan").start == NOW
    assert _r("what did I do this morning").start == NOW
    assert _r("last night's run").start == datetime.date(2026, 7, 20)


def test_days_ago_and_weekday():
    assert _r("what was I doing two days ago").start == datetime.date(2026, 7, 19)
    assert _r("3 days ago").start == datetime.date(2026, 7, 18)
    # last Monday from Tuesday 07-21 is 07-20; last Tuesday is a full week back
    assert _r("what happened last monday").start == datetime.date(2026, 7, 20)
    assert _r("what happened last tuesday").start == datetime.date(2026, 7, 14)


def test_weeks_months_and_absolute():
    lw = _r("last week")
    assert (lw.start, lw.end) == (datetime.date(2026, 7, 13), datetime.date(2026, 7, 19))
    tw = _r("this week")
    assert (tw.start, tw.end) == (datetime.date(2026, 7, 20), NOW)
    lm = _r("last month")
    assert (lm.start, lm.end) == (datetime.date(2026, 6, 1), datetime.date(2026, 6, 30))
    assert _r("on July 20").start == datetime.date(2026, 7, 20)
    assert _r("july 20th").start == datetime.date(2026, 7, 20)
    # future month/day resolves to last year
    assert _r("on December 25").start == datetime.date(2025, 12, 25)
    assert _r("2026-07-14").start == datetime.date(2026, 7, 14)


def test_cleaned_query_strips_phrase():
    tr = _r("what did I work on yesterday")
    assert tr.cleaned("what did I work on yesterday") == "what did I work on"
    assert _r("no time phrase here about work") is None


def test_sdk_hard_window_filter(tmp_path):
    store = EpisodicStore(tmp_path / "t.db")
    day = lambda d: datetime.datetime(2026, 7, d, 12, 0).timestamp()
    for d, text in [(14, "tuned the limit watcher"), (20, "ran the deep research sweep"),
                    (21, "swapped the corrupted database")]:
        store.append(Episode(content=text, run_id="r", session_id=f"s{d}",
                             ts=day(d), id=f"ep{d}").with_defaults())
    store.close()
    c = MemoryClient(db_path=str(tmp_path / "t.db"), embedder="hashing")
    hits = c.search("what did I do", date_from="2026-07-20", date_to="2026-07-20", k=5)
    assert [h.episode.id for h in hits] == ["ep20"]
    # auto_time picks the phrase up from the query (frozen via explicit dates
    # is the deterministic path; auto path just must not widen the window)
    hits = c.search("database work", date_from="2026-07-21", k=5)
    assert [h.episode.id for h in hits] == ["ep21"]
    # empty window is an honest empty answer
    assert c.search("anything", date_from="2026-07-01", date_to="2026-07-01", k=5) == []
    c.close()
