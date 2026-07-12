"""memsub.longmemeval: haystack-format adapter, schema validation, question types."""

from __future__ import annotations

import json

import pytest

from memsub.longmemeval import LongMemEvalFormatError, load_longmemeval

VALID_ITEM = {
    "question_id": "q1",
    "question_type": "single-session-user",
    "question": "What degree did I graduate with?",
    "answer": "Business Administration",
    "question_date": "2023/05/30 (Tue) 23:40",
    "haystack_session_ids": ["s1", "s2"],
    "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/25 (Thu) 10:00"],
    "haystack_sessions": [
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
        [{"role": "user", "content": "I graduated with a degree in Business Administration.",
          "has_answer": True}],
    ],
    "answer_session_ids": ["s2"],
}


def _write(tmp_path, items):
    p = tmp_path / "lme.json"
    p.write_text(json.dumps(items))
    return p


def test_load_valid_file(tmp_path):
    items = load_longmemeval(_write(tmp_path, [VALID_ITEM]))
    assert len(items) == 1
    item = items[0]
    assert item.question_id == "q1"
    assert item.question_type == "single-session-user"
    assert item.is_abstention is False
    assert item.evidence_session_ids == ("s2",)
    assert len(item.episodes) == 3  # 2 turns session 1 + 1 turn session 2
    assert item.episodes[0].content == "user: hello"
    assert item.episodes[0].session_id == "s1"


def test_abstention_flag_from_question_id_suffix(tmp_path):
    item = dict(VALID_ITEM, question_id="q1_abs")
    items = load_longmemeval(_write(tmp_path, [item]))
    assert items[0].is_abstention is True


def test_has_answer_flag_carried_into_provenance(tmp_path):
    items = load_longmemeval(_write(tmp_path, [VALID_ITEM]))
    evidence = [e for e in items[0].episodes if e.provenance.get("has_answer")]
    assert len(evidence) == 1
    assert "Business Administration" in evidence[0].content


def test_limit_truncates_parsed_items_but_not_validation(tmp_path):
    bad = dict(VALID_ITEM, question_id="q2", question_type="not-a-real-type")
    p = _write(tmp_path, [VALID_ITEM, bad])
    # even with limit=1 (only q1 would be parsed), the whole file is validated
    # first, so the bad item at index 1 must still raise
    with pytest.raises(LongMemEvalFormatError, match="not-a-real-type"):
        load_longmemeval(p, limit=1)


def test_missing_required_key_fails_fast(tmp_path):
    bad = {k: v for k, v in VALID_ITEM.items() if k != "haystack_dates"}
    with pytest.raises(LongMemEvalFormatError, match="missing keys"):
        load_longmemeval(_write(tmp_path, [bad]))


def test_mismatched_haystack_lengths_fails_fast(tmp_path):
    bad = dict(VALID_ITEM, haystack_dates=["2023/05/20 (Sat) 02:21"])  # len 1 vs 2
    with pytest.raises(LongMemEvalFormatError, match="lengths disagree"):
        load_longmemeval(_write(tmp_path, [bad]))


def test_unknown_question_type_fails_fast(tmp_path):
    bad = dict(VALID_ITEM, question_type="bogus-type")
    with pytest.raises(LongMemEvalFormatError, match="unknown question_type"):
        load_longmemeval(_write(tmp_path, [bad]))


def test_turn_missing_content_fails_fast(tmp_path):
    bad = dict(VALID_ITEM, haystack_sessions=[
        [{"role": "user"}], VALID_ITEM["haystack_sessions"][1],
    ])
    with pytest.raises(LongMemEvalFormatError, match="missing role/content"):
        load_longmemeval(_write(tmp_path, [bad]))


def test_turn_unknown_role_fails_fast(tmp_path):
    bad = dict(VALID_ITEM, haystack_sessions=[
        [{"role": "system", "content": "x"}], VALID_ITEM["haystack_sessions"][1],
    ])
    with pytest.raises(LongMemEvalFormatError, match="unknown role"):
        load_longmemeval(_write(tmp_path, [bad]))


def test_top_level_not_a_list_fails_fast(tmp_path):
    p = tmp_path / "lme.json"
    p.write_text(json.dumps({"not": "a list"}))
    with pytest.raises(LongMemEvalFormatError, match="top level is not a list"):
        load_longmemeval(p)


def test_all_official_question_types_accepted(tmp_path):
    from memsub.longmemeval import QUESTION_TYPES

    items = [dict(VALID_ITEM, question_id=f"q{i}", question_type=qt)
             for i, qt in enumerate(sorted(QUESTION_TYPES))]
    loaded = load_longmemeval(_write(tmp_path, items))
    assert {i.question_type for i in loaded} == QUESTION_TYPES
