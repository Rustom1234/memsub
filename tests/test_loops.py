"""Y: dead-session open-loop detection. Tests first (superpowers TDD).

A session that ends on a user message (or an error tail) left the user's
last ask unanswered: nobody ever came back to it. Nothing else detects
this (researched: claude-code #11455/#63023 beg for it; claude-mem treats
all sessions uniformly).

v1 (2026-07-22) adds fixture tests per new signal: dangling tool_use,
wakeup-never-fired, auth/limit death, compaction exclusion, and the
interrupted-marker weak-signal rule. See memsub/loops.py's module
docstring for the design.
"""

import json

from memsub.loops import (
    SEVERITY_AMBIENT,
    SEVERITY_INTERRUPT,
    SIGNAL_AUTH_LIMIT_DEATH,
    SIGNAL_DANGLING_TOOL_USE,
    SIGNAL_UNANSWERED_ASK,
    SIGNAL_WAKEUP_NEVER_FIRED,
    default_scan_dirs,
    find_open_loops,
    scan_loops,
    scan_session,
)

T0 = 1_784_500_000.0


def _iso(ts):
    import datetime

    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _msg(role, text, ts):
    return json.dumps({"type": role, "timestamp": _iso(ts),
                       "message": {"content": [{"type": "text", "text": text}]}})


def _tool_use(ts, tool_id, name, tool_input=None):
    return json.dumps({"type": "assistant", "timestamp": _iso(ts), "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input or {}}]}})


def _tool_result(ts, tool_id, content="ok"):
    return json.dumps({"type": "user", "timestamp": _iso(ts), "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": content}]}})


def _api_error(ts, error="authentication_failed", text="Failed to authenticate"):
    return json.dumps({"type": "assistant", "timestamp": _iso(ts), "isApiErrorMessage": True,
                       "error": error, "message": {"content": [{"type": "text", "text": text}]}})


def _compact_summary(ts, text="This session is being continued from a previous conversation."):
    return json.dumps({"type": "user", "timestamp": _iso(ts), "isCompactSummary": True,
                       "message": {"content": text}})


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


# --------------------------------------------------------------------------- #
# v1: dangling tool_use (signal 1)
# --------------------------------------------------------------------------- #
def test_scan_session_flags_dangling_tool_use(tmp_path):
    t = tmp_path / "dangling.jsonl"
    t.write_text("\n".join([
        _msg("user", "run the build", T0),
        _tool_use(T0 + 5, "tool-1", "Bash", {"command": "make build"}),
    ]) + "\n")
    findings = scan_session(t)
    signals = {f.signal for f in findings}
    assert SIGNAL_DANGLING_TOOL_USE in signals
    dangling = [f for f in findings if f.signal == SIGNAL_DANGLING_TOOL_USE][0]
    assert dangling.severity == SEVERITY_AMBIENT
    assert "Bash" in dangling.reason


def test_scan_session_tool_use_with_result_is_not_dangling(tmp_path):
    t = tmp_path / "clean.jsonl"
    t.write_text("\n".join([
        _msg("user", "run the build", T0),
        _tool_use(T0 + 5, "tool-1", "Bash", {"command": "make build"}),
        _tool_result(T0 + 6, "tool-1", "build ok"),
        _msg("assistant", "build passed", T0 + 7),
    ]) + "\n")
    findings = scan_session(t)
    assert SIGNAL_DANGLING_TOOL_USE not in {f.signal for f in findings}


# --------------------------------------------------------------------------- #
# v1: scheduled-wakeup never fired (signal 2) -- shape of the real
# 2026-07-22 case (docs/loops-2026-07-22.md), reproduced synthetically
# --------------------------------------------------------------------------- #
def test_scan_session_flags_wakeup_never_fired(tmp_path):
    t = tmp_path / "wakeup-silent.jsonl"
    t.write_text("\n".join([
        _msg("user", "keep going overnight, don't wait for me", T0),
        _tool_use(T0 + 10, "wake-1", "ScheduleWakeup", {"delaySeconds": 1500, "prompt": "continue"}),
        _tool_result(T0 + 12, "wake-1", "Next wakeup scheduled for 00:46:00 (in 1500s)"),
        _msg("assistant", "scheduled, working until then", T0 + 20),
        # nothing assistant-side near T0+10+1500 .. +2100 (grace) -- the
        # wakeup silently never fires; the human checks in hours later and
        # the session has died to an auth failure (signal 3, same session).
        _msg("user", "what have you been up to all night?", T0 + 33000),
        _api_error(T0 + 33010),
    ]) + "\n")
    findings = scan_session(t)
    signals = {f.signal for f in findings}
    assert SIGNAL_WAKEUP_NEVER_FIRED in signals
    assert SIGNAL_AUTH_LIMIT_DEATH in signals


def test_scan_session_wakeup_that_fired_is_not_flagged(tmp_path):
    t = tmp_path / "wakeup-fired.jsonl"
    t.write_text("\n".join([
        _msg("user", "keep going overnight", T0),
        _tool_use(T0 + 10, "wake-1", "ScheduleWakeup", {"delaySeconds": 100, "prompt": "continue"}),
        _tool_result(T0 + 12, "wake-1", "Next wakeup scheduled for ... (in 100s)"),
        _msg("assistant", "continuing the overnight build", T0 + 10 + 100 + 30),
        _msg("assistant", "done, all green", T0 + 10 + 100 + 60),
    ]) + "\n")
    findings = scan_session(t)
    assert SIGNAL_WAKEUP_NEVER_FIRED not in {f.signal for f in findings}


