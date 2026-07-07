from memsub.embed import HashingEmbedder
from memsub.retrieval import SemanticIndex
from memsub.store import Episode


def _ep(content, session_id="1", importance=1.0):
    return Episode(content=content, run_id="r", session_id=session_id, importance=importance)


def test_embedder_deterministic():
    e = HashingEmbedder()
    v1 = e.embed("the quick brown fox")
    v2 = e.embed("the quick brown fox")
    assert (v1 == v2).all()
    assert abs(float((v1 * v1).sum()) - 1.0) < 1e-9  # normalized


def test_semantic_relevance_ranking():
    idx = SemanticIndex(recency_weight=1.0)  # disable recency to isolate similarity
    idx.add(_ep("dataset audit and baseline training completed"))
    idx.add(_ep("rendered the final figure for the paper"))
    idx.add(_ep("ablation sweep over learning rates"))
    results = idx.search("how did the ablation sweep go", k=1)
    assert results[0].episode.content.startswith("ablation sweep")


def test_recency_weighting_breaks_ties():
    idx = SemanticIndex(recency_weight=0.5)
    # identical content: only recency should differ them
    idx.add(_ep("same report text", session_id="1"))
    idx.add(_ep("same report text", session_id="2"))
    results = idx.search("same report text", k=2)
    # newest (session 2) should rank first under recency weighting
    assert results[0].episode.session_id == "2"


def test_importance_weighting():
    idx = SemanticIndex(recency_weight=1.0)
    idx.add(_ep("report", session_id="1", importance=1.0))
    idx.add(_ep("report", session_id="2", importance=3.0))
    results = idx.search("report", k=2)
    assert results[0].episode.session_id == "2"


def test_empty_index_returns_empty():
    idx = SemanticIndex()
    assert idx.search("anything") == []
