"""The governor: memory with teeth.

The enforcement layer nobody has built (researched 2026-07-21: letta #3320
is an unstarted proposal; every other memory product stops at retrieval).
A PreToolUse hook consults this module on every tool call an agent makes;
rules compiled from the user's CONFIRMED standing decisions can warn (a
user-visible receipt, never blocking) or deny (with an explicit override
token as the escape hatch). Every hit lands in a JSONL ledger: that ledger
is the false-positive calibration data that decides whether a warn rule
ever earns blocking power, and it feeds the morning brief.

Design contract:
- FAIL-OPEN. Any error, any malformed input, any missing file: allow,
  silently. The governor must never take the harness down with it.
- Static matchers only (tool name + one input field + regex). No LLM, no
  db, no network in the hot path: the hook budget is ~50ms.
- Rules live in a hand-editable JSON file with status active|staged.
  Staged rules never fire: activation is a human edit, the same
  sovereignty pattern as consolidate staging. Each rule carries a
  `receipt`: the episode id / memory that justifies it.

The week that motivated this, for the record: a standing "2-4 agents max"
decision existed in the vault while a workflow spawned 207 agents
($247.81); a standing "never push without approval" rule existed in
CLAUDE.md, enforced by nothing but model attention.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RULES_PATH = Path.home() / ".memsub" / "governor" / "rules.json"
DEFAULT_HITS_PATH = Path.home() / ".memsub" / "governor" / "hits.jsonl"


@dataclass(frozen=True)
class Verdict:
    verdict: str          # "allow" | "warn" | "deny"
    rule_id: str = ""
    message: str = ""
    receipt: str = ""


def load_rules(rules_path: str | Path = DEFAULT_RULES_PATH) -> list[dict]:
    try:
        data = json.loads(Path(rules_path).read_text())
        rules = data.get("rules", [])
        return rules if isinstance(rules, list) else []
    except (OSError, ValueError):
        return []


def _log_hit(hits_path: Path, rule: dict, tool_name: str, field_value: str,
             overridden: bool) -> None:
    try:
        hits_path.parent.mkdir(parents=True, exist_ok=True)
        with open(hits_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(), "rule_id": rule.get("id", ""),
                "verdict": rule.get("verdict", ""), "tool": tool_name,
                "matched": str(field_value)[:200], "overridden": overridden,
            }) + "\n")
    except OSError:
        pass


def check(
    tool_name,
    tool_input,
    rules_path: str | Path = DEFAULT_RULES_PATH,
    hits_path: str | Path = DEFAULT_HITS_PATH,
) -> Verdict:
    """Evaluate one tool call. Fail-open on every malformed anything."""
    try:
        if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
            return Verdict("allow")
        for rule in load_rules(rules_path):
            try:
                if rule.get("status") != "active":
                    continue
                if rule.get("tool") != tool_name:
                    continue
                value = tool_input.get(rule.get("field", ""))
                if not isinstance(value, str):
                    continue
                if not re.search(rule.get("pattern", r"(?!)"), value):
                    continue
                override = rule.get("override", "")
                overridden = bool(override) and override in value
                _log_hit(Path(hits_path), rule, tool_name, value, overridden)
                if overridden:
                    return Verdict("allow", rule.get("id", ""), "", rule.get("receipt", ""))
                return Verdict(
                    rule.get("verdict", "warn") if rule.get("verdict") in ("warn", "deny") else "warn",
                    rule.get("id", ""),
                    rule.get("message", ""),
                    rule.get("receipt", ""),
                )
            except Exception:
                continue  # one bad rule never poisons the rest
        return Verdict("allow")
    except Exception:
        return Verdict("allow")


def hook_response(v: Verdict) -> str | None:
    """Claude Code PreToolUse hook JSON for a verdict; None = stay silent."""
    if v.verdict == "deny":
        reason = (f"memsub governor: {v.message} (rule {v.rule_id}, receipt "
                  f"{v.receipt}). To override with the user's explicit "
                  f"approval, include the rule's override token in the call.")
        return json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }})
    if v.verdict == "warn":
        return json.dumps({"systemMessage": (
            f"memsub governor (warn): {v.message} [rule {v.rule_id}, "
            f"receipt {v.receipt}]"
        )})
    return None


def hits_for_day(day, hits_path: str | Path = DEFAULT_HITS_PATH) -> list[dict]:
    """Governor ledger entries whose ts falls on `day` (a datetime.date),
    for the morning brief's accountability section."""
    import datetime

    lo = datetime.datetime.combine(day, datetime.time.min).timestamp()
    hi = lo + 86400
    out = []
    try:
        with open(hits_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if lo <= rec.get("ts", 0) < hi:
                    out.append(rec)
    except OSError:
        pass
    return out


def main() -> int:
    """stdin: Claude Code PreToolUse hook JSON. stdout: response or silence."""
    import sys

    try:
        payload = json.loads(sys.stdin.read() or "{}")
        v = check(payload.get("tool_name"), payload.get("tool_input") or {})
        out = hook_response(v)
        if out:
            print(out)
    except Exception:
        pass  # fail-open
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
