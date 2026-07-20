"""Tests for memsub/ask.py: "Ask your brain" chat panel core flow. See
docs/second-brain-spec-2026-07-20.md and WORKLOG.md's 2026-07-20 "Ask your
brain" entry for design + assumptions."""

from __future__ import annotations

from memsub.ask import (
    NO_EVIDENCE_MESSAGE,
    Evidence,
    _extract_query_entities,
    _sufficiency_gate,
    answer,
    build_evidence,
    merged_search,
    render_answer_html,
    render_nearest_html,
    render_sources_html,
)
from memsub.backends import MockBackend
from memsub.sdk import MemoryClient


def _client(namespace: str = "test") -> MemoryClient:
    return MemoryClient(db_path=":memory:", namespace=namespace, retrieval="hybrid")


# --------------------------------------------------------------------------- #
# Sufficiency gate
# --------------------------------------------------------------------------- #
def test_gate_sufficient_when_terms_and_entities_covered():
    client = _client()
    client.add("Rustom decided to use qwen3:8b as the local backend for memsub.")
    hits = client.search("what backend did I decide to use for memsub")
    evidence = build_evidence(hits, None)
    gate = _sufficiency_gate("what backend did I decide to use for memsub", evidence)
    assert gate["sufficient"] is True
    assert gate["term_coverage"] > 0.5


def test_gate_insufficient_on_empty_store():
    client = _client()
    hits = client.search("anything at all")
    evidence = build_evidence(hits, None)
    gate = _sufficiency_gate("anything at all", evidence)
    assert gate["sufficient"] is False
    assert evidence == []


def test_gate_rejects_single_coincidental_word_overlap():
    """A short query where only one of its two content words happens to
    appear in unrelated evidence must not pass -- found live (see
    WORKLOG.md): "capital of France" against a note that only mentions
    "France" in an unrelated context (a betting project) is not an answer
    to the question asked."""
    client = _client()
    client.add("The France Morocco betting project has a $50 stake and a $200 profit target.")
    hits = client.search("what is the capital of France")
    evidence = build_evidence(hits, None)
    gate = _sufficiency_gate("what is the capital of France", evidence)
    assert gate["sufficient"] is False


def test_gate_penalizes_missing_named_entity():
    client = _client()
    client.add("The project timeline slipped by two weeks after a review.")
    hits = client.search("what did Melanie say about the timeline")
    evidence = build_evidence(hits, None)
    gate = _sufficiency_gate("what did Melanie say about the timeline", evidence)
    assert gate["entity_coverage"] == 0.0
    assert gate["sufficient"] is False


def test_extract_query_entities_drops_sentence_initial_capital():
    assert _extract_query_entities("What did Melanie decide about Outreach Desk") == [
        "Melanie",
        "Outreach Desk",
    ]
    assert _extract_query_entities("what happened yesterday") == []


# --------------------------------------------------------------------------- #
# merged_search / build_evidence
# --------------------------------------------------------------------------- #
def test_merged_search_dedupes_and_caps_to_k():
    client_a = _client("ns-a")
    client_a.add("Rustom picked qwen3:8b for local inference in memsub.")
    client_b = _client("ns-b")
    client_b.add("The France Morocco bet has a $50 stake.")

    hits = merged_search("qwen3:8b local inference memsub", [client_a, client_b], k=1)
    assert len(hits) == 1
    assert "qwen3:8b" in hits[0].episode.content


def test_build_evidence_numbers_from_one_and_falls_back_to_first_sentence_title():
    client = _client()
    client.add("Rustom decided to use qwen3:8b. It runs locally with zero cloud calls.")
    hits = client.search("qwen3:8b local decision")
    evidence = build_evidence(hits, None)
    assert evidence[0].index == 1
    assert evidence[0].title == "Rustom decided to use qwen3:8b."
    assert evidence[0].relpath is None  # no vault index supplied


# --------------------------------------------------------------------------- #
# Citation parsing / rendering
# --------------------------------------------------------------------------- #
def _evidence(n: int) -> list[Evidence]:
    return [
        Evidence(index=i, episode_id=f"ep{i}", content=f"content {i}", score=0.5, relpath=None, title=f"Title {i}")
        for i in range(1, n + 1)
    ]


