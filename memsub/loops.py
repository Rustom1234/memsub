"""Open-loop detection: sessions that died with the user's last ask unanswered.

Feature Y (2026-07-21 night). A transcript whose final substantive message
is from the human means the session ended before the agent replied: an auth
failure, a crash, a limit, a closed laptop. That ask is an open loop nobody
will ever resurface (researched: no tool does this; users ask for it in
anthropics/claude-code #11455 and #63023). This module finds them so the
morning brief and `memsub loops` can hand them back.

The 2026-07-21 canonical case: the host-a session died on "Authentication
failed" holding two questions that were only rescued by hand, hours later.
"""

from __future__ import annotations

import datetime
import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpenLoop:
    session: str
    ts: float
    last_user_text: str
    repeats: int = 1  # identical asks across sessions collapse to the newest


def _last_substantive(path: Path) -> tuple[str, float, str] | None:
    """(role, ts, text) of the last human-or-assistant text message."""
    last: tuple[str, float, str] | None = None
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            if '"type"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            role = rec.get("type")
            if role not in ("user", "assistant"):
                continue
            content = rec.get("message", {}).get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text")
            else:
                continue
            text = text.strip()
            if not text or text.startswith("<"):  # tool results, system reminders
                continue
            ts = 0.0
            try:
                ts = datetime.datetime.fromisoformat(
                    str(rec.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
            last = (role, ts, text)
    return last


def find_open_loops(
    transcripts_dir: str | Path,
    days: int = 7,
    min_age_secs: float = 1800,
    max_text: int = 240,
) -> list[OpenLoop]:
    """Sessions from the last `days` whose final substantive message is the
    user's, and which have been quiet for min_age_secs (grace period so a
    session mid-typing is not a loop). Newest first."""
    cutoff = time.time() - days * 86400
    loops: list[OpenLoop] = []
    for path in Path(transcripts_dir).rglob("*.jsonl"):
        if "/subagents/" in str(path):
            continue  # workers, not conversations with the human
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff or time.time() - mtime < min_age_secs:
            continue
        last = _last_substantive(path)
        if last is None:
            continue
        role, ts, text = last
        if role != "user":
            continue
        if text.startswith("[Request interrupted"):
            continue  # a deliberate interrupt, not a dropped ask
        loops.append(OpenLoop(session=str(path), ts=ts or mtime,
                              last_user_text=text[:max_text]))
    # collapse identical asks (batch automation dying repeatedly on the same
    # prompt is one loop, not twenty)
    by_key: dict[str, OpenLoop] = {}
    for l in loops:
        key = l.last_user_text[:120]
        if key in by_key:
            prev = by_key[key]
            newest = l if l.ts > prev.ts else prev
            by_key[key] = OpenLoop(session=newest.session, ts=newest.ts,
                                   last_user_text=newest.last_user_text,
                                   repeats=prev.repeats + 1)
        else:
            by_key[key] = l
    out = sorted(by_key.values(), key=lambda l: l.ts, reverse=True)
    return out