# --------------------------------------------------------------------------- #
# v1: auth/limit death (signal 3)
# --------------------------------------------------------------------------- #
def test_scan_session_flags_auth_death(tmp_path):
    t = tmp_path / "auth-death.jsonl"
    t.write_text("\n".join([
        _msg("user", "please finish the migration", T0),
        _api_error(T0 + 5, error="authentication_failed",
                   text="Failed to authenticate: OAuth session expired and could not be refreshed"),
    ]) + "\n")
    findings = scan_session(t)
    auth_findings = [f for f in findings if f.signal == SIGNAL_AUTH_LIMIT_DEATH]
    assert len(auth_findings) == 1
    assert auth_findings[0].severity == SEVERITY_INTERRUPT
    assert auth_findings[0].confidence == "certain"
    assert "auth" in auth_findings[0].reason.lower()


def test_scan_session_rate_limit_death(tmp_path):
    t = tmp_path / "rate-limit.jsonl"
    t.write_text("\n".join([
        _msg("user", "keep going", T0),
        _api_error(T0 + 5, error="rate_limit", text="You have hit your usage limit"),
    ]) + "\n")
    findings = scan_session(t)
    auth_findings = [f for f in findings if f.signal == SIGNAL_AUTH_LIMIT_DEATH]
    assert len(auth_findings) == 1
    assert "limit" in auth_findings[0].reason.lower()


def test_scan_session_no_auth_death_when_user_replies_after(tmp_path):
    t = tmp_path / "recovered.jsonl"
    t.write_text("\n".join([
        _msg("user", "please finish the migration", T0),
        _api_error(T0 + 5),
        _msg("user", "ok I re-authed, try again", T0 + 3600),
        _msg("assistant", "done", T0 + 3620),
    ]) + "\n")
    findings = scan_session(t)
    assert SIGNAL_AUTH_LIMIT_DEATH not in {f.signal for f in findings}


# --------------------------------------------------------------------------- #
# v1: compaction is normal, not death (signal 5)
# --------------------------------------------------------------------------- #
def test_scan_session_ignores_compact_summary_as_last_message(tmp_path):
    t = tmp_path / "compacted.jsonl"
    t.write_text("\n".join([
        _msg("user", "long conversation", T0),
        _msg("assistant", "ok", T0 + 5),
        _compact_summary(T0 + 10),
    ]) + "\n")
    findings = scan_session(t)
    assert SIGNAL_UNANSWERED_ASK not in {f.signal for f in findings}


# --------------------------------------------------------------------------- #
# v1: interrupted marker is corroboration only, never a standalone Finding
# --------------------------------------------------------------------------- #
def test_scan_session_interrupted_marker_alone_produces_no_unanswered_ask(tmp_path):
    t = tmp_path / "interrupted.jsonl"
    t.write_text("\n".join([
        _msg("user", "start the long task", T0),
        _msg("assistant", "working on it", T0 + 5),
        _msg("user", "[Request interrupted by user]", T0 + 10),
    ]) + "\n")
    findings = scan_session(t)
    assert SIGNAL_UNANSWERED_ASK not in {f.signal for f in findings}


# --------------------------------------------------------------------------- #
# v1: scan_loops triage ordering (interrupt before ambient) and default dirs
# --------------------------------------------------------------------------- #
def test_scan_loops_orders_interrupt_before_ambient(tmp_path):
    ambient_only = tmp_path / "ambient.jsonl"
    ambient_only.write_text("\n".join([
        _msg("user", "run the build", T0),
        _tool_use(T0 + 5, "tool-1", "Bash", {"command": "make build"}),
    ]) + "\n")
    interrupt_case = tmp_path / "interrupt.jsonl"
    interrupt_case.write_text("\n".join([
        _msg("user", "fix the parser", T0),
        _msg("assistant", "done", T0 + 60),
        _msg("user", "now also add tests please", T0 + 120),
    ]) + "\n")
    findings = scan_loops([tmp_path], min_age_secs=0)
    assert findings, "expected at least one finding"
    assert findings[0].severity == SEVERITY_INTERRUPT


def test_default_scan_dirs_finds_every_dot_claude_star_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    (tmp_path / ".claude-remote" / "projects").mkdir(parents=True)
    (tmp_path / ".claude-other" / "not-projects").mkdir(parents=True)
    dirs = {str(p) for p in default_scan_dirs()}
    assert str(tmp_path / ".claude" / "projects") in dirs
    assert str(tmp_path / ".claude-remote" / "projects") in dirs
    assert len(dirs) == 2


# --------------------------------------------------------------------------- #
# v1: dedup hardlinked session files across scanned directories (docs/
# loops-2026-07-22.md caveat -- ~/.claude/projects and ~/.claude-remote/projects
# can share the same file via os.link; scan_loops must count it once)
# --------------------------------------------------------------------------- #
def test_scan_loops_dedupes_hardlinked_session_across_dirs(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("HOME", str(tmp_path))
    dir_a = tmp_path / "dir-a"
    dir_b = tmp_path / "dir-b"
    dir_a.mkdir()
    dir_b.mkdir()
    original = dir_a / "session.jsonl"
    original.write_text("\n".join([
        _msg("user", "fix the parser", T0),
        _msg("assistant", "done", T0 + 60),
        _msg("user", "now also add tests please", T0 + 120),
    ]) + "\n")
    linked = dir_b / "session.jsonl"
    os.link(original, linked)  # same (st_dev, st_ino), different path

    findings = scan_loops([dir_a, dir_b], min_age_secs=0)
    sessions = {f.session for f in findings}
    assert len(sessions) == 1
    assert sessions == {str(original)}