def test_render_answer_html_converts_in_range_citation_to_chip():
    evidence = _evidence(2)
    result = render_answer_html("Rustom uses qwen3:8b. [1] It runs locally. [2]", evidence)
    assert '<a class="cite-chip" href="#src-1" title="Title 1">[1]</a>' in result["answer_html"]
    assert '<a class="cite-chip" href="#src-2" title="Title 2">[2]</a>' in result["answer_html"]
    assert result["cited"] == [1, 2]
    assert result["invalid_citations"] == []


def test_render_answer_html_leaves_out_of_range_citation_as_plain_text():
    evidence = _evidence(1)
    result = render_answer_html("This claims something. [5]", evidence)
    assert "[5]" in result["answer_html"]
    assert '<a class="cite-chip"' not in result["answer_html"]
    assert result["invalid_citations"] == [5]
    assert result["cited"] == []


def test_render_answer_html_escapes_model_output():
    evidence = _evidence(1)
    result = render_answer_html("<script>alert(1)</script> [1]", evidence)
    assert "<script>" not in result["answer_html"]
    assert "&lt;script&gt;" in result["answer_html"]


def test_render_answer_html_adjacent_citations_both_parsed():
    evidence = _evidence(2)
    result = render_answer_html("Two sources agree. [1][2]", evidence)
    assert result["cited"] == [1, 2]


def test_render_sources_html_only_includes_cited_indices():
    evidence = _evidence(3)
    html_out = render_sources_html(evidence, [2])
    assert 'id="src-2"' in html_out
    assert 'id="src-1"' not in html_out
    assert 'id="src-3"' not in html_out


def test_render_sources_html_empty_when_no_citations():
    assert render_sources_html(_evidence(2), []) == ""


def test_render_nearest_html_caps_at_three():
    html_out = render_nearest_html(_evidence(5))
    assert html_out.count("<li>") == 3


# --------------------------------------------------------------------------- #
# answer() end-to-end (MockBackend, no network)
# --------------------------------------------------------------------------- #
def test_answer_sufficient_path_returns_cited_html():
    client = _client()
    client.add("Rustom decided to use qwen3:8b as the local backend for memsub.")
    backend = MockBackend(responder=lambda s, m: "The backend is qwen3:8b. [1]")

    result = answer("what backend did I decide to use for memsub", [client], None, backend)

    assert result["sufficient"] is True
    assert "cite-chip" in result["answer_html"]
    assert result["cited"] == [1]
    assert "error" not in result or result.get("error") is None


def test_answer_insufficient_path_never_calls_backend():
    client = _client()
    calls = []
    backend = MockBackend(responder=lambda s, m: calls.append(1) or "should not be called")

    result = answer("what is the capital of France", [client], None, backend)

    assert result["sufficient"] is False
    assert result["message"] == NO_EVIDENCE_MESSAGE
    assert calls == []


def test_answer_empty_question_is_honest_refusal():
    client = _client()
    backend = MockBackend()
    result = answer("   ", [client], None, backend)
    assert result["sufficient"] is False
    assert result["message"] == NO_EVIDENCE_MESSAGE


def test_answer_backend_none_reports_error_not_crash():
    client = _client()
    client.add("Rustom decided to use qwen3:8b as the local backend for memsub.")
    result = answer("what backend did I decide to use for memsub", [client], None, None)
    assert result["sufficient"] is True
    assert "ollama" in result["error"]


def test_answer_backend_exception_is_caught_gracefully():
    client = _client()
    client.add("Rustom decided to use qwen3:8b as the local backend for memsub.")

    class ExplodingBackend:
        name = "boom"

        def complete(self, system, messages):
            raise RuntimeError("connection refused")

    result = answer("what backend did I decide to use for memsub", [client], None, ExplodingBackend())
    assert result["sufficient"] is True
    assert "connection refused" in result["error"]


def test_answer_strips_think_blocks_and_preamble():
    client = _client()
    client.add("Rustom decided to use qwen3:8b as the local backend for memsub.")
    backend = MockBackend(
        responder=lambda s, m: "<think>reasoning here</think>Based on the memories provided, the backend is qwen3:8b. [1]"
    )
    result = answer("what backend did I decide to use for memsub", [client], None, backend)
    assert "<think>" not in result["answer_html"]
    assert "Based on the memories" not in result["answer_html"]
