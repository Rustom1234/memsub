"""Bridges: non-obvious connections between ideas across projects.

find_bridges() mines the active memory set for pairs that are semantically
close (per the store's embedder) but come from different project "buckets",
are not near-duplicates, share no entity link, and were never surfaced
together in the same recall. It is a candidate finder, not a truth engine --
see docs/bridges-2026-07-22.md for the honest does-NOT-do list.

A memory's bucket is derived in this order:
  1. an explicit project hint pulled from its provenance (the transcript
     path segment extraction wrote it from, e.g. ".../projects/book-dna/x")
  2. failing that, its strongest entity-cluster from the graph tables
     (memsub.graph), if that layer has been populated
  3. failing both, a prefix of its session id

Score = similarity * cross_bucket_bonus. The bonus is a within-run
inverse-frequency weight: a bridge between two buckets that rarely pair up
among this run's surviving candidates ranks above an equally-similar bridge
between two buckets that constantly do -- the point of this product is the
rare link, not the obvious one.

Persistence (bridges + user verdicts) lives in a small JSON cache, default
~/.memsub/bridges.json, overridable for tests. A "keep" verdict pins a pair
to the top of future runs; a "dismiss" verdict removes it permanently.
"""

from __future__ import annotations

import hashlib
import html
import json
import socketserver
import threading
import time
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import numpy as np

from .backends import LLMBackend, build_backend, ollama_available
from .consolidate import ConsolidationConfig
from .embed import Embedder, build_embedder
from .store import EpisodicStore

DEFAULT_CACHE_PATH = Path.home() / ".memsub" / "bridges.json"
EXCERPT_MAX_CHARS = 240
WHY_TOP_N = 20
WHY_MODEL = "qwen3:8b"
DEFAULT_SERVE_PORT = 8768

WHY_SYSTEM = (
    "You are shown two short memories belonging to the same person, from two "
    "different projects. In one short sentence (at most 25 words), suggest a "
    "specific, plausible, non-obvious way these two ideas might connect. If "
    "you genuinely see no connection, reply with exactly: no clear connection.\n"
    "Output only that one sentence, nothing else."
)


