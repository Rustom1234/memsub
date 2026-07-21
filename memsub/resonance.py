"""Idea resonance: drop a new idea, get back distant-but-resonant memories.

    python -m memsub.resonance "your idea text"

The point is not "find the most similar memory" (that is just search). The
point is a *surprising* connection: a memory similar enough to genuinely
relate to the new idea, but pulled from a different topic/entity cluster
than the obvious, nearest match -- something the person might have
forgotten they already thought about, from an angle they were not
expecting.

Pipeline, three steps:

1. Score every active episode by cosine similarity between an embedding of
   the idea and an embedding of the episode's content (memsub/embed.py's
   existing Embedder protocol -- no new embedding machinery).
2. Distance selection (`find_resonances`, pure, no LLM, stub-testable):
   above a resonance floor, group candidates by a cheap topic proxy (the
   episode's most specific linked entity -- memsub/graph.py's existing
   entities table, read-only), throw out whichever cluster the single
   closest match belongs to (that is the "obvious" connection, not a
   surprising one), then keep the best-scoring episode from each of the
   remaining clusters, capped at `top_n`.
3. One local LLM call per surviving match (`generate_bridge`, qwen3:8b by
   default via memsub.backends) to write 2-3 sentences on *why* the two
   might connect.

Honesty rule: if nothing clears the resonance floor, or everything that
does shares the top match's cluster (no distinct angle survives), the
result says "nothing resonates yet" -- never a fabricated connection.

The idea itself is persisted through the same public write path every
other memsub caller uses (memsub.sdk.MemoryClient.add, channel
"user_stated" -- the person just directly stated it), so next time it is
itself a memory other ideas can resonate against.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

import numpy as np

from .backends import LLMBackend, build_backend, strip_think_blocks
from .embed import Embedder, build_embedder
from .sdk import MemoryClient
from .store import Episode, EpisodicStore

# Hashing-embedder cosine scores over a real demo vault (single-namespace,
# one person's notes) run 0.01-0.40, mean ~0.21, median ~0.21 -- most
# content in a small single-topic vault scores moderately against any
# on-topic idea just from shared vocabulary (the person's own name, the
# project's name). The floor only needs to reject genuine noise; distance
# is enforced by cluster exclusion below, not by the floor. See
# WORKLOG.md 2026-07-21 "Idea resonance" for the calibration run.
DEFAULT_FLOOR = 0.15
DEFAULT_TOP_N = 3
# Only the top-scoring CANDIDATE_POOL episodes get an entity lookup (bounded
# cost regardless of store size -- entities_for_episode is one query per
# episode, and a real vault can have thousands of active episodes).
CANDIDATE_POOL = 100

NOTHING_MESSAGE = "Nothing resonates yet."

BRIDGE_SYSTEM = """You connect a brand new idea to an old, distant memory from the same person's second brain. In 2-3 sentences, explain a genuine, specific bridge between them: what pattern, principle, or feeling links them, even though they are about different things on the surface. Start your answer with "These connect because". Do not restate either text verbatim. Do not invent facts that are not present in either text. Be concrete and specific, not generic filler. Never use an em dash (the long "--" or "—" character); use a comma, a period, or a new sentence instead."""


@dataclass
class ResonanceMatch:
    """One surfaced memory: the episode, its raw similarity score, the
    cluster key it was grouped under (for tests/debugging), and the
    LLM-written bridge (empty until `resonate`/`generate_bridge` fills it
    in -- `find_resonances` alone never calls an LLM)."""

    episode: Episode
    score: float
    cluster_key: str
    bridge: str = ""


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _cluster_key(store: EpisodicStore, ep: Episode) -> str:
    """A cheap topic proxy for one episode: its most specific linked entity
    (lowest degree = fewest other episodes it also links to, so a generic
    entity like the vault owner's own name -- linked to nearly everything
    -- never dominates the grouping), falling back to namespace+kind for an
    episode with no graph links yet (graph backfill has not run, or the
    episode predates it)."""
    entities = store.entities_for_episode(ep.id)
    if not entities:
        return f"{ep.namespace}:{ep.kind}"
    degrees = [(len(store.episode_ids_for_entities([e.id])), e.id) for e in entities]
    degrees.sort()
    return "entity:" + degrees[0][1]


