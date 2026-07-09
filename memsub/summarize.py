"""Multi-granularity memory: session-level summaries as first-class episodes.

Extension #1 from docs/extensions.md (reprioritized by orchestrator review,
2026-07-09): the weakest measured cells (multi-hop, open-domain) are
aggregation-shaped questions that fact-level, turn-level retrieval
structurally misses (a single raw turn rarely contains the whole answer).
A session summary is a short, information-dense note covering an entire
session; storing it as a normal Episode (kind="summary") means it is
embedded, BM25-indexed, and retrieved through the exact same hybrid
pipeline as any other memory, and competes for token budget alongside raw
facts, exactly like any other memory.

Write-time cost only: one LLM call per session, off the interactive query
path, cacheable. Uses the same swappable LLMBackend as extraction and
consolidation (memsub.backends), so it runs free and local on ollama.
"""

from __future__ import annotations

import time
from collections import defaultdict

from .backends import LLMBackend
from .store import Episode

SUMMARY_SYSTEM = """You summarize one conversation session into a short, \
information-dense note.

Write 2-4 sentences capturing who was involved, what happened, key facts \
stated, decisions made, and topics discussed. Be concrete: include names, \
dates, places, and specific details mentioned, not vague generalities.

Output ONLY the summary text, no preamble, no labels."""


def summarize_sessions(
    episodes: list[Episode],
    backend: LLMBackend,
    min_turns: int = 3,
    max_chars: int = 8000,
    importance: float = 0.9,
) -> list[Episode]:
    """Group episodes by session_id and generate one summary Episode each.

    Sessions with fewer than min_turns episodes are skipped (too little to
    aggregate). Does not mutate or consume the input episodes; the caller
    decides whether to merge the returned summaries into the indexed set.
    """
    by_session: dict[str, list[Episode]] = defaultdict(list)
    for ep in episodes:
        by_session[ep.session_id].append(ep)

    summaries: list[Episode] = []
    for session_id, group in by_session.items():
        if len(group) < min_turns:
            continue
        group = sorted(group, key=lambda e: e.ts)
        transcript = "\n".join(e.content for e in group)[:max_chars]
        summary_text = backend.complete(
            SUMMARY_SYSTEM, [{"role": "user", "content": transcript}]
        ).strip()
        if not summary_text:
            continue
        first, last = group[0], group[-1]
        summaries.append(
            Episode(
                content=summary_text,
                run_id=first.run_id,
                session_id=session_id,
                kind="summary",
                importance=importance,
                provenance={
                    "origin": "session_summary",
                    "source_session": session_id,
                    "n_source_turns": len(group),
                    "backend": backend.name,
                    "model": backend.model,
                    "summarized_at": time.time(),
                },
                ts=last.ts,
                namespace=first.namespace,
            )
        )
    return summaries
