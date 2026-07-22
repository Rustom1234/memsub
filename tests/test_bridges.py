from __future__ import annotations

import http.client
import json
import threading

import pytest

from memsub.bridges import (
    Bridge,
    _project_hint,
    apply_verdicts,
    compute_bridges,
    find_bridges,
    load_cache,
    make_bridges_handler,
    pair_key,
    render_constellation_page,
    save_cache,
    set_verdict,
    _ServeState,
)
from memsub.embed import HashingEmbedder
from memsub.store import Episode, EpisodicStore

BOOKDNA_SRC = "/homes/x/.claude/projects/-homes-x-projects-book-dna/s1.jsonl"
AIRTHROW_SRC = "/homes/x/.claude/projects/-homes-x-airthrow/s2.jsonl"

# Two sentences that share the word "gesture" but are otherwise unrelated:
# moderate hashing-embedder cosine similarity, comfortably under the
# consolidate dedup threshold (0.92), so they survive the near-dup filter.
CONTENT_A = "The gesture recognition prototype uses Stark for iOS control."
CONTENT_B = "A gesture based interface could control the memory sublayer's playback."


def _add(store: EpisodicStore, content: str, session_id: str, source: str | None, **kw) -> Episode:
    provenance = {"origin": "extraction", "source": source} if source else {}
    provenance.update(kw.pop("provenance", {}))
    ep = Episode(content=content, run_id="r1", session_id=session_id, provenance=provenance, **kw)
    return store.append(ep)


def test_project_hint_extraction():
    assert _project_hint(BOOKDNA_SRC) == "book-dna"
    assert _project_hint(AIRTHROW_SRC) == "airthrow"
    assert _project_hint(None) is None
    assert _project_hint("/homes/x/.claude/projects/-homes-x/only.jsonl") is None


def test_same_bucket_excluded():
    """Two otherwise-qualifying memories from the SAME project hint never
    become a bridge, even though nothing else about them is excluded."""
    store = EpisodicStore(":memory:")
    _add(store, CONTENT_A, "s1", BOOKDNA_SRC)
    _add(store, CONTENT_B, "s2", BOOKDNA_SRC)
    bridges = find_bridges(store, HashingEmbedder())
    assert bridges == []


def test_near_duplicate_excluded():
    """Identical content from different buckets is a near-dup, not a bridge."""
    store = EpisodicStore(":memory:")
    _add(store, CONTENT_A, "s1", BOOKDNA_SRC)
    _add(store, CONTENT_A, "s2", AIRTHROW_SRC)
    bridges = find_bridges(store, HashingEmbedder())
    assert bridges == []


def test_shared_entity_excluded():
    """Two different-bucket memories linked to the same entity are excluded,
    even at a similarity that would otherwise qualify."""
    store = EpisodicStore(":memory:")
    a = _add(store, CONTENT_A, "s1", BOOKDNA_SRC)
    b = _add(store, CONTENT_B, "s2", AIRTHROW_SRC)
    store.upsert_entity("shared::widget", "default", "TOPIC", 1.0)
    store.link_entity("shared::widget", a.id, 1.0)
    store.link_entity("shared::widget", b.id, 1.0)
    bridges = find_bridges(store, HashingEmbedder())
    assert bridges == []


def test_co_retrieved_excluded():
    """Two different-bucket memories ever surfaced together in the same
    recall (same query string on a "retrieved" event) are excluded."""
    store = EpisodicStore(":memory:")
    a = _add(store, CONTENT_A, "s1", BOOKDNA_SRC)
    b = _add(store, CONTENT_B, "s2", AIRTHROW_SRC)
    store.log_event(a.id, "retrieved", {"query": "gesture", "score": 0.5, "rank": 0})
    store.log_event(b.id, "retrieved", {"query": "gesture", "score": 0.4, "rank": 1})
    bridges = find_bridges(store, HashingEmbedder())
    assert bridges == []


