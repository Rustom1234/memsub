""""Ask your brain": question -> hybrid retrieval -> evidence sufficiency
gate -> grounded local-LLM answer with mandatory per-claim citations.

Read-only surface (docs/second-brain-spec-2026-07-20.md, this round's task
brief): imports `memsub.sdk.MemoryClient` (public retrieval API) and
`memsub.backends` (local LLM backends) but never touches `memsub/cli.py`,
`memsub/distill.py`, `memsub/rerank.py`, `memsub/hybrid.py`, or
`memsub/answer_policy.py` -- concurrent agents own those this round.

The sufficiency gate below is deliberately self-contained rather than
importing `answer_policy.AbstentionPolicy`: that module's calibrated
logistic model is the *better* answer to "is this evidence enough", but
importing it here mid-round would race the agent that owns it. Once it
lands, replacing `_sufficiency_gate` with a call into it is a one-line
future merge -- noted, not forgotten.

Everything here is a plain function over plain data (no server-framework
imports), so `answer()` is testable with a `MockBackend` and an in-memory
`MemoryClient`, independent of `memsub/vault_serve.py`.
"""

from __future__ import annotations

import html
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from .backends import LLMBackend, strip_think_blocks
from .lexical import tokenize
from .sdk import MemoryClient, SearchHit
from .vault import namespace_dirname, note_filename
from .vault_notes import note_title

if TYPE_CHECKING:  # pragma: no cover -- type-only, avoids a runtime circular import
    from .vault_serve import VaultIndex

DEFAULT_K = 8
DEFAULT_BUDGET_TOKENS = 1200

# Sufficiency gate thresholds. Hand-tuned against the 6 real questions run
# in this round (see WORKLOG.md "Ask your brain" entry for the log): a
# genuinely-covered question's top-8 evidence pool contains at least a third
# of the question's own (stemmed, stopworded) content words, and at least
# half of any proper-noun-looking phrases the question names explicitly.
MIN_TERM_COVERAGE = 0.34
MIN_TERM_MATCHES = 2  # absolute floor, capped by query length -- see _sufficiency_gate
MIN_ENTITY_COVERAGE = 0.5
NEAREST_COUNT = 3

NO_EVIDENCE_MESSAGE = "Your brain does not contain enough on this yet."

