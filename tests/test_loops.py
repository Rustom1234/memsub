"""Y: dead-session open-loop detection. Tests first (superpowers TDD).

A session that ends on a user message (or an error tail) left the user's
last ask unanswered: nobody ever came back to it. Nothing else detects
this (researched: claude-code #11455/#63023 beg for it; claude-mem treats
all sessions uniformly).
"""

import json

from memsub.loops import find_open_loops

T0 = 1_784_500_000.0


def _iso(ts):
    import datetime

    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _msg(role, text, ts):
    return json.dumps({"type": role, "timestamp": _iso(ts),
                       "message": {"content": [{"type": "text", "text": text}]}})


def test_detects_session_ending_on_user_message(tmp_path):
    t = tmp_path / "s1.jsonl"
    t.write_text("\n".join([
        _msg("user", "fix the parser", T0),
        _msg("assistant", "done, parser fixed", T0 + 60),
        _msg("user", "now also compare us to claude-mem please", T0 + 120),
    ]) + "\n")
    loops = find_open_loops(tmp_path, min_age_secs=0)
    assert len(loops) == 1
    assert "claude-mem" in loops[0].last_user_text
    assert loops[0].session.endswith("s1.jsonl")


def test_answered_session_is_closed(tmp_path):
    t = tmp_path / "s2.jsonl"
    t.write_text("\n".join([
        _msg("user", "fix the parser", T0),
        _msg("assistant", "done, parser fixed", T0 + 60),
    ]) + "\n")
    assert find_open_loops(tmp_path, min_age_secs=0) == []


def test_ignores_tool_result_user_turns(tmp_path):
    """tool_result messages have type user but are not human asks."""
    t = tmp_path / "s3.jsonl"
    t.write_text("\n".join([
        _msg("user", "run the tests", T0),
        _msg("assistant", "running", T0 + 5),
        json.dumps({"type": "user", "timestamp": _iso(T0 + 10), "message": {"content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "1 passed"}]}}),
    ]) + "\n")
    assert find_open_loops(tmp_path, min_age_secs=0) == []


def test_recent_sessions_are_grace_period(tmp_path):
    import time

    t = tmp_path / "s4.jsonl"
    t.write_text(_msg("user", "still typing here", time.time()) + "\n")
    assert find_open_loops(tmp_path, min_age_secs=3600) == []
