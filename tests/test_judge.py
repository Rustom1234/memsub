"""memsub.judge: Sonnet-judge wrapper, mocked backends (no claude CLI needed)."""

from __future__ import annotations

from memsub.backends import MockBackend
from memsub.judge import build_judge_prompt, judge_answer, parse_judge_response


def test_build_judge_prompt_contains_all_three_fields():
    prompt = build_judge_prompt("q?", "gold", "answer")
    assert "QUESTION: q?" in prompt
    assert "GOLD ANSWER: gold" in prompt
    assert "MODEL ANSWER: answer" in prompt


def test_parse_valid_json_true():
    assert parse_judge_response('{"correct": true}') is True


def test_parse_valid_json_false():
    assert parse_judge_response('{"correct": false}') is False


def test_parse_json_embedded_in_extra_text():
    assert parse_judge_response('Sure, here is my verdict: {"correct": true} thanks') is True


def test_parse_falls_back_to_incorrect_keyword():
    assert parse_judge_response("The answer is incorrect.") is False


def test_parse_falls_back_to_correct_keyword_not_matched_inside_incorrect():
    # "incorrect" must not be matched by the "correct" fallback
    assert parse_judge_response("This is correct.") is True


def test_parse_malformed_returns_none():
    assert parse_judge_response("I cannot judge this.") is None


def test_judge_answer_ok():
    backend = MockBackend(responder=lambda s, m: '{"correct": true}')
    log: list[dict] = []
    result = judge_answer("q", "gold", "ans", backend, log=log)
    assert result["status"] == "ok"
    assert result["correct"] is True
    assert log[0]["status"] == "ok"


def test_judge_answer_malformed_response_not_scored():
    backend = MockBackend(responder=lambda s, m: "no verdict here at all")
    result = judge_answer("q", "gold", "ans", backend)
    assert result["status"] == "malformed"
    assert result["correct"] is None


def test_judge_answer_backend_error_does_not_raise():
    def boom(s, m):
        raise RuntimeError("claude cli failed")

    backend = MockBackend(responder=boom)
    log: list[dict] = []
    result = judge_answer("q", "gold", "ans", backend, log=log)
    assert result["status"] == "error"
    assert result["correct"] is None
    assert "claude cli failed" in log[0]["error"]


def test_judge_answer_one_call_per_item():
    calls = []

    def responder(system, messages):
        calls.append(messages[0]["content"])
        return '{"correct": false}'

    backend = MockBackend(responder=responder)
    judge_answer("what color?", "blue", "red", backend)
    assert len(calls) == 1
    assert "what color?" in calls[0]
