"""`memsub loops scan`/`memsub loops json` CLI tests.

Fixture-only (no ~/.claude*/projects touched): see docs/loops-2026-07-22.md
for the live smoke-run result against the real transcript dirs.
"""

from __future__ import annotations

import datetime
import json
import os
import time

from memsub.cli import main

T0 = time.time() - 5000  # well past both --since and the default 30-min grace


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _msg(role, text, ts):
    return json.dumps({"type": role, "timestamp": _iso(ts),
                       "message": {"content": [{"type": "text", "text": text}]}})


def _write_session(path, lines):
    path.write_text("\n".join(lines) + "\n")
    old = T0 - 100
    os.utime(path, (old, old))


def test_loops_scan_reports_interrupt_finding(tmp_path, capsys):
    session = tmp_path / "s1.jsonl"
    _write_session(session, [
        _msg("user", "fix the parser", T0),
        _msg("assistant", "done", T0 + 60),
        _msg("user", "now also add tests please", T0 + 120),
    ])
    rc = main(["loops", "scan", "--projects-dir", str(tmp_path), "--since", "30"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "interrupt" in out
    assert "unanswered ask" in out
    assert str(session) in out


def test_loops_scan_no_findings_message(tmp_path, capsys):
    rc = main(["loops", "scan", "--projects-dir", str(tmp_path), "--since", "30"])
    assert rc == 0
    assert "no dropped-work findings" in capsys.readouterr().out


def test_loops_json_outputs_parseable_findings(tmp_path, capsys):
    session = tmp_path / "s1.jsonl"
    _write_session(session, [
        _msg("user", "run the build", T0),
    ])
    session.write_text(session.read_text() + json.dumps({
        "type": "assistant", "timestamp": _iso(T0 + 5),
        "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
    }) + "\n")
    os.utime(session, (T0 - 100, T0 - 100))

    rc = main(["loops", "json", "--projects-dir", str(tmp_path), "--since", "30"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert any(f["signal"] == "dangling_tool_use" for f in data)
    assert all({"session", "ts", "reason", "severity", "confidence", "signal"} <= f.keys() for f in data)