def test_finds_cross_bucket_bridge():
    """The positive case: different buckets, not a near-dup, no shared
    entity, never co-retrieved -- this pair IS a bridge."""
    store = EpisodicStore(":memory:")
    a = _add(store, CONTENT_A, "s1", BOOKDNA_SRC)
    b = _add(store, CONTENT_B, "s2", AIRTHROW_SRC)
    bridges = find_bridges(store, HashingEmbedder())
    assert len(bridges) == 1
    br = bridges[0]
    assert {br.a_id, br.b_id} == {a.id, b.id}
    assert {br.a_bucket, br.b_bucket} == {"book-dna", "airthrow"}
    assert br.score > 0
    assert br.why is None


def test_session_prefix_fallback_bucket():
    """No provenance source and no graph entities: bucket falls back to a
    session-id prefix, so two episodes in different sessions still bridge."""
    store = EpisodicStore(":memory:")
    _add(store, CONTENT_A, "aaaaaaaa-session-1111", None)
    _add(store, CONTENT_B, "bbbbbbbb-session-2222", None)
    bridges = find_bridges(store, HashingEmbedder())
    assert len(bridges) == 1
    assert bridges[0].a_bucket.startswith("session:")
    assert bridges[0].a_bucket != bridges[0].b_bucket


def test_entity_cluster_bucket_used_when_no_source_hint():
    """No provenance source, but the graph layer is populated: bucket falls
    back to the episode's strongest (highest-degree) entity cluster."""
    store = EpisodicStore(":memory:")
    a = _add(store, CONTENT_A, "s1", None)
    b = _add(store, CONTENT_B, "s2", None)
    store.upsert_entity("default::topicone", "default", "TOPIC", 1.0)
    store.upsert_entity("default::topictwo", "default", "TOPIC", 1.0)
    store.link_entity("default::topicone", a.id, 1.0)
    store.link_entity("default::topictwo", b.id, 1.0)
    bridges = find_bridges(store, HashingEmbedder())
    assert len(bridges) == 1
    assert bridges[0].a_bucket.startswith("entity:")
    assert bridges[0].b_bucket.startswith("entity:")


def test_k_caps_results():
    store = EpisodicStore(":memory:")
    contents = [
        "Alpha project idea about gesture control interfaces for phones.",
        "Beta project idea about gesture driven memory retrieval systems.",
        "Gamma project idea about gesture recognition and wearable sensors.",
    ]
    for i, content in enumerate(contents):
        _add(store, content, f"s{i}", f"/x/.claude/projects/-x-proj{i}/t.jsonl")
    bridges = find_bridges(store, HashingEmbedder(), k=1)
    assert len(bridges) == 1


# --------------------------------------------------------------------------- #
# Verdict persistence round-trip
# --------------------------------------------------------------------------- #
def test_verdict_persistence_roundtrip(tmp_path):
    cache_path = tmp_path / "bridges.json"
    store = EpisodicStore(":memory:")
    a = _add(store, CONTENT_A, "s1", BOOKDNA_SRC)
    b = _add(store, CONTENT_B, "s2", AIRTHROW_SRC)
    embedder = HashingEmbedder()

    first = compute_bridges(store, embedder, cache_path=cache_path, use_llm=False)
    assert len(first) == 1
    key = pair_key(a.id, b.id)

    set_verdict(cache_path, a.id, b.id, "dismiss")
    cache = load_cache(cache_path)
    assert cache["verdicts"][key] == "dismiss"

    second = compute_bridges(store, embedder, cache_path=cache_path, use_llm=False)
    assert second == []  # dismissed pairs never resurface

    set_verdict(cache_path, a.id, b.id, "keep")
    third = compute_bridges(store, embedder, cache_path=cache_path, use_llm=False)
    assert len(third) == 1
    assert third[0].pair_key == key

    with pytest.raises(ValueError):
        set_verdict(cache_path, a.id, b.id, "maybe")


def test_apply_verdicts_pins_kept_and_drops_dismissed():
    br1 = Bridge("a", "b", "ea", "eb", "bucket1", "bucket2", score=0.9)
    br2 = Bridge("c", "d", "ec", "ed", "bucket1", "bucket3", score=0.5)
    br3 = Bridge("e", "f", "ee", "ef", "bucket1", "bucket4", score=0.8)
    verdicts = {br2.pair_key: "keep", br3.pair_key: "dismiss"}
    result = apply_verdicts([br1, br2, br3], verdicts)
    assert [b.pair_key for b in result] == [br2.pair_key, br1.pair_key]


