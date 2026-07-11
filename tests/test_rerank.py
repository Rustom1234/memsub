"""memsub.rerank: opt-in LLM rerank, with mocked backends (no ollama needed)."""

from __future__ import annotations

from memsub.backends import MockBackend
from memsub.bench import HybridSystem, RerankSystem
from memsub.rerank import llm_rerank
from memsub.store import Episode


def _episode(content: str, ts: float) -> Episode:
    return Episode(content=content, run_id="t", session_id="s", kind="fact", ts=ts)


def test_llm_rerank_reorders_on_valid_response():
    backend = MockBackend(responder=lambda s, m: "[2, 0, 1]")
    order = llm_rerank("q", ["a", "b", "c"], backend)
    assert order == [2, 0, 1]


def test_llm_rerank_falls_back_on_unparseable_response():
    backend = MockBackend(responder=lambda s, m: "not json at all")
    log: list[dict] = []
    order = llm_rerank("q", ["a", "b", "c"], backend, log=log)
    assert order == [0, 1, 2]
    assert log[0]["status"] == "fallback_unparseable"


def test_llm_rerank_completes_partial_response():
    # model only returned 2 of 3 indices (common with small local models):
    # trust the ones it gave, append the rest, never drop a candidate
    backend = MockBackend(responder=lambda s, m: "[2, 0]")
    log: list[dict] = []
    order = llm_rerank("q", ["a", "b", "c"], backend, log=log)
    assert order == [2, 0, 1]
    assert log[0]["status"] == "reranked_partial"


def test_llm_rerank_falls_back_on_backend_error():
    def boom(s, m):
        raise RuntimeError("ollama down")

    backend = MockBackend(responder=boom)
    log: list[dict] = []
    order = llm_rerank("q", ["a", "b", "c"], backend, log=log)
    assert order == [0, 1, 2]
    assert log[0]["status"] == "fallback_error"


def test_llm_rerank_empty_input():
    backend = MockBackend(responder=lambda s, m: "[]")
    assert llm_rerank("q", [], backend) == []


def test_rerank_system_never_drops_episodes_on_fallback():
    backend = MockBackend(responder=lambda s, m: "garbage")
    sys_ = RerankSystem(backend=backend, rerank_k=2)
    eps = [_episode("alpha thing", 1.0), _episode("beta thing", 2.0), _episode("gamma thing", 3.0)]
    sys_.ingest(eps)
    packed = sys_.inject("thing", budget_tokens=1000)
    assert {e.content for e in packed} == {e.content for e in eps}
    assert sys_.rerank_log and sys_.rerank_log[0]["status"] == "fallback_unparseable"


def test_rerank_system_reorders_top_k_only():
    calls = []

    def responder(system, messages):
        calls.append(messages[0]["content"])
        return "[1, 0]"

    backend = MockBackend(responder=responder)
    sys_ = RerankSystem(backend=backend, rerank_k=2)
    eps = [_episode("alpha", 1.0), _episode("beta", 2.0), _episode("gamma", 3.0)]
    sys_.ingest(eps)
    packed = sys_.inject("alpha beta gamma", budget_tokens=1000)
    assert len(packed) == 3
    assert len(calls) == 1  # one batched call, not one per candidate
