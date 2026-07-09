"""Sleep consolidation: an offline pass that replays recent memories the way
sleep consolidates memory in the brain, and produces three first-class
memory types, each with full provenance linking back to its sources:

  (a) summaries   - session-level notes (memsub.summarize.summarize_sessions)
  (b) inferences  - higher-order conclusions the raw facts imply, marked
                     inferred-not-observed (kind="inference",
                     provenance["inferred"]=True, carries a confidence).
                     Consolidation (memsub.consolidate) never lets an
                     inference silently outrank an observed memory on
                     contradiction, no matter which is newer.
  (c) links       - related-memory edges: a dependency-free entity
                     co-occurrence graph built here (offline, at sleep time)
                     rather than at query time, so multi-hop questions that
                     span sessions can be bridged by one retrievable memory.

All three are ordinary Episodes: embedded, BM25-indexed, and retrieved
through the exact same hybrid pipeline as any other memory, competing for
token budget alongside raw facts. Sleep itself is offline and can be slow;
the interactive retrieval latency budget does not apply to it.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from .backends import LLMBackend
from .store import Episode, EpisodicStore
from .summarize import summarize_sessions

SLEPT_KINDS = {"summary", "inference", "link"}  # never re-sleep on sleep's own output

INFERENCE_SYSTEM = """You draw higher-order conclusions from a set of \
conversation session summaries.

An inference is something NOT directly stated but that reasonably follows: \
a pattern across sessions, an implied preference, a likely reason behind a \
decision, a connection between separate events. Do not restate a summary as \
an inference; only include genuine higher-order conclusions.

You get numbered session summaries. Output ONLY a JSON array (no prose), up \
to 5 elements:
{"content": "<one self-contained inferred sentence>",
 "confidence": <0.0-1.0>,
 "based_on": [<session ids that support it>]}

