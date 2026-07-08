"""Extraction: turn a raw conversation transcript into candidate memories.

Input: a transcript file, either JSONL (one message object per line, several
common shapes accepted) or plain text. Output: a list of CandidateMemory
objects, each carrying the extracted content, a kind tag, a confidence score,
and full provenance (source file, turn span, extractor backend and model,
extraction timestamp). Candidates can then be appended to an EpisodicStore.

The LLM call goes through the swappable backend interface (memsub.backends),
so tests run against MockBackend with zero cost.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backends import LLMBackend
from .store import Episode

VALID_KINDS = ("fact", "preference", "decision", "event")

EXTRACTION_SYSTEM = """You extract durable memories from a conversation transcript.

A durable memory is something worth remembering across future sessions:
stable facts about the user, their environment, tools, or projects;
stated preferences; decisions made and their reasons; significant events.

Do NOT extract: transient chit-chat, information only relevant within this
conversation, restatements of the same fact, or speculation.

Output ONLY a JSON array (no prose, no code fences). Each element:
{"content": "<one self-contained sentence>",
 "kind": "fact" | "preference" | "decision" | "event",
 "confidence": <0.0-1.0, how certain the transcript supports this>}

If nothing is worth remembering, output []."""


@dataclass(frozen=True)
class Turn:
    """One message in a transcript."""

    role: str
    content: str
    ts: float = 0.0
    index: int = 0


@dataclass(frozen=True)
class CandidateMemory:
    """A memory proposed by extraction, before it is written to a store."""

    content: str
    kind: str
    confidence: float
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_episode(self, run_id: str, session_id: str) -> Episode:
        return Episode(
            content=self.content,
            run_id=run_id,
            session_id=session_id,
            kind=self.kind,
            importance=max(self.confidence, 0.05),
            provenance=self.provenance,
        )


# --------------------------------------------------------------------------- #
# Transcript parsing
# --------------------------------------------------------------------------- #
def load_transcript(path: str | Path) -> list[Turn]:
    """Parse a transcript file into turns.

    JSONL (.jsonl): accepts {"role","content"} messages, Claude Code style
    {"type","message":{"role","content"}} lines (content may be a string or a
    list of {"type":"text","text":...} blocks), and skips lines that carry no
    message text. Plain text (anything else): each non-empty paragraph becomes
    one turn with role "unknown".
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".jsonl":
        return _parse_jsonl(text)
    return _parse_plain(text)


def _parse_jsonl(text: str) -> list[Turn]:
    turns: list[Turn] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        turn = _obj_to_turn(obj, index=len(turns))
        if turn is not None:
            turns.append(turn)
    return turns


def _obj_to_turn(obj: Any, index: int) -> Turn | None:
    if not isinstance(obj, dict):
        return None
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
    role = str(msg.get("role") or obj.get("type") or "unknown")
    content = msg.get("content")
    if isinstance(content, list):
        content = "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    if not isinstance(content, str) or not content.strip():
        return None
    ts = float(obj.get("ts") or 0.0)
    if not ts and obj.get("timestamp"):
        try:
            from datetime import datetime

            ts = datetime.fromisoformat(str(obj["timestamp"]).replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = 0.0
    return Turn(role=role, content=content.strip(), ts=ts, index=index)


def _parse_plain(text: str) -> list[Turn]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return [Turn(role="unknown", content=p, index=i) for i, p in enumerate(paragraphs)]


# --------------------------------------------------------------------------- #
# Chunking and extraction
# --------------------------------------------------------------------------- #
def chunk_turns(turns: list[Turn], max_chars: int = 12000) -> list[list[Turn]]:
    """Split turns into chunks under max_chars, never splitting a turn."""
    chunks: list[list[Turn]] = []
    current: list[Turn] = []
    size = 0
    for t in turns:
        t_len = len(t.content) + len(t.role) + 4
        if current and size + t_len > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(t)
        size += t_len
    if current:
        chunks.append(current)
    return chunks


def _render_chunk(turns: list[Turn]) -> str:
    return "\n\n".join(f"[{t.role}]: {t.content}" for t in turns)


def _parse_candidates(response: str) -> list[dict[str, Any]]:
    """Parse the LLM response into raw candidate dicts. Tolerates surrounding prose."""
    start, end = response.find("["), response.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(response[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)]


def extract_memories(
    turns: list[Turn],
    backend: LLMBackend,
    source: str,
    max_chars: int = 12000,
) -> list[CandidateMemory]:
    """Run LLM extraction over a transcript, one call per chunk."""
    candidates: list[CandidateMemory] = []
    extracted_at = time.time()
    for chunk in chunk_turns(turns, max_chars=max_chars):
        response = backend.complete(
            EXTRACTION_SYSTEM,
            [{"role": "user", "content": _render_chunk(chunk)}],
        )
        span = (chunk[0].index, chunk[-1].index)
        for raw in _parse_candidates(response):
            content = str(raw.get("content", "")).strip()
            if not content:
                continue
            kind = str(raw.get("kind", "fact"))
            if kind not in VALID_KINDS:
                kind = "fact"
            try:
                confidence = min(max(float(raw.get("confidence", 0.5)), 0.0), 1.0)
            except (TypeError, ValueError):
                confidence = 0.5
            candidates.append(
                CandidateMemory(
                    content=content,
                    kind=kind,
                    confidence=confidence,
                    provenance={
                        "origin": "extraction",
                        "source": source,
                        "turn_span": list(span),
                        "backend": backend.name,
                        "model": backend.model,
                        "extracted_at": extracted_at,
                        "confidence": confidence,
                    },
                )
            )
    return candidates


def extract_file(
    path: str | Path,
    backend: LLMBackend,
    max_chars: int = 12000,
) -> list[CandidateMemory]:
    """Convenience: load a transcript file and extract candidate memories."""
    turns = load_transcript(path)
    return extract_memories(turns, backend, source=str(path), max_chars=max_chars)