@dataclass(frozen=True)
class Bridge:
    """One candidate connection between two memories in different buckets."""

    a_id: str
    b_id: str
    a_excerpt: str
    b_excerpt: str
    a_bucket: str
    b_bucket: str
    score: float
    why: str | None = None

    @property
    def pair_key(self) -> str:
        return pair_key(self.a_id, self.b_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "a_id": self.a_id,
            "b_id": self.b_id,
            "a_excerpt": self.a_excerpt,
            "b_excerpt": self.b_excerpt,
            "a_bucket": self.a_bucket,
            "b_bucket": self.b_bucket,
            "score": round(self.score, 6),
            "why": self.why,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Bridge":
        return Bridge(
            a_id=d["a_id"],
            b_id=d["b_id"],
            a_excerpt=d["a_excerpt"],
            b_excerpt=d["b_excerpt"],
            a_bucket=d["a_bucket"],
            b_bucket=d["b_bucket"],
            score=float(d["score"]),
            why=d.get("why"),
        )


def pair_key(a_id: str, b_id: str) -> str:
    """Order-independent identity for a bridge pair, used as the verdict key."""
    lo, hi = sorted((a_id, b_id))
    return f"{lo}|{hi}"


# --------------------------------------------------------------------------- #
# Bucket derivation
# --------------------------------------------------------------------------- #
def _project_hint(source: str | None) -> str | None:
    """Best-effort project-name hint from a transcript source path's parent
    directory segment.

    Claude Code (and similar tools) name a transcript's project folder after
    the working directory with "/" replaced by "-", e.g.
    ".../-home-user-projects-book-dna/<session>.jsonl" -> "book-dna",
    ".../-home-user-airthrow/<session>.jsonl" -> "airthrow". The project
    slug is whatever follows the last "-projects-" marker, or (no marker)
    whatever remains after dropping a leading home-directory/username pair.
    """
    if not source:
        return None
    parent = Path(source).parent.name
    if not parent:
        return None
    marker = "-projects-"
    idx = parent.find(marker)
    if idx != -1:
        slug = parent[idx + len(marker):]
    else:
        parts = parent.lstrip("-").split("-")
        if parts and parts[0] in ("homes", "home"):
            parts = parts[2:]  # drop "homes" and the username segment
        slug = "-".join(parts)
    slug = slug.strip("-").lower()
    return slug or None


def _entity_map(store: EpisodicStore, episode_ids: list[str]) -> dict[str, set[str]]:
    return {eid: {e.id for e in store.entities_for_episode(eid)} for eid in episode_ids}


def _entity_degree(ep_entities: dict[str, set[str]]) -> dict[str, int]:
    degree: dict[str, int] = defaultdict(int)
    for entities in ep_entities.values():
        for eid in entities:
            degree[eid] += 1
    return degree


def _bucket_of(session_id: str, source: str | None, entities: set[str], degree: dict[str, int]) -> str:
    hint = _project_hint(source)
    if hint:
        return hint
    if entities:
        best = max(entities, key=lambda eid: (degree.get(eid, 0), eid))
        return "entity:" + best.split("::", 1)[-1]
    return "session:" + session_id[:8]


def _retrieved_queries(store: EpisodicStore) -> dict[str, set[str]]:
    """episode_id -> set of query strings it was retrieved for. memsub.audit's
    log_recall logs every scored episode of one recall against the same query
    string in one call, so two episodes sharing a query string here were
    co-retrieved together."""
    out: dict[str, set[str]] = defaultdict(set)
    for ev in store.events(event="retrieved"):
        q = ev.detail.get("query")
        if q:
            out[ev.episode_id].add(q)
    return out


def _excerpt(content: str, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    text = " ".join(content.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
# find_bridges
# --------------------------------------------------------------------------- #
def find_bridges(
    store: EpisodicStore,
    embedder: Embedder,
    k: int = 50,
    dedup_threshold: float | None = None,
) -> list[Bridge]:
    """Find up to `k` candidate bridges among the store's active episodes.

    A pair is excluded when it is:
      (same bucket)   both memories resolve to the same bucket
      (a) near-dup    cosine similarity >= the consolidate dedup threshold
      (d) linked      the two episodes share at least one entity_link row
      (e) co-retrieved they were ever surfaced together in the same recall

    Surviving pairs are scored similarity * cross_bucket_bonus and returned
    highest score first.
    """
    episodes = store.active()
    if len(episodes) < 2:
        return []

    threshold = (
        dedup_threshold if dedup_threshold is not None else ConsolidationConfig().dedup_threshold
    )

    vectors = np.stack([embedder.embed(ep.content) for ep in episodes])
    sims = vectors @ vectors.T

    episode_ids = [ep.id for ep in episodes]
    ep_entities = _entity_map(store, episode_ids)
    degree = _entity_degree(ep_entities)
    buckets = {
        ep.id: _bucket_of(ep.session_id, ep.provenance.get("source"), ep_entities[ep.id], degree)
        for ep in episodes
    }
    retrieved_for = _retrieved_queries(store)

    raw: list[tuple[int, int, float]] = []
    for i in range(len(episodes)):
        for j in range(i + 1, len(episodes)):
            a, b = episodes[i], episodes[j]
            if buckets[a.id] == buckets[b.id]:
                continue
            sim = float(sims[i, j])
            if sim >= threshold:
                continue
            if ep_entities[a.id] & ep_entities[b.id]:
                continue
            if retrieved_for.get(a.id, set()) & retrieved_for.get(b.id, set()):
                continue
            raw.append((i, j, sim))

    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    for i, j, _ in raw:
        pair_counts[tuple(sorted((buckets[episodes[i].id], buckets[episodes[j].id])))] += 1

    bridges: list[Bridge] = []
    for i, j, sim in raw:
        a, b = episodes[i], episodes[j]
        bp = tuple(sorted((buckets[a.id], buckets[b.id])))
        bonus = 1.0 / (pair_counts[bp] ** 0.5)
        bridges.append(
            Bridge(
                a_id=a.id,
                b_id=b.id,
                a_excerpt=_excerpt(a.content),
                b_excerpt=_excerpt(b.content),
                a_bucket=buckets[a.id],
                b_bucket=buckets[b.id],
                score=sim * bonus,
            )
        )
    bridges.sort(key=lambda br: -br.score)
    return bridges[:k]


# --------------------------------------------------------------------------- #
# Optional garnish: one "why" line per top-N bridge, ollama only
# --------------------------------------------------------------------------- #
def add_why_lines(
    bridges: list[Bridge], backend: LLMBackend, top_n: int = WHY_TOP_N
) -> list[Bridge]:
    """Best-effort "why these might connect" line for the top `top_n` bridges.

    Never raises: a backend failure just leaves that bridge's why as None,
    since the product works with zero LLM garnish (this is the garnish, not
    the mechanism)."""
    out = list(bridges)
    for idx in range(min(top_n, len(out))):
        br = out[idx]
        try:
            response = backend.complete(
                WHY_SYSTEM,
                [
                    {
                        "role": "user",
                        "content": f"A ({br.a_bucket}): {br.a_excerpt}\nB ({br.b_bucket}): {br.b_excerpt}",
                    }
                ],
            )
        except Exception:
            continue
        line = " ".join(response.split()).strip()
        if line and line.lower().rstrip(".") != "no clear connection":
            out[idx] = replace(br, why=line[:200])
    return out


# --------------------------------------------------------------------------- #
# Persistence: ~/.memsub/bridges.json (bridges + verdicts)
# --------------------------------------------------------------------------- #
def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"computed_at": None, "bridges": [], "verdicts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"computed_at": None, "bridges": [], "verdicts": {}}
    data.setdefault("bridges", [])
    data.setdefault("verdicts", {})
    return data


def save_cache(path: Path, bridges: list[Bridge], verdicts: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "computed_at": time.time(),
        "bridges": [br.to_dict() for br in bridges],
        "verdicts": verdicts,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def apply_verdicts(bridges: list[Bridge], verdicts: dict[str, str]) -> list[Bridge]:
    """Drop dismissed pairs, pin kept pairs to the top (score order preserved
    within each group). Pure: never mutates its arguments."""
    kept: list[Bridge] = []
    rest: list[Bridge] = []
    for br in bridges:
        verdict = verdicts.get(br.pair_key)
        if verdict == "dismiss":
            continue
        (kept if verdict == "keep" else rest).append(br)
    return kept + rest


def set_verdict(cache_path: Path, a_id: str, b_id: str, verdict: str) -> None:
    """Record keep/dismiss for one pair, merged into the cache file."""
    if verdict not in ("keep", "dismiss"):
        raise ValueError(f"verdict must be 'keep' or 'dismiss', got {verdict!r}")
    data = load_cache(cache_path)
    data["verdicts"][pair_key(a_id, b_id)] = verdict
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def compute_bridges(
    store: EpisodicStore,
    embedder: Embedder,
    k: int = 50,
    cache_path: Path = DEFAULT_CACHE_PATH,
    use_llm: bool = True,
) -> list[Bridge]:
    """find_bridges(), merged with cached verdicts (dismissed dropped, kept
    pinned to the top), why-lines added for the surviving top WHY_TOP_N when
    an ollama daemon is reachable (silently skipped otherwise), then
    persisted back to cache_path."""
    bridges = find_bridges(store, embedder, k=k)
    cache = load_cache(cache_path)
    verdicts: dict[str, str] = cache.get("verdicts", {})
    bridges = apply_verdicts(bridges, verdicts)
    if use_llm and ollama_available():
        try:
            backend = build_backend(prefer="ollama", model=WHY_MODEL)
            bridges = add_why_lines(bridges, backend)
        except RuntimeError:
            pass
    save_cache(cache_path, bridges, verdicts)
    return bridges


# --------------------------------------------------------------------------- #
# Frontend: a "constellation" page, dark/phone-first, stdlib-only server --
# same house style as memsub/vault_serve.py, kept as an independent copy in
# this file (bridges.py is this feature's one home) rather than an import,
# since vault_serve's palette/CSS constants are module-private.
# --------------------------------------------------------------------------- #
_PALETTE = [
    "#7cb0ff", "#6fd6ff", "#7fe0b0", "#e6c860", "#ffb37c", "#ff8aa8", "#b98cff", "#8ce0e0",
]


def _bucket_color(name: str) -> str:
    """Deterministic color per bucket name, picked from a fixed palette."""
    h = int.from_bytes(hashlib.md5(name.encode("utf-8")).digest()[:4], "little")
    return _PALETTE[h % len(_PALETTE)]


def _bucket_pair_key(a_bucket: str, b_bucket: str) -> str:
    return "|".join(sorted((a_bucket, b_bucket)))


def _card_html(br: Bridge) -> str:
    why_html = f'<p class="bridge-why">{html.escape(br.why)}</p>' if br.why else ""
    pct = max(2, min(100, round(br.score * 100)))
    a_color, b_color = _bucket_color(br.a_bucket), _bucket_color(br.b_bucket)
    return (
        f'<div class="bridge-card" data-pair="{html.escape(br.pair_key)}" '
        f'data-bp="{html.escape(_bucket_pair_key(br.a_bucket, br.b_bucket))}">'
        '<div class="bridge-chips">'
        f'<span class="bucket-chip" style="border-color:{a_color};color:{a_color}">{html.escape(br.a_bucket)}</span>'
        '<span class="bridge-arrow">&harr;</span>'
        f'<span class="bucket-chip" style="border-color:{b_color};color:{b_color}">{html.escape(br.b_bucket)}</span>'
        "</div>"
        '<div class="bridge-pair">'
        f'<p class="bridge-excerpt">{html.escape(br.a_excerpt)}</p>'
        f'<p class="bridge-excerpt">{html.escape(br.b_excerpt)}</p>'
        "</div>"
        f"{why_html}"
        '<div class="bridge-strength"><div class="bridge-strength-fill" '
        f'style="width:{pct}%"></div></div>'
        '<div class="bridge-actions">'
        '<button class="btn-keep" data-action="keep" type="button">keep</button>'
        '<button class="btn-dismiss" data-action="dismiss" type="button">dismiss</button>'
        "</div></div>"
    )


def _filter_chips_html(bridges: list[Bridge]) -> str:
    counts: dict[str, int] = defaultdict(int)
    labels: dict[str, str] = {}
    for br in bridges:
        key = _bucket_pair_key(br.a_bucket, br.b_bucket)
        counts[key] += 1
        lo, hi = sorted((br.a_bucket, br.b_bucket))
        labels[key] = f"{lo} &harr; {hi}"
    chips = [f'<button class="chip active" data-bp="" type="button">all ({len(bridges)})</button>']
    for key in sorted(counts, key=lambda kk: -counts[kk]):
        chips.append(
            f'<button class="chip" data-bp="{html.escape(key)}" type="button">'
            f"{labels[key]} ({counts[key]})</button>"
        )
    return "".join(chips)


_CSS = """
:root {
  color-scheme: dark;
  --bg: #0a0b0d; --bg-raised: #14161a; --bg-card: #191c22; --border: #262a33;
  --text: #d8dbe2; --text-dim: #8b909c;
  --accent: #6fd6ff;
  --mono: ui-monospace, "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace;
  --sans: -apple-system, system-ui, sans-serif;
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font-family: var(--sans); margin: 0; padding: 0 0 3rem; line-height: 1.5; }
.page { max-width: 720px; margin: 0 auto; padding: 1.25rem 1rem; }
h1 { font-family: var(--mono); font-size: 1.2rem; color: #f0f2f6; margin: 0 0 1rem; }
.empty { color: var(--text-dim); }
.chip-row { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 1.1rem; }
.chip { font-family: var(--mono); font-size: 0.78rem; color: var(--text-dim); background: var(--bg-raised);
  border: 1px solid var(--border); border-radius: 999px; padding: 0.25rem 0.7rem; cursor: pointer; }
.chip.active { color: var(--accent); border-color: var(--accent); }
.bridge-grid { display: grid; gap: 0.8rem; }
.bridge-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 0.9rem 1rem; }
.bridge-card.kept { border-color: var(--accent); }
.bridge-chips { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.6rem; }
.bucket-chip { font-family: var(--mono); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.03em;
  border: 1px solid; border-radius: 999px; padding: 0.15rem 0.55rem; }
.bridge-arrow { color: var(--text-dim); }
.bridge-pair { display: grid; gap: 0.5rem; }
.bridge-excerpt { margin: 0; padding: 0.55rem 0.7rem; background: var(--bg-raised); border-radius: 8px; font-size: 0.9rem; }
.bridge-why { margin: 0.6rem 0 0; font-size: 0.85rem; color: var(--accent); font-style: italic; }
.bridge-strength { margin: 0.7rem 0 0.6rem; height: 5px; background: var(--bg-raised); border-radius: 3px; overflow: hidden; }
.bridge-strength-fill { height: 100%; background: var(--accent); }
.bridge-actions { display: flex; gap: 0.5rem; }
.bridge-actions button { font-family: var(--mono); font-size: 0.78rem; border-radius: 6px; padding: 0.3rem 0.7rem;
  cursor: pointer; border: 1px solid var(--border); background: var(--bg-raised); color: var(--text); }
.btn-keep:hover { color: var(--accent); border-color: var(--accent); }
.btn-dismiss:hover { color: #ff8a8a; border-color: #ff8a8a; }
@media (min-width: 640px) { .bridge-pair { grid-template-columns: 1fr 1fr; } }
"""

_JS = """
document.querySelectorAll('.chip').forEach(function (chip) {
  chip.addEventListener('click', function () {
    document.querySelectorAll('.chip').forEach(function (c) { c.classList.remove('active'); });
    chip.classList.add('active');
    var bp = chip.dataset.bp;
    document.querySelectorAll('.bridge-card').forEach(function (card) {
      card.style.display = (!bp || card.dataset.bp === bp) ? '' : 'none';
    });
  });
});
document.querySelectorAll('.bridge-actions button').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var card = btn.closest('.bridge-card');
    var action = btn.dataset.action;
    fetch('/api/verdict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pair: card.dataset.pair, verdict: action })
    }).then(function (r) { return r.json(); }).then(function () {
      if (action === 'dismiss') {
        card.remove();
      } else {
        card.classList.add('kept');
        card.parentNode.insertBefore(card, card.parentNode.firstChild);
      }
    });
  });
});
"""


def render_constellation_page(bridges: list[Bridge]) -> bytes:
    n = len(bridges)
    header = f"<h1>{n} unmade connection{'' if n == 1 else 's'} across your projects</h1>"
    if not bridges:
        body = (
            header
            + '<p class="empty">No candidate bridges yet -- run <code>memsub bridges scan</code> '
            "against a populated store, or check back after more memories accumulate.</p>"
        )
    else:
        body = (
            header
            + f'<div class="chip-row">{_filter_chips_html(bridges)}</div>'
            + '<div class="bridge-grid">' + "".join(_card_html(b) for b in bridges) + "</div>"
        )
    doc = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Bridges</title><style>{_CSS}</style></head>"
        f'<body><div class="page">{body}</div><script>{_JS}</script></body></html>'
    )
    return doc.encode("utf-8")


@dataclass
class _ServeState:
    bridges: list[Bridge]
    cache_path: Path
    lock: threading.Lock = field(default_factory=threading.Lock)


def make_bridges_handler(state: _ServeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "memsub-bridges-serve/1.0"

        def log_message(self, fmt, *args):  # quieter default logging
            pass

        def _send(self, body: bytes, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (stdlib-mandated name)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                with state.lock:
                    self._send(render_constellation_page(state.bridges))
                return
            if parsed.path == "/api/bridges.json":
                with state.lock:
                    payload = json.dumps([b.to_dict() for b in state.bridges]).encode("utf-8")
                self._send(payload, content_type="application/json; charset=utf-8")
                return
            self._send(b"not found", status=404, content_type="text/plain")

        def do_POST(self) -> None:  # noqa: N802 (stdlib-mandated name)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/verdict":
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = {}
                pair = str(data.get("pair", ""))
                verdict = str(data.get("verdict", ""))
                if "|" not in pair or verdict not in ("keep", "dismiss"):
                    self._send(b'{"ok": false}', status=400, content_type="application/json; charset=utf-8")
                    return
                a_id, b_id = pair.split("|", 1)
                with state.lock:
                    set_verdict(state.cache_path, a_id, b_id, verdict)
                    verdicts = load_cache(state.cache_path).get("verdicts", {})
                    state.bridges = apply_verdicts(state.bridges, verdicts)
                self._send(b'{"ok": true}', content_type="application/json; charset=utf-8")
                return
            self._send(b"not found", status=404, content_type="text/plain")

    return Handler


def serve_bridges(
    db_path: Path | str,
    host: str = "127.0.0.1",
    port: int = DEFAULT_SERVE_PORT,
    cache_path: Path = DEFAULT_CACHE_PATH,
    embedder: Embedder | None = None,
    use_llm: bool = True,
    k: int = 50,
) -> int:
    """Compute (or reuse cached) bridges once at startup, same "no live
    reload" contract memsub/vault_serve.py's index has, then serve the
    constellation page. Verdict POSTs update the in-memory list and the
    cache file, so a keep/dismiss is visible on the next GET without a
    server restart."""
    db_path = Path(db_path).expanduser()
    if not db_path.exists():
        print(f"no store at {db_path}")
        return 1
    store = EpisodicStore(db_path)
    emb = embedder or build_embedder(prefer="hashing")
    try:
        bridges = compute_bridges(store, emb, k=k, cache_path=cache_path, use_llm=use_llm)
    finally:
        store.close()
    state = _ServeState(bridges=bridges, cache_path=cache_path)
    handler = make_bridges_handler(state)
    with socketserver.ThreadingTCPServer((host, port), handler) as httpd:
        print(f"memsub bridges serve: {len(bridges)} bridge(s) from {db_path}")
        print(f"listening on http://{host}:{port} (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