If nothing solid follows, output []."""

_CAP_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
_ENTITY_STOP = {
    "The", "A", "An", "I", "He", "She", "They", "It", "We", "You", "This",
    "That", "These", "Those", "What", "When", "Where", "Why", "How", "Is",
    "Are", "Do", "Does", "Did", "But", "And", "So", "Then", "There",
}


@dataclass
class SleepResult:
    summaries: list[Episode] = field(default_factory=list)
    inferences: list[Episode] = field(default_factory=list)
    links: list[Episode] = field(default_factory=list)

    @property
    def all_memories(self) -> list[Episode]:
        return self.summaries + self.inferences + self.links


def _parse_json_array(response: str) -> list[dict]:
    start, end = response.find("["), response.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(response[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)]


def generate_inferences(
    summaries: list[Episode], backend: LLMBackend, max_inferences: int = 5
) -> list[Episode]:
    """One LLM call over all session summaries for one conversation/run."""
    if not summaries:
        return []
    lines = [f"[{s.session_id}]: {s.content}" for s in summaries]
    response = backend.complete(INFERENCE_SYSTEM, [{"role": "user", "content": "\n".join(lines)}])
    generated_at = time.time()
    session_ids = {s.session_id for s in summaries}
    first = summaries[0]
    last_ts = max(s.ts for s in summaries)
    inferences: list[Episode] = []
    for i, raw in enumerate(_parse_json_array(response)[:max_inferences]):
        content = str(raw.get("content", "")).strip()
        if not content:
            continue
        try:
            confidence = min(max(float(raw.get("confidence", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.5
        based_on = [str(s) for s in raw.get("based_on", []) if str(s) in session_ids]
        inferences.append(
            Episode(
                content=content,
                run_id=first.run_id,
                session_id="sleep:inference",
                kind="inference",
                importance=confidence,
                provenance={
                    "origin": "sleep_inference",
                    "inferred": True,
                    "confidence": confidence,
                    "derived_from": based_on or sorted(session_ids),
                    "backend": backend.name,
                    "model": backend.model,
                    "generated_at": generated_at,
                },
                ts=last_ts,
                namespace=first.namespace,
                id=f"inference:{first.run_id}:{i}:{round(generated_at)}",
            )
        )
    return inferences


def _extract_entities(content: str) -> set[str]:
    speaker, sep, _ = content.partition(": ")
    entities = {w for w in _CAP_RE.findall(content) if w not in _ENTITY_STOP}
    if sep and speaker and speaker[0].isupper():
        entities.add(speaker)
    return entities


def generate_links(
    episodes: list[Episode], min_sessions: int = 2, max_links: int = 10
) -> list[Episode]:
    """Dependency-free entity co-occurrence graph, built once at sleep time.

    An entity that shows up in >= min_sessions distinct sessions becomes one
    link Episode connecting those sessions, so a query touching that entity
    can retrieve a single memory that bridges sessions instead of relying on
    query-time retrieval to independently rank each session's mention high
    enough to fit the budget.
    """
    if not episodes:
        return []
    entity_sessions: dict[str, set[str]] = defaultdict(set)
    session_last_ts: dict[str, float] = {}
    for ep in episodes:
        session_last_ts[ep.session_id] = max(session_last_ts.get(ep.session_id, 0.0), ep.ts)
        for entity in _extract_entities(ep.content):
            entity_sessions[entity].add(ep.session_id)

    candidates = [(e, s) for e, s in entity_sessions.items() if len(s) >= min_sessions]
    candidates.sort(key=lambda kv: (-len(kv[1]), kv[0]))

    first = episodes[0]
    generated_at = time.time()
    links: list[Episode] = []
    for entity, sessions in candidates[:max_links]:
        session_list = sorted(sessions)
        links.append(
            Episode(
                content=f"{entity} comes up across sessions: {', '.join(session_list)}.",
                run_id=first.run_id,
                session_id="sleep:link",
                kind="link",
                importance=0.5,
                provenance={
                    "origin": "sleep_link",
                    "entity": entity,
                    "derived_from": session_list,
                    "generated_at": generated_at,
                },
                ts=max(session_last_ts[s] for s in sessions),
                namespace=first.namespace,
                id=f"link:{first.run_id}:{entity}",
            )
        )
    return links


def sleep(
    episodes: list[Episode],
    backend: LLMBackend,
    min_session_turns: int = 3,
    max_inferences: int = 5,
    min_link_sessions: int = 2,
    max_links: int = 10,
) -> SleepResult:
    """Run the full sleep pass over a batch of episodes (typically: memories
    written since the last sleep run). Does not mutate or consume the input;
    the caller decides whether/how to merge the result into the indexed set
    and, for a real store, how to mark the source episodes as slept."""
    summaries = summarize_sessions(episodes, backend, min_turns=min_session_turns)
    inferences = generate_inferences(summaries, backend, max_inferences=max_inferences)
    links = generate_links(episodes, min_sessions=min_link_sessions, max_links=max_links)
    return SleepResult(summaries=summaries, inferences=inferences, links=links)


def sleep_store(
    store: EpisodicStore,
    backend: LLMBackend,
    run_id: str | None = None,
    namespace: str | None = None,
    dry_run: bool = False,
    **sleep_kwargs,
) -> SleepResult:
    """Incremental, idempotent sleep over a real store: only active episodes
    that have never been through sleep before (no "slept" event, and not
    themselves sleep output) are replayed. Marks each source episode "slept"
    and appends the new summary/inference/link episodes, unless dry_run."""
    already_slept = {e.episode_id for e in store.events(event="slept")}
    candidates = [
        ep for ep in store.active(run_id=run_id, namespace=namespace)
        if ep.id not in already_slept and ep.kind not in SLEPT_KINDS
    ]
    result = sleep(candidates, backend, **sleep_kwargs)
    if not dry_run:
        for ep in candidates:
            store.log_event(ep.id, "slept", {"n_source": len(candidates)})
        for m in result.all_memories:
            store.append(m)
    return result