def find_resonances(
    idea_text: str,
    store: EpisodicStore,
    embedder: Embedder,
    namespace: str | None = None,
    floor: float = DEFAULT_FLOOR,
    top_n: int = DEFAULT_TOP_N,
    candidate_pool: int = CANDIDATE_POOL,
) -> list[ResonanceMatch]:
    """Pure distance-selection logic: no LLM call, no persistence. Returns
    up to `top_n` matches, each from a distinct cluster, none sharing the
    single closest match's cluster, all scoring at or above `floor`.
    Returns [] (never raises) on an empty idea, an empty store, nothing
    clearing the floor, or everything clearing the floor sharing one
    cluster -- the honest "nothing resonates yet" cases."""
    idea_text = idea_text.strip()
    if not idea_text:
        return []
    active = store.active(namespace=namespace)
    if not active:
        return []

    idea_vec = embedder.embed(idea_text)
    scored = [(ep, cosine(idea_vec, embedder.embed(ep.content))) for ep in active]
    scored = [(ep, s) for ep, s in scored if s >= floor]
    if not scored:
        return []
    scored.sort(key=lambda pair: -pair[1])
    pool = scored[:candidate_pool]

    keyed = [(ep, score, _cluster_key(store, ep)) for ep, score in pool]
    near_cluster = keyed[0][2]
    distant = [t for t in keyed if t[2] != near_cluster]
    if not distant:
        return []  # every resonant memory is the same obvious topic -- no surprise

    best_per_cluster: dict[str, tuple[Episode, float, str]] = {}
    for ep, score, key in distant:
        if key not in best_per_cluster or score > best_per_cluster[key][1]:
            best_per_cluster[key] = (ep, score, key)

    ranked = sorted(best_per_cluster.values(), key=lambda t: -t[1])[:top_n]
    return [ResonanceMatch(episode=ep, score=score, cluster_key=key) for ep, score, key in ranked]


def generate_bridge(backend: LLMBackend, idea_text: str, memory_content: str) -> str:
    """One LLM call: 2-3 sentences on why idea_text and memory_content might
    connect. Raises on backend failure -- callers (resonate/route/main)
    decide how to degrade, this stays a thin, testable wrapper."""
    raw = backend.complete(
        BRIDGE_SYSTEM,
        [{"role": "user", "content": f"New idea: {idea_text}\n\nOld memory: {memory_content}"}],
    )
    cleaned = strip_think_blocks(raw).strip()
    # Belt-and-suspenders on top of the system prompt's instruction: a local
    # 8B model does not reliably follow a style rule 100% of the time, and
    # this text renders straight into the product UI.
    return cleaned.replace("—", ", ").replace(" -- ", ", ")


@dataclass
class ResonanceResult:
    idea: str
    matches: list[ResonanceMatch] = field(default_factory=list)
    message: str = ""
    bridge_error: str = ""


def resonate(
    idea_text: str,
    store: EpisodicStore,
    embedder: Embedder,
    backend: LLMBackend | None,
    namespace: str | None = None,
    floor: float = DEFAULT_FLOOR,
    top_n: int = DEFAULT_TOP_N,
) -> ResonanceResult:
    """find_resonances() plus one bridge call per match. Never raises: a
    backend failure or absence is reported in `bridge_error`, matches are
    still returned (with empty bridges) rather than dropped."""
    idea_text = idea_text.strip()
    matches = find_resonances(idea_text, store, embedder, namespace=namespace, floor=floor, top_n=top_n)
    if not matches:
        return ResonanceResult(idea=idea_text, matches=[], message=NOTHING_MESSAGE)

    result = ResonanceResult(idea=idea_text, matches=matches)
    if backend is None:
        result.bridge_error = "local model (ollama) is not reachable -- start it and try again"
        return result
    for m in matches:
        try:
            m.bridge = generate_bridge(backend, idea_text, m.episode.content)
        except Exception as exc:  # backend/network failure -- never crash the request
            result.bridge_error = f"local model call failed: {exc}"
            break
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m memsub.resonance",
        description="Drop an idea, get back distant-but-resonant memories with an AI-written bridge.",
    )
    parser.add_argument("idea", nargs="+", help="the idea text")
    parser.add_argument("--db", default=None, help="path to memory.db (default: ~/.memsub/memory.db)")
    parser.add_argument("--namespace", default=None, help="namespace to write/search (default: MemoryClient default)")
    parser.add_argument("--search-namespace", default=None, help="restrict matching to one namespace (default: all)")
    parser.add_argument("--floor", type=float, default=DEFAULT_FLOOR)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--embedder", default="auto", choices=["auto", "ollama", "hashing"])
    parser.add_argument("--no-persist", action="store_true", help="don't write the idea as a memory")
    args = parser.parse_args(argv)
    idea_text = " ".join(args.idea).strip()

    client = MemoryClient(db_path=args.db, namespace=args.namespace, embedder=args.embedder)
    try:
        backend: LLMBackend | None
        try:
            backend = build_backend(prefer="ollama", model="qwen3:8b")
        except RuntimeError as exc:
            print(f"warning: local ollama unavailable ({exc}); bridges will be skipped", file=sys.stderr)
            backend = None

        result = resonate(
            idea_text,
            client.store,
            client.embedder,
            backend,
            namespace=args.search_namespace,
            floor=args.floor,
            top_n=args.top_n,
        )

        if not args.no_persist:
            ep = client.add(idea_text, kind="idea", channel="user_stated")
            print(f"idea persisted as {ep.id[:8]} (namespace={ep.namespace})")

        if not result.matches:
            print(result.message or NOTHING_MESSAGE)
            return 0

        for m in result.matches:
            print(f"\n[{m.score:.3f}] {m.episode.content}")
            if m.bridge:
                print(f"  -> {m.bridge}")
        if result.bridge_error:
            print(f"\n(bridge generation incomplete: {result.bridge_error})", file=sys.stderr)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
