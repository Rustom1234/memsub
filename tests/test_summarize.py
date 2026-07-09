"""Session-summary extension tests. LLM calls go through MockBackend, zero cost."""

from memsub.backends import MockBackend
from memsub.store import Episode
from memsub.summarize import summarize_sessions


def _ep(content, session_id, ts, run_id="r1", namespace="default"):
    return Episode(
        content=content, run_id=run_id, session_id=session_id, kind="turn",
        importance=1.0, ts=ts, namespace=namespace,
    )


def test_summarize_sessions_groups_by_session_and_calls_backend_once_each():
    episodes = [
        _ep("a: hi", "s1", 1.0), _ep("b: hello", "s1", 2.0), _ep("a: bye", "s1", 3.0),
        _ep("a: one", "s2", 4.0), _ep("b: two", "s2", 5.0), _ep("a: three", "s2", 6.0),
    ]
    calls = []

    def responder(system, messages):
        calls.append(messages[0]["content"])
        return "a short summary"

    backend = MockBackend(responder=responder)
    summaries = summarize_sessions(episodes, backend, min_turns=3)
    assert len(summaries) == 2
    assert len(calls) == 2
    assert {s.session_id for s in summaries} == {"s1", "s2"}
    assert all(s.kind == "summary" for s in summaries)
    assert all(s.content == "a short summary" for s in summaries)


def test_summarize_sessions_skips_short_sessions():
    episodes = [_ep("a: hi", "s1", 1.0), _ep("b: hello", "s1", 2.0)]
    backend = MockBackend(responder=lambda s, m: "summary")
    summaries = summarize_sessions(episodes, backend, min_turns=3)
    assert summaries == []


def test_summarize_sessions_uses_last_turn_ts_and_first_namespace():
    episodes = [
        _ep("a: hi", "s1", 10.0, namespace="team"),
        _ep("b: hello", "s1", 20.0, namespace="team"),
        _ep("a: bye", "s1", 30.0, namespace="team"),
    ]
    backend = MockBackend(responder=lambda s, m: "summary")
    [summary] = summarize_sessions(episodes, backend, min_turns=3)
    assert summary.ts == 30.0
    assert summary.namespace == "team"
    assert summary.provenance["n_source_turns"] == 3
    assert summary.provenance["origin"] == "session_summary"


def test_summarize_sessions_skips_empty_response():
    episodes = [_ep("a: hi", "s1", 1.0), _ep("b: hello", "s1", 2.0), _ep("a: bye", "s1", 3.0)]
    backend = MockBackend(responder=lambda s, m: "   ")
    assert summarize_sessions(episodes, backend, min_turns=3) == []
