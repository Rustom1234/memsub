"""LLM-judge metric: scores answer correctness given (question, gold, answer).

Substitutes for the GPT-4o judge used in published LOCOMO/LongMemEval
leaderboards (docs/benchmark-external.md explains the deviation this causes).
Uses Claude Sonnet via the local `claude` CLI in print mode
(`memsub.backends.ClaudeCLIBackend`, model="sonnet"), one compact prompt per
item. The prompt asks for a strict JSON verdict so the grade is stable
regardless of decoding temperature; parsing also accepts a plain-text
CORRECT/INCORRECT fallback for whatever a model returns outside JSON, and
anything else is reported as a malformed response rather than guessed at.
"""

from __future__ import annotations

import json
import re

JUDGE_SYSTEM = (
    "You are grading whether a MODEL ANSWER correctly answers a QUESTION, "
    "using a GOLD ANSWER as the reference. Judge on meaning, not exact "
    "wording: paraphrases, different units or formats, or a partial phrase "
    "that captures the same fact are CORRECT. Judge INCORRECT if the model "
    "answer is wrong, missing, off-topic, or contradicts the gold answer. "
    'Respond with ONLY a JSON object of the exact form {"correct": true} or '
    '{"correct": false}. No other text, no markdown fences, no explanation.'
)

_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)
_TRUE_RE = re.compile(r"\btrue\b", re.IGNORECASE)
_FALSE_RE = re.compile(r"\bfalse\b", re.IGNORECASE)
_INCORRECT_RE = re.compile(r"\bincorrect\b", re.IGNORECASE)
_CORRECT_RE = re.compile(r"\bcorrect\b", re.IGNORECASE)


def build_judge_prompt(question: str, gold: str, answer: str) -> str:
    return f"QUESTION: {question}\nGOLD ANSWER: {gold}\nMODEL ANSWER: {answer}"


def parse_judge_response(raw: str) -> bool | None:
    """Parse a judge verdict. Returns True/False, or None if unparseable.

    Primary path: a JSON object with a "correct" boolean. Fallback: plain
    text containing the standalone word incorrect/correct or true/false
    (checked in that order so "incorrect" is never matched as "correct").
    Returns None only when nothing usable is found at all.
    """
    m = _JSON_RE.search(raw)
    if m:
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and "correct" in parsed:
            val = parsed["correct"]
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                if val.strip().lower() == "true":
                    return True
                if val.strip().lower() == "false":
                    return False
    if _INCORRECT_RE.search(raw):
        return False
    if _CORRECT_RE.search(raw):
        return True
    if _FALSE_RE.search(raw):
        return False
    if _TRUE_RE.search(raw):
        return True
    return None


def judge_answer(
    question: str,
    gold: str,
    answer: str,
    backend,
    log: list[dict] | None = None,
) -> dict:
    """Score one (question, gold, answer) triple. Never raises.

    Returns {"correct": bool | None, "status": "ok" | "malformed" | "error",
    "raw": str}. On a backend exception or an unparseable response, "correct"
    is None and "status" explains why, rather than defaulting to a guess --
    a malformed/errored item is excluded from accuracy, not silently scored
    wrong or right.
    """
    entry: dict = {
        "question": question,
        "backend": getattr(backend, "name", "?"),
        "model": getattr(backend, "model", "?"),
    }
    try:
        prompt = build_judge_prompt(question, gold, answer)
        raw = backend.complete(JUDGE_SYSTEM, [{"role": "user", "content": prompt}])
        verdict = parse_judge_response(raw)
        if verdict is None:
            entry.update(status="malformed", raw=raw, correct=None)
        else:
            entry.update(status="ok", raw=raw, correct=verdict)
    except Exception as exc:  # noqa: BLE001 - any backend failure must degrade, not crash
        entry.update(status="error", error=str(exc)[:200], correct=None)
    if log is not None:
        log.append(entry)
    return entry
