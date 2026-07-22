"""Governor: memory with teeth. Tests written first (superpowers TDD).

Contract under test:
- rules load from a hand-editable JSON file; only status=active fires
- matchers are static and fast (regex on tool name + input fields)
- verdicts: deny (with override escape hatch) or warn (never blocks)
- every hit is appended to a JSONL ledger (the calibration data)
- FAIL-OPEN everywhere: malformed input, missing rules file, unknown
  tools -> silent allow. The governor must never break the harness.
"""

import json

import pytest

from memsub.governor import check, load_rules

RULES = [
    {"id": "no-push", "status": "active", "verdict": "deny",
     "tool": "Bash", "field": "command", "pattern": r"\bgit\s+push\b",
     "override": "GOVERNOR_OVERRIDE",
     "message": "Standing decision: never push without explicit approval.",
     "receipt": "global-claude-md"},
    {"id": "fable-subagent", "status": "active", "verdict": "warn",
     "tool": "Agent", "field": "model", "pattern": r"^(fable|opus)$",
     "message": "Standing decision: subagents run haiku/sonnet only.",
     "receipt": "subagent-model-policy"},
    {"id": "workflow-scale", "status": "staged", "verdict": "warn",
     "tool": "Workflow", "field": "script", "pattern": ".",
     "message": "2-4 agents max.", "receipt": "75402eac"},
]


@pytest.fixture()
def rules_file(tmp_path):
    p = tmp_path / "rules.json"
    p.write_text(json.dumps({"rules": RULES}))
    return p


def _check(rules_file, tmp_path, tool, tool_input):
    return check(tool_name=tool, tool_input=tool_input,
                 rules_path=rules_file, hits_path=tmp_path / "hits.jsonl")


def test_deny_rule_fires_and_logs(rules_file, tmp_path):
    v = _check(rules_file, tmp_path, "Bash", {"command": "git push origin master"})
    assert v.verdict == "deny"
    assert "never push" in v.message
    hits = (tmp_path / "hits.jsonl").read_text().strip().splitlines()
    assert len(hits) == 1
    assert json.loads(hits[0])["rule_id"] == "no-push"


def test_override_token_suppresses_deny(rules_file, tmp_path):
    v = _check(rules_file, tmp_path, "Bash",
               {"command": "git push origin master  # GOVERNOR_OVERRIDE per user"})
    assert v.verdict == "allow"
    # the override itself is logged for the audit trail
    assert json.loads((tmp_path / "hits.jsonl").read_text().strip())["overridden"] is True


def test_warn_rule_never_blocks(rules_file, tmp_path):
    v = _check(rules_file, tmp_path, "Agent", {"model": "fable", "prompt": "x"})
    assert v.verdict == "warn"
    v2 = _check(rules_file, tmp_path, "Agent", {"model": "sonnet", "prompt": "x"})
    assert v2.verdict == "allow"


def test_staged_rules_do_not_fire(rules_file, tmp_path):
    v = _check(rules_file, tmp_path, "Workflow", {"script": "anything"})
    assert v.verdict == "allow"


def test_fail_open_on_missing_rules(tmp_path):
    v = check(tool_name="Bash", tool_input={"command": "git push"},
              rules_path=tmp_path / "nope.json", hits_path=tmp_path / "h.jsonl")
    assert v.verdict == "allow"


def test_fail_open_on_garbage_input(rules_file, tmp_path):
    v = _check(rules_file, tmp_path, "Bash", {"command": None})
    assert v.verdict == "allow"
    v = _check(rules_file, tmp_path, None, "not a dict")
    assert v.verdict == "allow"


def test_load_rules_roundtrip(rules_file):
    rules = load_rules(rules_file)
    assert [r["id"] for r in rules] == ["no-push", "fable-subagent", "workflow-scale"]
