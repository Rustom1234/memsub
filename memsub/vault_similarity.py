"""Layer 3: connections (the serendipity engine). Pure TF-IDF cosine
similarity over note body text, computed once at server-start index-build
time -- no embeddings, no LLM calls, ever, at query or build time.

Honesty note (WORKLOG.md 2026-07-20 assumption 3): the task's brief prefers
"existing embeddings if accessible read-only"; checked first -- memsub's
SQLite schema has no persisted per-episode embedding, only query-time
embedding in memsub/embed.py, so this module is genuinely TF-IDF, stated as
such everywhere it's surfaced in the UI, never called "embeddings."

Similarity is computed only over each note's `## Content` section (the
actual memory text), never provenance/frontmatter noise, via
memsub.vault_notes.split_note_sections.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .vault_notes import is_content_note, note_title, split_note_sections

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z'-]{2,}")

# A short, generic English stopword list -- stdlib has none built in. Purely
# to keep the TF-IDF signal on content words; not exhaustive by design.
_STOPWORDS = frozenset("""
the a an and or but if then else for of to in on at by with from as is are
was were be been being this that these those it its it's i you he she we
they them his her their our your my me him us not no yes do does did doing
have has had having will would could should can may might must shall about
into over under again further out up down off just also than so such very
what which who whom when where why how all each few more most other some
only own same too own s t can will just don should now
""".split())


@dataclass
class SimilarityIndex:
    # relpath -> top-K unlinked-similar (relpath, score, shared_terms)
    unlinked_neighbors: dict[str, list[tuple[str, float, list[str]]]] = field(default_factory=dict)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOPWORDS]


def _content_text(relpath: str, idx) -> str:
    text = idx.notes.get(relpath, "")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            text = text[nl + 1 :] if nl != -1 else ""
    sections = split_note_sections(text)
    return sections.content or text


def build_similarity_index(
    idx,
    linked_sets: dict[str, set[str]],
    top_k: int = 5,
    max_df_ratio: float = 0.15,
    candidate_pool: int = 20,
) -> SimilarityIndex:
    """Build unit-normalized TF-IDF vectors, an inverted postings index
    pruned to terms with document frequency in [2, max_df_ratio * N] (a
    term in exactly one doc can never contribute to a pairwise score; a
    term in most docs is stopword-like and contributes near-zero idf while
    still costing comparison time), then for every note keep the top
    `candidate_pool` scoring neighbors and filter out anything in
    linked_sets[relpath] before capping to top_k. One-time cost at server
    start; note pages do an O(1) dict lookup afterward."""
    # Structural pages (index.md, review-queue.md, templates) are excluded --
    # same "index.md links to every note" finding vault_graph.py's docstring
    # explains; here it would also flood the TF-IDF corpus with a single
    # giant degenerate document of wikilink slugs.
    relpaths = [rel for rel in idx.notes if is_content_note(rel, idx)]
    n_docs = len(relpaths)
    term_counts: dict[str, Counter] = {}
    df: Counter = Counter()

    for rel in relpaths:
        tokens = _tokenize(_content_text(rel, idx))
        counts = Counter(tokens)
        term_counts[rel] = counts
        for term in counts:
            df[term] += 1

    max_df = max(2, int(max_df_ratio * n_docs)) if n_docs else 2
    kept_terms = {t for t, d in df.items() if 2 <= d <= max_df}

    vectors: dict[str, dict[str, float]] = {}
    for rel, counts in term_counts.items():
        vec = {}
        for term in kept_terms & counts.keys():
            tf = counts[term]
            idf = math.log(n_docs / df[term]) if df[term] else 0.0
            w = (1 + math.log(tf)) * idf
            if w > 0:
                vec[term] = w
        norm = math.sqrt(sum(w * w for w in vec.values()))
        if norm > 0:
            vectors[rel] = {t: w / norm for t, w in vec.items()}
        else:
            vectors[rel] = {}

    postings: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for rel, vec in vectors.items():
        for term, w in vec.items():
            postings[term].append((rel, w))

    result = SimilarityIndex()
    for rel in relpaths:
        vec = vectors.get(rel, {})
        if not vec:
            result.unlinked_neighbors[rel] = []
            continue
        scores: Counter = Counter()
        for term, w in vec.items():
            for other, ow in postings.get(term, ()):
                if other != rel:
                    scores[other] += w * ow
        ranked = scores.most_common(candidate_pool + len(linked_sets.get(rel, set())))
        linked = linked_sets.get(rel, set())
        picked: list[tuple[str, float, list[str]]] = []
        for other, score in ranked:
            if other in linked or score <= 0:
                continue
            shared = _top_shared_terms(vec, vectors.get(other, {}))
            picked.append((other, round(score, 4), shared))
            if len(picked) >= top_k:
                break
        result.unlinked_neighbors[rel] = picked
    return result


def _top_shared_terms(a: dict[str, float], b: dict[str, float], n: int = 4) -> list[str]:
    shared = {t: a[t] * b[t] for t in a.keys() & b.keys()}
    return [t for t, _ in sorted(shared.items(), key=lambda kv: -kv[1])[:n]]


def related_ideas(relpath: str, sim_index: SimilarityIndex, idx) -> list[dict]:
    """JSON-ready "similar but unlinked" list for one note's related-ideas
    panel: title (prose, via note_title), href, one-line why."""
    out = []
    for other, score, shared_terms in sim_index.unlinked_neighbors.get(relpath, []):
        out.append({
            "relpath": other,
            "title": note_title(other, idx),
            "score": score,
            "why": ", ".join(shared_terms) if shared_terms else "",
        })
    return out


def connection_of_the_day(sim_index: SimilarityIndex, idx, date_str: str, pool_size: int = 50) -> dict | None:
    """Deterministic date-seeded pick of a high-similarity unlinked pair.
    Pool: the union of every note's precomputed top unlinked neighbor,
    deduplicated by unordered pair, top `pool_size` by score globally. Same
    pair all day (keyed on date_str, e.g. "2026-07-20"); no LLM, no
    per-request recomputation beyond a deterministic pick from an
    already-built pool."""
    seen_pairs: dict[tuple[str, str], tuple[float, list[str]]] = {}
    for rel, neighbors in sim_index.unlinked_neighbors.items():
        for other, score, shared in neighbors:
            key = tuple(sorted((rel, other)))
            if key not in seen_pairs or score > seen_pairs[key][0]:
                seen_pairs[key] = (score, shared)
    if not seen_pairs:
        return None
    pool = sorted(seen_pairs.items(), key=lambda kv: -kv[1][0])[:pool_size]
    (a, b), (score, shared) = random.Random(date_str).choice(pool)
    return {
        "a": {"relpath": a, "title": note_title(a, idx)},
        "b": {"relpath": b, "title": note_title(b, idx)},
        "score": round(score, 4),
        "why": ", ".join(shared) if shared else "shared vocabulary",
    }