_CITATION_RE = re.compile(r"\[(\d+)\]")
_ENTITY_RE = re.compile(r"[A-Z][a-zA-Z0-9]{2,}(?:\s+[A-Z][a-zA-Z0-9]{2,})*")
# A handful of preamble openers observed live from qwen3:8b once <think>
# blocks are stripped; trimmed so the rendered answer starts on content.
_PREAMBLE_RE = re.compile(
    r"^(Based on (the |your )?(provided |available )?memories,?\s*"
    r"|According to (the |your )?(provided |available )?memories,?\s*"
    r"|Here('s| is) what (I|your brain) (found|knows?):?\s*)",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You are answering a question using ONLY the numbered memories given below. These are personal notes from a real person's second brain, not general knowledge, and not something you already know.

Rules, all mandatory:
1. Use ONLY the provided memories. Never use outside knowledge. Never invent a fact, a date, a name, or a detail that is not in the memories.
2. Every sentence that states a fact must end with the citation marker(s) of the memory it came from, written like [1] or [2][4]. Do not write [1, 2] or (1) -- only the bracket form, one bracket per number.
3. If the memories only partially answer the question, answer the part they support and plainly say what is missing. Never guess at the missing part.
4. Do not add a "Sources" or "References" section of your own -- citations are inline only, the caller renders sources separately.
5. Do not include reasoning, preambles, or meta-commentary such as "Based on the memories provided" or "As an AI". Answer directly, in plain prose.
6. Keep the answer to 2-6 sentences unless the question genuinely needs a list.
7. The numbered memories are retrieved by keyword match and may include some that are about a different project or topic than the question, even though they share a word or two. Before citing a memory, check that it is actually about the same subject as the question. If a memory is not really about the question's subject, leave it out of your answer entirely -- do not blend it into a sentence about a different memory just because it was in the list."""


@dataclass
class Evidence:
    """One retrieved memory, numbered for citation (`index`, 1-based)."""

    index: int
    episode_id: str
    content: str
    score: float
    relpath: str | None  # vault note path if this episode has a synced note
    title: str


def _relpath_for_episode(ep) -> str:
    return f"{namespace_dirname(ep.namespace)}/{note_filename(ep)}"


def _fallback_title(content: str, max_len: int = 120) -> str:
    text = " ".join(content.split())
    m = re.search(r"[.!?](?:\s|$)", text)
    title = text[: m.end()].strip() if m else text
    return title if len(title) <= max_len else title[:max_len].rstrip() + "..."


def build_evidence(hits: list[SearchHit], idx: "VaultIndex | None") -> list[Evidence]:
    """Merged/sorted search hits -> numbered, title-resolved evidence."""
    out = []
    for i, hit in enumerate(hits, start=1):
        ep = hit.episode
        relpath = _relpath_for_episode(ep)
        if idx is not None and relpath in idx.notes:
            title = note_title(relpath, idx)
        else:
            relpath = None
            title = _fallback_title(ep.content)
        out.append(
            Evidence(
                index=i,
                episode_id=ep.id,
                content=ep.content,
                score=hit.score,
                relpath=relpath,
                title=title,
            )
        )
    return out


def merged_search(
    question: str,
    clients: Sequence[MemoryClient],
    k: int = DEFAULT_K,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
) -> list[SearchHit]:
    """Search every client (one per known namespace), merge by score, cap
    to k overall. Dedupes by episode id in case the same episode is somehow
    reachable from more than one client."""
    seen: dict[str, SearchHit] = {}
    for client in clients:
        for hit in client.search(question, k=k, budget_tokens=budget_tokens):
            existing = seen.get(hit.episode.id)
            if existing is None or hit.score > existing.score:
                seen[hit.episode.id] = hit
    ranked = sorted(seen.values(), key=lambda h: -h.score)
    return ranked[:k]


def _extract_query_entities(question: str) -> list[str]:
    """Capitalized-phrase proxy for "named things the question mentions"
    (people, projects, places). Drops a sentence-initial capital that is
    just ordinary capitalization, not a name, by discarding a solo first
    match at position 0 unless it is a multi-word phrase (a lone
    capitalized first word is usually just "What"/"How"/etc.)."""
    matches = [m.group(0) for m in _ENTITY_RE.finditer(question)]
    if matches and matches[0] == question.split()[0].strip("?.,!") and " " not in matches[0]:
        matches = matches[1:]
    seen: list[str] = []
    for m in matches:
        if m not in seen:
            seen.append(m)
    return seen


def _sufficiency_gate(question: str, evidence: list[Evidence]) -> dict:
    """Self-contained evidence-sufficiency check (see module docstring for
    why this doesn't import answer_policy.AbstentionPolicy). Returns a
    debug dict so callers/tests can see exactly why a gate decision was
    made, not just the bool."""
    query_terms = set(tokenize(question))
    evidence_text = "\n".join(e.content for e in evidence)
    evidence_terms = set(tokenize(evidence_text))
    matched_terms = query_terms & evidence_terms
    term_coverage = len(matched_terms) / len(query_terms) if query_terms else 0.0
    # A fraction alone lets one coincidental word overlap "cover" a short
    # query (e.g. 1 of 2 content words matching by chance still clears a
    # 0.34 fraction bar) -- found live during testing (see WORKLOG.md), so
    # also require an absolute floor of matched terms, capped by how many
    # the query even has.
    required_matches = min(MIN_TERM_MATCHES, len(query_terms))
    entities = _extract_query_entities(question)
    if entities:
        lowered = evidence_text.lower()
        hit_entities = [e for e in entities if e.lower() in lowered]
        entity_coverage = len(hit_entities) / len(entities)
    else:
        entity_coverage = 1.0  # no named entities to miss -- neutral, not a penalty
    top_score = evidence[0].score if evidence else 0.0
    sufficient = (
        bool(evidence)
        and top_score > 0
        and term_coverage >= MIN_TERM_COVERAGE
        and len(matched_terms) >= required_matches
        and entity_coverage >= MIN_ENTITY_COVERAGE
    )
    return {
        "sufficient": sufficient,
        "term_coverage": round(term_coverage, 3),
        "entity_coverage": round(entity_coverage, 3),
        "entities_checked": entities,
        "top_score": top_score,
    }


def _evidence_prompt_block(evidence: list[Evidence]) -> str:
    return "\n".join(f"[{e.index}] {e.content}" for e in evidence)


def render_answer_html(answer_text: str, evidence: list[Evidence]) -> dict:
    """Escapes the model's answer, turns [n] markers into citation chips
    linking to #src-n, and reports which indices were actually cited (in
    first-citation order) plus any out-of-range indices (a possible
    hallucinated citation -- surfaced, never silently dropped)."""
    valid = {e.index for e in evidence}
    cited: list[int] = []
    invalid: list[int] = []
    escaped = html.escape(answer_text)

    def _sub(m: re.Match) -> str:
        n = int(m.group(1))
        if n not in valid:
            if n not in invalid:
                invalid.append(n)
            return m.group(0)
        if n not in cited:
            cited.append(n)
        return f'<a class="cite-chip" href="#src-{n}" title="{html.escape(_title_for(evidence, n))}">[{n}]</a>'

    answer_html = _CITATION_RE.sub(_sub, escaped)
    return {"answer_html": answer_html, "cited": cited, "invalid_citations": invalid}


def _title_for(evidence: list[Evidence], index: int) -> str:
    for e in evidence:
        if e.index == index:
            return e.title
    return ""


def render_sources_html(evidence: list[Evidence], indices: list[int]) -> str:
    """Ordered <ol> of the cited evidence items, id="src-n" anchors for the
    citation chips to jump to. Links to /note/<relpath> when the episode
    has a synced note; plain text (no link) otherwise."""
    items = []
    for n in indices:
        e = next((ev for ev in evidence if ev.index == n), None)
        if e is None:
            continue
        title = html.escape(e.title)
        if e.relpath:
            href = "/note/" + urllib.parse.quote(e.relpath)
            title_html = f'<a href="{href}">{title}</a>'
        else:
            title_html = f'{title} <span class="chip" title="not yet vault-synced">unsynced</span>'
        items.append(f'<li id="src-{n}">{title_html}</li>')
    return f'<ol class="ask-sources">{"".join(items)}</ol>' if items else ""


def render_nearest_html(evidence: list[Evidence], n: int = NEAREST_COUNT) -> str:
    """The honest-refusal case: up to n nearest notes, plainly labeled
    maybe-related, not cited as an answer."""
    items = []
    for e in evidence[:n]:
        title = html.escape(e.title)
        if e.relpath:
            href = "/note/" + urllib.parse.quote(e.relpath)
            title_html = f'<a href="{href}">{title}</a>'
        else:
            title_html = f'{title} <span class="chip" title="not yet vault-synced">unsynced</span>'
        items.append(f"<li>{title_html}</li>")
    return f'<ul class="ask-nearest">{"".join(items)}</ul>' if items else ""


def _clean_answer_text(raw: str) -> str:
    text = strip_think_blocks(raw)
    text = _PREAMBLE_RE.sub("", text).strip()
    return text


def answer(
    question: str,
    clients: Sequence[MemoryClient],
    idx: "VaultIndex | None",
    backend: LLMBackend | None,
    k: int = DEFAULT_K,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
) -> dict:
    """The full ask flow. Returns a plain, JSON-ready dict:

    {question, sufficient, gate, latency_ms, backend,
     answer_html, sources_html, cited, invalid_citations,   # sufficient path
     message, nearest_html,                                  # refusal path
     error}                                                   # backend failure
    """
    started = time.monotonic()
    question = question.strip()
    result: dict = {"question": question, "backend": getattr(backend, "name", None)}

    if not question:
        result.update(
            sufficient=False,
            gate=None,
            message=NO_EVIDENCE_MESSAGE,
            nearest_html="",
            latency_ms=0,
        )
        return result

    hits = merged_search(question, clients, k=k, budget_tokens=budget_tokens)
    evidence = build_evidence(hits, idx)
    gate = _sufficiency_gate(question, evidence)
    result["gate"] = gate

    if not gate["sufficient"]:
        result.update(
            sufficient=False,
            message=NO_EVIDENCE_MESSAGE,
            nearest_html=render_nearest_html(evidence),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    if backend is None:
        result.update(
            sufficient=True,
            error="local model (ollama) is not reachable -- start it and try again",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    try:
        raw = backend.complete(
            SYSTEM_PROMPT,
            [{"role": "user", "content": f"Memories:\n{_evidence_prompt_block(evidence)}\n\nQuestion: {question}"}],
        )
    except Exception as exc:  # backend/network failure -- never crash the request
        result.update(
            sufficient=True,
            error=f"local model call failed: {exc}",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    cleaned = _clean_answer_text(raw)
    rendered = render_answer_html(cleaned, evidence)
    result.update(
        sufficient=True,
        answer_html=rendered["answer_html"],
        cited=rendered["cited"],
        invalid_citations=rendered["invalid_citations"],
        sources_html=render_sources_html(evidence, rendered["cited"]),
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    return result
