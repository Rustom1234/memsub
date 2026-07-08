"""Benchmark harness tests: determinism, budget enforcement, metrics, loaders."""

import json

from memsub.bench import (
    NaiveTopKSystem,
    PolicySystem,
    RecentFirstSystem,
    generate_recall_dataset,
    load_locomo,
    pack_greedy,
    run_locomo_benchmark,
    run_recall_benchmark,
)
from memsub.policy import approx_tokens
from memsub.store import Episode


def _ep(content, ts, importance=1.0, id=""):
    return Episode(
        content=content, run_id="r", session_id="s", kind="fact",
        importance=importance, ts=ts, id=id or f"e{ts}",
    )


# --------------------------------------------------------------------------- #
# Packing
# --------------------------------------------------------------------------- #
def test_pack_greedy_hard_budget():
    eps = [_ep("x" * 40, i) for i in range(20)]  # ~11 tokens each
    packed = pack_greedy(eps, budget_tokens=30)
    used = sum(approx_tokens(e.content) + 1 for e in packed)
    assert used <= 30
    assert len(packed) == 2


def test_pack_greedy_skips_oversize_keeps_smaller():
    eps = [_ep("y" * 400, 1, id="big"), _ep("small fact", 2, id="small")]
    packed = pack_greedy(eps, budget_tokens=20)
    assert [e.id for e in packed] == ["small"]


# --------------------------------------------------------------------------- #
# Dataset generator
# --------------------------------------------------------------------------- #
def test_generator_deterministic():
    a = generate_recall_dataset(seed=7)
    b = generate_recall_dataset(seed=7)
    assert [e.content for e in a.episodes] == [e.content for e in b.episodes]
    assert a.queries == b.queries
    c = generate_recall_dataset(seed=8)
    assert [e.content for e in a.episodes] != [e.content for e in c.episodes]


def test_generator_ground_truth_consistent():
    ds = generate_recall_dataset(seed=1, n_facts=5, n_versions=3, n_distractors=10)
    assert len(ds.queries) == 5
    assert len(ds.episodes) == 5 * 3 + 10
    by_id = {e.id: e for e in ds.episodes}
    for q in ds.queries:
        gold_ep = by_id[q.gold_episode_id]
        assert q.gold in gold_ep.content
        assert len(q.stale) == 2
        # gold version is the newest of its family
        family = [e for e in ds.episodes if e.id.startswith(q.gold_episode_id.rsplit("-", 1)[0] + "-")]
        assert gold_ep.ts == max(e.ts for e in family)


# --------------------------------------------------------------------------- #
# Systems
# --------------------------------------------------------------------------- #
def test_policy_prefers_fresh_version_naive_is_relevance_only():
    stale = _ep("The deploy port of the amber-router service is 1111.", ts=1, importance=0.9, id="v0")
    fresh = _ep("The deploy port of the amber-router service is 2222.", ts=100, importance=0.9, id="v1")
    query = "What is the deploy port of the amber-router service?"

    policy = PolicySystem(recency_weight=0.9)
    policy.ingest([stale, fresh])
    packed = policy.inject(query, budget_tokens=20)
    assert packed and packed[0].id == "v1"  # recency breaks the tie

    naive = NaiveTopKSystem()
    naive.ingest([stale, fresh])
    n_packed = naive.inject(query, budget_tokens=1000)
    assert {e.id for e in n_packed} == {"v0", "v1"}  # naive happily injects both


def test_policy_downweights_low_importance():
    chit = _ep("we chatted about the amber-router being a fun name", ts=50, importance=0.2, id="chit")
    fact = _ep("The deploy port of the amber-router service is 2222.", ts=10, importance=0.9, id="fact")
    policy = PolicySystem()
    policy.ingest([chit, fact])
    packed = policy.inject("What is the deploy port of the amber-router service?", 20)
    assert packed[0].id == "fact"


def test_recent_first_ignores_query():
    sys = RecentFirstSystem()
    sys.ingest([_ep("old relevant fact about ports", 1, id="old"), _ep("new irrelevant chatter", 2, id="new")])
    packed = sys.inject("ports?", budget_tokens=10)
    assert packed[0].id == "new"


def test_consolidated_policy_drops_exact_duplicates():
    dup1 = _ep("The deploy port of the amber-router service is 2222.", ts=1, id="d1")
    dup2 = _ep("The deploy port of the amber-router service is 2222.", ts=2, id="d2")
    sys = PolicySystem(consolidated=True)
    sys.ingest([dup1, dup2])
    packed = sys.inject("deploy port of the amber-router?", 1000)
    assert [e.id for e in packed] == ["d2"]


# --------------------------------------------------------------------------- #
# Part A end to end (small)
# --------------------------------------------------------------------------- #
def test_run_recall_benchmark_shapes_and_budget():
    rows = run_recall_benchmark(seeds=[1, 2], budgets=[50, 200])
    names = {r.system for r in rows}
    assert names == {"policy", "policy+cons", "naive-topk", "recent"}
    assert len(rows) == 8
    for r in rows:
        assert r.n == 60  # 30 queries x 2 seeds
        assert 0 <= r.recall <= 1
        assert r.avg_tokens_used <= r.budget
    # sanity: any semantic system beats blind recency stuffing at tight budgets
    by = {(r.system, r.budget): r for r in rows}
    assert by[("policy", 50)].recall > by[("recent", 50)].recall


# --------------------------------------------------------------------------- #
# LOCOMO loader + eval on a tiny fixture
# --------------------------------------------------------------------------- #
def _locomo_fixture(tmp_path):
    data = [
        {
            "sample_id": "conv-1",
            "conversation": {
                "speaker_a": "A", "speaker_b": "B",
                "session_1_date_time": "1 pm on 1 May, 2023",
                "session_1": [
                    {"speaker": "A", "dia_id": "D1:1", "text": "I adopted a dog named Biscuit."},
                    {"speaker": "B", "dia_id": "D1:2", "text": "Congrats!"},
                ],
                "session_2_date_time": "2 pm on 2 May, 2023",
                "session_2": [
                    {"speaker": "A", "dia_id": "D2:1", "text": "Biscuit chewed my headphones today."},
                ],
            },
            "qa": [
                {"question": "What is the name of A's dog?", "answer": "Biscuit",
                 "evidence": "['D1:1']", "category": "4"},
                {"question": "Did A ever fly to the moon?", "answer": "Not mentioned",
                 "evidence": [], "category": "5"},
            ],
        }
    ]
    p = tmp_path / "locomo.json"
    p.write_text(json.dumps(data))
    return p


def test_load_locomo(tmp_path):
    convs = load_locomo(_locomo_fixture(tmp_path))
    assert len(convs) == 1
    conv = convs[0]
    assert len(conv.episodes) == 3
    assert conv.episodes[0].id == "conv-1:D1:1"
    assert conv.episodes[0].content == "A: I adopted a dog named Biscuit."
    assert conv.episodes[0].ts < conv.episodes[2].ts
    assert len(conv.qa) == 2
    assert conv.qa[0].evidence == ("D1:1",)
    assert conv.qa[0].category == 4


def test_run_locomo_benchmark_skips_adversarial(tmp_path):
    convs = load_locomo(_locomo_fixture(tmp_path))
    rows = run_locomo_benchmark(convs, budgets=[100])
    assert {r.system for r in rows} == {"policy", "naive-topk", "recent"}
    for r in rows:
        assert r.n == 1  # category 5 skipped
        assert r.avg_tokens_used <= 100
    policy_row = next(r for r in rows if r.system == "policy")
    assert policy_row.recall_all == 1.0  # tiny corpus, evidence easily packed
