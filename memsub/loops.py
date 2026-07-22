"""Open-loop detection: sessions that died with work outstanding.

Feature Y (2026-07-21 night, v0) started with one signal: a transcript
whose final substantive message is from the human means the session ended
before the agent replied (an auth failure, a crash, a limit, a closed
laptop). `find_open_loops`/`OpenLoop` below is that v0 detector, unchanged.

Loops v1 (2026-07-22, this module's extension) adds four more heuristics,
researched against real bugs and a real case from this repo's own overnight
run (see docs/loops-2026-07-22.md for the full writeup and the live smoke
result):

1. Dangling tool_use: a `tool_use` block with no matching `tool_result`
   later in the file. Pure JSON scan, no LLM, strongest structural signal.
2. Scheduled-wakeup never fired: Claude Code's ScheduleWakeup tool is
   in-memory only (anthropics/claude-code #61735, open) -- a wakeup can be
   "scheduled" and then simply never happen because nothing is left running
   to fire it. Detected by absence: no assistant activity near the promised
   time.
3. Auth/limit death: the session's last assistant turn is an API error
   (`isApiErrorMessage`/`error` field, or the equivalent auth/limit text for
   older transcripts) with nothing after it.
4. Unanswered explicit ask (v0's signal, wrapped as a Finding).

All four are heuristics over raw JSONL, not proof: v1 ships zero LLM calls
(see docs for the deferred LLM ask-extraction pass). Every Finding carries
a confidence (certain/likely/uncertain) so callers can filter.

Two signals from the research pass are NOT separate Findings by design:
- "[Request interrupted by user]" is a known false-positive-prone marker
  (interrupts fire even on deliberate, successful stops). v1 never reports
  it alone; `_has_interrupt_marker` only corroborates other findings.
- `compact_boundary` / `isCompactSummary` records are normal, not death --
  `_last_substantive` skips them so a post-compaction synthetic summary
  message is never mistaken for a live human ask.
"""

from __future__ import annotations

import datetime
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# v0: unanswered-ask detector (unchanged behavior, still the default path)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OpenLoop:
    session: str
    ts: float
    last_user_text: str
    repeats: int = 1  # identical asks across sessions collapse to the newest


def _last_substantive(path: Path) -> tuple[str, float, str] | None:
    """(role, ts, text) of the last human-or-assistant text message.

    Skips tool results, system reminders, and post-compaction synthetic
    summaries (`isCompactSummary: true` -- signal 5, "compaction is normal,
    not death": without this check, a session that happened to end right
    after an auto-compaction would show a "user" message that is really the
    continuation summary claude-code itself wrote, not a live human ask).
    """
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
            if rec.get("isCompactSummary"):
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


# --------------------------------------------------------------------------- #
# v1: multi-signal Finding, triage tiers, confidence
# --------------------------------------------------------------------------- #
SEVERITY_INTERRUPT = "interrupt"  # an explicit user ask left unanswered
SEVERITY_AMBIENT = "ambient"      # background work vanished, no ask pending
SEVERITY_DIGEST = "digest"        # informational only

_SEVERITY_ORDER = {SEVERITY_INTERRUPT: 0, SEVERITY_AMBIENT: 1, SEVERITY_DIGEST: 2}

CONFIDENCE_CERTAIN = "certain"
CONFIDENCE_LIKELY = "likely"
CONFIDENCE_UNCERTAIN = "uncertain"

SIGNAL_DANGLING_TOOL_USE = "dangling_tool_use"
SIGNAL_WAKEUP_NEVER_FIRED = "wakeup_never_fired"
SIGNAL_AUTH_LIMIT_DEATH = "auth_limit_death"
SIGNAL_UNANSWERED_ASK = "unanswered_ask"


@dataclass(frozen=True)
class Finding:
    """One dropped-work finding. `reason` is the one-line, human-readable
    label meant for direct display (triage report, /loops page row)."""
    session: str
    ts: float
    reason: str
    severity: str
    confidence: str
    signal: str


# Text fallback for transcript shapes that lack the structured
# isApiErrorMessage/error fields (e.g. older sessions, other tools).
_AUTH_LIMIT_RE = re.compile(
    r"oauth session expired|failed to authenticate|authentication failed|"
    r"usage limit reached|rate limit|quota exceeded",
    re.IGNORECASE,
)
_INTERRUPT_MARKER = "[Request interrupted"


