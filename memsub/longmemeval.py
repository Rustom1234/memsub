"""LongMemEval adapter: parse the haystack-session dataset format into Episodes.

LongMemEval (Wu et al., ICLR 2025, arXiv 2410.10813) ships each question with
its OWN independent chat history ("haystack"): a list of sessions, each a
list of {"role": "user"|"assistant", "content": str} turns, with an optional
"has_answer": true flag on evidence turns. This mirrors memsub.bench's LOCOMO
loader (turn-level Episode ingestion, no LLM extraction pass -- see
docs/benchmark-external.md for why that matches the existing LOCOMO
methodology in this repo) but the haystack is per-question here, not shared
across a conversation's whole QA set.

Schema validation fails fast (LongMemEvalFormatError) rather than silently
skipping or guessing on drift: this dataset is downloaded from a third party
and re-released periodically (the repo README notes a 2025/09 "cleaned"
revision), so a field rename or shape change should be caught immediately,
not surface as a confusing downstream KeyError or a silently-empty haystack.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .store import Episode

# Official question types (LongMemEval README / paper Table 1). A question_id
# ending in "_abs" is an abstention variant of the same six types, not a
# seventh type.
QUESTION_TYPES = frozenset({
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "temporal-reasoning",
    "knowledge-update",
    "multi-session",
})

_REQUIRED_KEYS = (
    "question_id", "question_type", "question", "answer", "question_date",
    "haystack_session_ids", "haystack_dates", "haystack_sessions",
    "answer_session_ids",
)


class LongMemEvalFormatError(ValueError):
    """Raised when the dataset does not match the expected haystack schema."""


@dataclass
class LongMemEvalItem:
    question_id: str
    question_type: str
    is_abstention: bool
    question: str
    answer: str
    question_date: str
    evidence_session_ids: tuple[str, ...]
    episodes: list[Episode] = field(default_factory=list)


def _fail(msg: str) -> None:
    raise LongMemEvalFormatError(f"LongMemEval schema drift: {msg}")


def _validate_item(item: dict, idx: int) -> None:
    if not isinstance(item, dict):
        _fail(f"item {idx} is not an object (got {type(item).__name__})")
    missing = [k for k in _REQUIRED_KEYS if k not in item]
    if missing:
        _fail(f"item {idx} ({item.get('question_id', '?')}) missing keys: {missing}")
    n_sessions = len(item["haystack_session_ids"])
    if len(item["haystack_dates"]) != n_sessions or len(item["haystack_sessions"]) != n_sessions:
        _fail(
            f"item {idx} ({item['question_id']}): haystack_session_ids "
            f"({n_sessions}), haystack_dates ({len(item['haystack_dates'])}), and "
            f"haystack_sessions ({len(item['haystack_sessions'])}) lengths disagree"
        )
    base_type = item["question_type"]
    if base_type not in QUESTION_TYPES:
        _fail(
            f"item {idx} ({item['question_id']}): unknown question_type "
            f"{base_type!r}, expected one of {sorted(QUESTION_TYPES)}"
        )
    for si, session in enumerate(item["haystack_sessions"]):
        if not isinstance(session, list):
            _fail(f"item {idx} ({item['question_id']}) session {si} is not a list of turns")
        for ti, turn in enumerate(session):
            if not isinstance(turn, dict) or "role" not in turn or "content" not in turn:
                _fail(
                    f"item {idx} ({item['question_id']}) session {si} turn {ti} "
                    f"missing role/content: {turn!r}"
                )
            if turn["role"] not in ("user", "assistant"):
                _fail(
                    f"item {idx} ({item['question_id']}) session {si} turn {ti} "
                    f"has unknown role {turn['role']!r}"
                )


def load_longmemeval(path: str | Path, limit: int | None = None) -> list[LongMemEvalItem]:
    """Parse a LongMemEval JSON file (e.g. longmemeval_s_cleaned.json).

    Validates the full file's schema before building any Episode (fail fast
    on the whole file, not partway through). `limit` truncates the number of
    items parsed into Episodes (for dry runs / pre-registered subsamples);
    validation still runs over every item in the file regardless of `limit`.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        _fail(f"top level is not a list (got {type(raw).__name__})")
    for idx, item in enumerate(raw):
        _validate_item(item, idx)

    out: list[LongMemEvalItem] = []
    for item in raw[:limit] if limit is not None else raw:
        qid = item["question_id"]
        episodes: list[Episode] = []
        ts = 0.0
        for session_id, session_date, session in zip(
            item["haystack_session_ids"], item["haystack_dates"], item["haystack_sessions"]
        ):
            for ti, turn in enumerate(session):
                ts += 1.0
                content = str(turn["content"]).strip()
                if not content:
                    continue
                episodes.append(
                    Episode(
                        content=f"{turn['role']}: {content}",
                        run_id=qid,
                        session_id=str(session_id),
                        kind="turn",
                        importance=1.0,
                        provenance={
                            "session_date": session_date,
                            "has_answer": bool(turn.get("has_answer", False)),
                        },
                        ts=ts,
                        id=f"{qid}:{session_id}:{ti}",
                    )
                )
        out.append(
            LongMemEvalItem(
                question_id=qid,
                question_type=item["question_type"],
                is_abstention=qid.endswith("_abs"),
                question=str(item["question"]),
                answer=str(item["answer"]),
                question_date=str(item["question_date"]),
                evidence_session_ids=tuple(str(s) for s in item["answer_session_ids"]),
                episodes=episodes,
            )
        )
    return out
