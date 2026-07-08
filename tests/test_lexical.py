"""BM25, RRF, and hybrid system tests."""

from memsub.bench import HybridSystem
from memsub.lexical import BM25Index, rrf_fuse, tokenize
from memsub.store import Episode


def _ep(content, ts, id):
    return Episode(content=content, run_id="r", session_id="s", kind="turn", ts=ts, id=id)


def test_tokenize_drops_stopwords():
    assert tokenize("What is the deploy port of the router?") == ["deploy", "port", "router"]


def test_bm25_ranks_rare_term_match_first():
    docs = [
        "we talked about the weather today",
        "the xylophone recital is on friday",
        "weather weather weather all week",
    ]
    idx = BM25Index(docs)
    s = idx.scores("when is the xylophone recital")
    assert s[1] > s[0] and s[1] > s[2]
    # idf: common word matches score below rare word matches
    s2 = idx.scores("weather")
    assert max(s2[0], s2[2]) < s[1]


def test_bm25_empty_query_and_corpus():
    assert BM25Index([]).scores("anything") == []
    idx = BM25Index(["some doc"])
    assert idx.scores("") == [0.0]


def test_rrf_fuse_rewards_agreement():
    # doc 2 is mid-ranked by both; doc 0 and 1 are top in one, bottom in other
    fused = rrf_fuse([[0, 2, 1], [1, 2, 0]])
    assert fused[0] == 2


def test_rrf_weights():
    fused = rrf_fuse([[0, 1], [1, 0]], weights=[10.0, 1.0])
    assert fused[0] == 0


def test_hybrid_recovers_lexical_miss():
    # dense hashing embedder is weak on this pairing; bm25 catches "harmonica"
    eps = [
        _ep("Melanie picked up the harmonica her grandfather left her.", 1, "a"),
        _ep("They discussed the weather and lunch plans at length.", 2, "b"),
        _ep("Completely unrelated chatter about gardening tools.", 3, "c"),
    ]
    hy = HybridSystem()
    hy.ingest(eps)
    packed = hy.inject("What instrument does Melanie play?", budget_tokens=30)
    assert packed and packed[0].id == "a"


def test_hybrid_rank_is_permutation():
    eps = [_ep(f"turn number {i} content", i, f"e{i}") for i in range(10)]
    hy = HybridSystem()
    hy.ingest(eps)
    order = hy.rank("turn number 3")
    assert sorted(order) == list(range(10))