def test_load_cache_missing_file_returns_defaults(tmp_path):
    cache = load_cache(tmp_path / "nope.json")
    assert cache == {"computed_at": None, "bridges": [], "verdicts": {}}


def test_save_and_load_cache_roundtrip(tmp_path):
    path = tmp_path / "sub" / "bridges.json"
    br = Bridge("a", "b", "ea", "eb", "bucket1", "bucket2", score=0.42, why="maybe related")
    save_cache(path, [br], {"x|y": "keep"})
    cache = load_cache(path)
    assert cache["verdicts"] == {"x|y": "keep"}
    assert cache["bridges"][0]["why"] == "maybe related"
    restored = Bridge.from_dict(cache["bridges"][0])
    assert restored == br


# --------------------------------------------------------------------------- #
# Serve smoke test (real ephemeral-port HTTP server, same pattern as
# tests/test_vault_serve.py's test_serve_vault_over_http_smoke)
# --------------------------------------------------------------------------- #
def test_serve_bridges_smoke(tmp_path):
    import socketserver

    cache_path = tmp_path / "bridges.json"
    br = Bridge(
        a_id="ep-a", b_id="ep-b",
        a_excerpt="Gesture control prototype", b_excerpt="Memory sublayer playback idea",
        a_bucket="airthrow", b_bucket="book-dna", score=0.31,
    )
    state = _ServeState(bridges=[br], cache_path=cache_path)
    handler = make_bridges_handler(state)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "1 unmade connection across your projects" in body
        assert "airthrow" in body and "book-dna" in body

        conn.request("GET", "/api/bridges.json")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data[0]["a_id"] == "ep-a"

        payload = json.dumps({"pair": pair_key("ep-a", "ep-b"), "verdict": "dismiss"}).encode()
        conn.request(
            "POST", "/api/verdict", body=payload,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        assert json.loads(resp.read().decode("utf-8"))["ok"] is True

        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert "0 unmade connections across your projects" in body
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert load_cache(cache_path)["verdicts"][pair_key("ep-a", "ep-b")] == "dismiss"


def test_render_constellation_page_empty_state():
    body = render_constellation_page([]).decode("utf-8")
    assert "0 unmade connections across your projects" in body
    assert "memsub bridges scan" in body


def test_find_bridges_caps_candidate_episodes(monkeypatch):
    # The pairwise scan is O(n^2) in memory; max_episodes must bound the
    # candidate set to the most important, then most recent, active memories.
    from memsub.bridges import find_bridges
    from memsub.embed import build_embedder
    from memsub.store import Episode, EpisodicStore

    store = EpisodicStore(":memory:")
    for i in range(40):
        store.append(Episode(
            content=f"idea number {i} about a distinct topic {i}",
            run_id="r", session_id=f"proj{i % 4}-sess", ts=float(i),
            importance=float(i), id=f"ep{i:03d}",
        ).with_defaults())
    seen = {}
    real_active = store.active
    def _spy():
        eps = real_active()
        seen["n"] = len(eps)
        return eps
    monkeypatch.setattr(store, "active", _spy)
    bridges = find_bridges(store, build_embedder(prefer="hashing"), k=5, max_episodes=10)
    assert seen["n"] == 40  # all active fetched
    # but only the 10 highest importance survive into the pairwise step, so no
    # bridge references an episode below the cutoff importance (ep030..ep039)
    kept_ids = {b.a_id for b in bridges} | {b.b_id for b in bridges}
    assert all(int(i.replace("ep", "")) >= 30 for i in kept_ids)
    store.close()


def test_filter_chips_escape_bucket_names():
    from memsub.bridges import Bridge, _filter_chips_html
    b = Bridge(a_id="a", b_id="b", a_excerpt="x", b_excerpt="y",
               a_bucket="<script>alert(1)</script>", b_bucket="book-dna", score=0.5)
    html_out = _filter_chips_html([b])
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out
