"""F2: morning brief. Written first per superpowers TDD.

The brief is prospective reflection in product form (RMM lineage, see
docs/research-memory-landscape-2026-07-21.md): a story-time digest of one
day: what happened, what was decided, and open loops, with every line
traceable to episode ids. LLM only writes prose over facts it is handed;
with no backend it degrades to a deterministic outline (never fabricates).
"""

import datetime

import pytest

from memsub.brief import build_brief, render_brief_markdown
from memsub.store import Episode, EpisodicStore


class FakeBackend:
    name = "fake"
    model = "fake"

    def complete(self, system, messages):
        return "A day of rate changes and shipping."


def _day_ts(d, hour=10):
    return datetime.datetime(2026, 7, d, hour).timestamp()


@pytest.fixture()
def store(tmp_path):
    s = EpisodicStore(tmp_path / "b.db")
    rows = [
        ("e1", "decision", "decided to switch the interface to a web dashboard", 20, 9),
        ("e2", "fact", "shipped the approval queue screen", 20, 11),
        ("e3", "idea", "maybe add an objection log later", 20, 15),
        ("e4", "fact", "unrelated: earlier day fact", 19, 12),
    ]
    for eid, kind, content, day, hour in rows:
        s.append(Episode(content=content, run_id="r", session_id="s", kind=kind,
                         id=eid, ts=_day_ts(day, hour)).with_defaults())
    return s


def test_build_brief_scopes_to_story_day(store):
    brief = build_brief(store, datetime.date(2026, 7, 20))
    ids = {e.id for sec in brief.sections.values() for e in sec}
    assert ids == {"e1", "e2", "e3"}


def test_build_brief_sections_by_kind(store):
    brief = build_brief(store, datetime.date(2026, 7, 20))
    assert [e.id for e in brief.sections["decisions"]] == ["e1"]
    assert [e.id for e in brief.sections["ideas_open_loops"]] == ["e3"]
    assert [e.id for e in brief.sections["events"]] == ["e2"]


def test_render_includes_receipts_and_summary(store):
    brief = build_brief(store, datetime.date(2026, 7, 20), backend=FakeBackend())
    md = render_brief_markdown(brief)
    assert "A day of rate changes and shipping." in md
    assert "e1" in md and "e3" in md  # episode-id receipts
    assert "2026-07-20" in md


def test_no_backend_degrades_deterministically(store):
    brief = build_brief(store, datetime.date(2026, 7, 20), backend=None)
    md = render_brief_markdown(brief)
    assert "decided to switch" in md  # raw facts still present
    assert brief.summary == ""  # no fabricated prose


def test_empty_day_is_honest(store):
    brief = build_brief(store, datetime.date(2026, 7, 1))
    md = render_brief_markdown(brief)
    assert "no memories" in md.lower()