def _has_interrupt_marker(path: Path) -> bool:
    """Weak corroborating signal only (research: known false-positive bug
    class -- interrupts fire on deliberate stops too). Never used to raise
    a Finding by itself, only to note extra context on one that already
    exists on its own evidence."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if _INTERRUPT_MARKER in line:
                    return True
    except OSError:
        pass
    return False


def _parse_ts(rec: dict) -> float | None:
    raw = rec.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text_of(rec: dict) -> str:
    content = rec.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _load_records(path: Path) -> list[dict]:
    """One pass, one parse per line; every v1 signal below reuses this list
    so a session file is only read from disk once."""
    records: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return records


def _detect_dangling_tool_use(records: list[dict]) -> tuple[float, str] | None:
    """(ts, tool_name) of the last tool_use with no matching tool_result
    anywhere later in the file, or None. Compaction is not consulted here:
    a `tool_result` genuinely absent from the raw file (not just summarized
    away) means the turn never completed."""
    tool_use: dict[str, tuple[float, str]] = {}  # id -> (ts, name)
    satisfied: set[str] = set()
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        content = rec.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        ts = _parse_ts(rec)
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                tool_use[block["id"]] = (ts or 0.0, block.get("name", "tool"))
    for rec in records:
        if rec.get("type") != "user":
            continue
        content = rec.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id"):
                satisfied.add(block["tool_use_id"])
    dangling = [(ts, name) for tid, (ts, name) in tool_use.items() if tid not in satisfied]
    if not dangling:
        return None
    dangling.sort(key=lambda t: t[0])
    return dangling[-1]


def _detect_wakeup_never_fired(records: list[dict], grace_secs: float = 600) -> tuple[float, float] | None:
    """(schedule_call_ts, scheduled_ts) for the last ScheduleWakeup call in
    the file if no assistant activity appears in
    [scheduled_ts, scheduled_ts + grace_secs] -- i.e. the promised
    continuation never shows up in the transcript at all. ScheduleWakeup is
    in-memory only (anthropics/claude-code #61735): this is an absence
    heuristic, not proof, hence `likely` confidence in the caller.
    """
    last_schedule: tuple[float, float] | None = None  # (call_ts, delay_secs)
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        content = rec.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "ScheduleWakeup":
                call_ts = _parse_ts(rec)
                delay = block.get("input", {}).get("delaySeconds")
                if call_ts is not None and isinstance(delay, (int, float)):
                    last_schedule = (call_ts, float(delay))
    if last_schedule is None:
        return None
    call_ts, delay = last_schedule
    scheduled_ts = call_ts + delay
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        ts = _parse_ts(rec)
        if ts is None:
            continue
        if scheduled_ts <= ts <= scheduled_ts + grace_secs:
            return None  # something assistant-side happened near the promised time
    return (call_ts, scheduled_ts)


def _detect_auth_limit_death(records: list[dict]) -> tuple[float, str, str] | None:
    """(ts, kind, detail) for the file's terminal assistant turn if it is an
    API error (structured `isApiErrorMessage`/`error` field -- certain
    confidence) or, failing that, matches an auth/limit text pattern
    (`likely` confidence, older transcript shapes). None if the file's last
    substantive record is not this shape, or a user reply follows it."""
    last_idx = None
    for i, rec in enumerate(records):
        if rec.get("type") in ("user", "assistant"):
            last_idx = i
    if last_idx is None:
        return None
    rec = records[last_idx]
    if rec.get("type") != "assistant":
        return None  # a user reply (or nothing after) means this isn't terminal
    ts = _parse_ts(rec) or 0.0
    if rec.get("isApiErrorMessage") and rec.get("error"):
        return (ts, str(rec["error"]), "certain")
    text = _text_of(rec)
    if text and _AUTH_LIMIT_RE.search(text):
        return (ts, "text-match", "likely")
    return None


def scan_session(path: Path) -> list[Finding]:
    """Every v1 signal over one session file, read once. Signal 4
    (unanswered ask) reuses `_last_substantive` directly rather than
    `find_open_loops` so this function has no directory-level cutoff
    concerns of its own -- callers apply days/min_age_secs before calling."""
    session = str(path)
    records = _load_records(path)
    if not records:
        return []
    findings: list[Finding] = []
    interrupt_note = " (an interrupted-marker also appears in this session, weak corroboration)" \
        if _has_interrupt_marker(path) else ""

    dangling = _detect_dangling_tool_use(records)
    if dangling is not None:
        ts, name = dangling
        findings.append(Finding(
            session=session, ts=ts,
            reason=f"tool call '{name}' never got a result: session died mid-action",
            severity=SEVERITY_AMBIENT, confidence=CONFIDENCE_CERTAIN,
            signal=SIGNAL_DANGLING_TOOL_USE,
        ))

    wakeup = _detect_wakeup_never_fired(records)
    if wakeup is not None:
        _call_ts, scheduled_ts = wakeup
        when = datetime.datetime.fromtimestamp(scheduled_ts).strftime("%H:%M")
        findings.append(Finding(
            session=session, ts=scheduled_ts,
            reason=f"wakeup scheduled for {when} never resurfaced (ScheduleWakeup is in-memory only)",
            severity=SEVERITY_AMBIENT, confidence=CONFIDENCE_LIKELY,
            signal=SIGNAL_WAKEUP_NEVER_FIRED,
        ))

    auth_death = _detect_auth_limit_death(records)
    if auth_death is not None:
        ts, kind, confidence = auth_death
        if kind == "rate_limit":
            reason = "session died to a usage/rate limit with no reply after"
        elif kind == "authentication_failed":
            reason = "session died to an auth failure (OAuth expired) with no reply after"
        elif kind == "text-match":
            reason = "session's last message is an auth/limit failure with no reply after"
        else:
            reason = f"session died to an API error ({kind}) with no reply after"
        findings.append(Finding(
            session=session, ts=ts, reason=reason + interrupt_note,
            severity=SEVERITY_INTERRUPT, confidence=confidence,
            signal=SIGNAL_AUTH_LIMIT_DEATH,
        ))

    last = _last_substantive(path)
    if last is not None:
        role, ts, text = last
        if role == "user" and not text.startswith("[Request interrupted"):
            findings.append(Finding(
                session=session, ts=ts,
                reason=f'unanswered ask: "{text[:80]}"' + interrupt_note,
                severity=SEVERITY_INTERRUPT, confidence=CONFIDENCE_CERTAIN,
                signal=SIGNAL_UNANSWERED_ASK,
            ))

    return findings


def default_scan_dirs() -> list[Path]:
    """Every `~/.claude*/projects` that exists (`~/.claude/projects`,
    `~/.claude-remote/projects`, any other `.claude*` variant)."""
    home = Path.home()
    return sorted(p for p in home.glob(".claude*/projects") if p.is_dir())


def scan_loops(
    dirs: list[str | Path] | None = None,
    days: int = 7,
    min_age_secs: float = 1800,
) -> list[Finding]:
    """All v1 findings across one or more transcript directories, newest
    first within severity tier (interrupt, then ambient, then digest --
    triage order, not pure chronological)."""
    search_dirs = [Path(d) for d in dirs] if dirs else default_scan_dirs()
    cutoff = time.time() - days * 86400
    findings: list[Finding] = []
    seen_inodes: set[tuple[int, int]] = set()  # (st_dev, st_ino): dedup hardlinked
    # session files shared between e.g. ~/.claude/projects and
    # ~/.claude-remote/projects (docs/loops-2026-07-22.md caveat), keeping the
    # first path encountered.
    for d in search_dirs:
        if not d.is_dir():
            continue
        for path in d.rglob("*.jsonl"):
            if "/subagents/" in str(path):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            mtime = st.st_mtime
            if mtime < cutoff or time.time() - mtime < min_age_secs:
                continue
            inode = (st.st_dev, st.st_ino)
            if inode in seen_inodes:
                continue
            seen_inodes.add(inode)
            findings.extend(scan_session(path))
    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), -f.ts))
    return findings


_SEVERITY_LABELS = {
    SEVERITY_INTERRUPT: "interrupt -- unanswered ask",
    SEVERITY_AMBIENT: "ambient -- background work vanished",
    SEVERITY_DIGEST: "digest -- informational",
}


def render_loops_report(findings: list[Finding]) -> str:
    """Triage-ordered plain-text report: interrupt tier first, each line
    carries the reason label, confidence, and session path (`memsub loops
    scan`'s output)."""
    if not findings:
        return "no dropped-work findings"
    lines: list[str] = []
    for sev in (SEVERITY_INTERRUPT, SEVERITY_AMBIENT, SEVERITY_DIGEST):
        group = [f for f in findings if f.severity == sev]
        if not group:
            continue
        lines.append(f"== {_SEVERITY_LABELS[sev]}: {len(group)} ==")
        for f in group:
            when = datetime.datetime.fromtimestamp(f.ts).strftime("%Y-%m-%d %H:%M")
            lines.append(f"  {when}  [{f.confidence}]  {f.reason}")
            lines.append(f"      {f.session}")
    return "\n".join(lines)
