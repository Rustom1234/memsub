"""Read-only browser viewer for a markdown vault (or any markdown folder).

Built for working over SSH with no Obsidian on the client: `memsub vault
serve` starts a small stdlib-only HTTP server (no third-party web framework,
no markdown library -- .venv has neither installed, and the vault is a few
thousand small files, so a minimal line-based renderer is plenty) that acts
as a small memory product: a dashboard (real counts from the store, a
provenance donut, a 30-day activity sparkline), instant-feel search, a note
view with a provenance badge and animated supersede-chain timeline, and a
read-only review-queue triage page. `[[wikilinks]]` render as real links
(dead-link style when the target does not exist, hover preview when it
does), frontmatter shows as a small metadata card instead of raw YAML, and
```mermaid code fences render as diagrams client-side via the mermaid CDN
script.

Vault mode (--vault, the default) and root mode (--root) are the exact same
viewer pointed at a different folder: root mode exists so a plain docs/
folder full of design docs (mermaid diagrams included) is viewable with the
same command, no memsub-specific structure required. The dashboard's store
numbers are best-effort: if no SQLite store is found at --db (or it can't
be opened), the page falls back to "vault-only mode" and derives every
number from note frontmatter instead -- it never fabricates a store.

Read-only and local-only by construction: no write endpoints exist anywhere
(including the dashboard's store access, which only ever calls
EpisodicStore's read methods and never opens a path that doesn't already
exist on disk, so a GET request can never create a database file), the
server binds 127.0.0.1 only, and every requested note path is resolved and
checked to stay under the served root before the file is opened (see
`_safe_path`), so a crafted URL cannot read anything outside the vault.
"""

from __future__ import annotations

import html
import json
import math
import random
import re
import socketserver
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from . import ask as ask_mod
from .backends import build_backend
from .sdk import MemoryClient
from .store import EpisodicStore, TERMINAL_EVENTS
from .vault_graph import (
    DEFAULT_NODE_LIMIT,
    MAX_NODE_LIMIT,
    build_graph_index,
    filter_and_cap,
    linked_neighbors,
    note_constellation,
)
from .vault_notes import (
    NoteSections,
    group_related_entities,
    namespace_in_words,
    note_title,
    parse_editorial_links,
    split_note_sections,
)
from .vault_similarity import build_similarity_index, connection_of_the_day, related_ideas

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"
SEARCH_CAP = 200
SERVE_VERSION = "0.2.0"  # mirrors pyproject.toml's memsub version line for the footer stamp

# Same 8-color jewel-tone palette as _GRAPH_JS's client-side PALETTE array
# (kept in sync by hand -- this is only ever the static legend swatch strip,
# never used to pick a color, so there's nothing to import/compute here).
_CLUSTER_PALETTE = ["#7cb0ff", "#6fd6ff", "#7fe0b0", "#e6c860", "#ffb37c", "#ff8aa8", "#b98cff", "#8ce0e0"]
_CLUSTER_SWATCHES = "".join(f'<span class="dot" style="background:{c}"></span>' for c in _CLUSTER_PALETTE)

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_FENCE_RE = re.compile(r"^```(\S*)\s*$")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)\d+\.\s+(.*)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})\s*$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")

# `## <category> (<meta>)` heading and `- [[target]] -- <body>` item lines
# in review-queue.md (memsub/scripts/triage_vault.py's own output format).
_REVIEW_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_REVIEW_CATEGORY_RE = re.compile(r"^(\S+)\s*\((.*)\)$")
_REVIEW_ITEM_RE = re.compile(r"^-\s+\[\[([^\]]+)\]\]\s+--\s+(.*)$")

# `- superseded by [[..]]` / `- supersedes [[..]]` lines memsub/vault.py
# writes under a note's "## Links" section.
_SUPERSEDE_RE = re.compile(r"^- (superseded by|supersedes) \[\[([^\]]+)\]\]\s*$", re.MULTILINE)

_REVIEW_CATEGORY_CLASS = {
    "corrected_reassertion": "badge-review-warn",
    "near_duplicate": "badge-review-dup",
    "stale_transient": "badge-review-stale",
}


# --------------------------------------------------------------------------- #
# Index: build once at server start from the files on disk
# --------------------------------------------------------------------------- #
@dataclass
class VaultIndex:
    root: Path
    # relpath (posix, with .md) -> raw file text
    notes: dict[str, str] = field(default_factory=dict)
    # bare stem (no dir, no extension) -> list of relpaths sharing that stem,
    # for resolving same-directory wikilinks the way Obsidian does
    by_stem: dict[str, list[str]] = field(default_factory=dict)
    # relpath -> parsed frontmatter fields, cached at build time so the
    # dashboard's vault-only fallback never re-parses every note per request
    frontmatter: dict[str, dict[str, str]] = field(default_factory=dict)


def build_index(root: Path) -> VaultIndex:
    idx = VaultIndex(root=root)
    for f in sorted(root.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = f.relative_to(root).as_posix()
        idx.notes[rel] = text
        stem_key = f.stem
        idx.by_stem.setdefault(stem_key, []).append(rel)
        fields, _ = split_frontmatter(text)
        if fields:
            idx.frontmatter[rel] = fields
    return idx


def resolve_wikilink(target: str, idx: VaultIndex) -> str | None:
    """Return the relpath (with .md) a `[[target]]` resolves to, or None."""
    target = target.strip()
    if not target:
        return None
    candidate = target if target.endswith(".md") else f"{target}.md"
    if candidate in idx.notes:
        return candidate
    stem = Path(target).stem
    matches = idx.by_stem.get(stem)
    if matches:
        return matches[0]
    return None


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #
def safe_path(root: Path, requested: str) -> Path | None:
    """Resolve a request-supplied relative path against root and verify the
    result stays under root. Returns None on any traversal attempt or if the
    resolved file does not exist."""
    requested = urllib.parse.unquote(requested).lstrip("/")
    if not requested:
        return None
    root_resolved = root.resolve()
    try:
        candidate = (root / requested).resolve()
    except (OSError, RuntimeError):
        return None
    if candidate != root_resolved and root_resolved not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    return candidate


# --------------------------------------------------------------------------- #
# Frontmatter
# --------------------------------------------------------------------------- #
def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Very small, permissive `key: value` frontmatter parser (not full
    YAML -- nothing in .venv parses YAML and every note memsub writes is a
    flat key: "value" block, see memsub/vault.py's own _frontmatter). Returns
    (fields, body). A note with no frontmatter fence returns ({}, text)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1].replace('\\"', '"')
        if key:
            fields[key] = value
    return fields, text[m.end():]


def _provenance_badge(fields: dict) -> str:
    """observed | inferred | human-verified | quarantined, derived only from
    frontmatter memsub/vault.py already writes. Precedence when fields
    overlap (e.g. a confirmed inference): human-verified > quarantined >
    inferred > observed -- the same order used by the dashboard donut so the
    label means the same thing everywhere, even though the donut reads live
    store events and this reads frontmatter (note view never touches the
    store)."""
    if fields.get("human_confirmed") == "True":
        return "human-verified"
    if fields.get("status") == "quarantined":
        return "quarantined"
    if fields.get("type") == "inferred":
        return "inferred"
    return "observed"


# --------------------------------------------------------------------------- #
# Markdown -> HTML (line-based, stdlib-only)
# --------------------------------------------------------------------------- #
def _inline(text: str, idx: VaultIndex) -> str:
    text = html.escape(text, quote=False)

    def _wikilink(m: re.Match) -> str:
        target, alias = m.group(1), m.group(2)
        display = html.escape((alias or target).strip())
        resolved = resolve_wikilink(target, idx)
        if resolved is None:
            return f'<span class="wikilink dead" title="note not found">{display}</span>'
        href = "/note/" + urllib.parse.quote(resolved)
        return f'<a class="wikilink" href="{href}">{display}</a>'

    text = _WIKILINK_RE.sub(_wikilink, text)
    text = _MD_LINK_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', text
    )
    text = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    return text


def render_markdown(text: str, idx: VaultIndex) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    para: list[str] = []
    list_stack: list[str] = []  # tag names of open <ul>/<ol>

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para), idx)}</p>")
            para.clear()

    def close_lists() -> None:
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    while i < len(lines):
        line = lines[i]

        fence_m = _FENCE_RE.match(line)
        if fence_m:
            flush_para()
            close_lists()
            lang = fence_m.group(1)
            body_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            code = html.escape("\n".join(body_lines))
            if lang == "mermaid":
                out.append(f'<pre class="mermaid">{code}</pre>')
            else:
                cls = f' class="language-{html.escape(lang)}"' if lang else ""
                out.append(f"<pre><code{cls}>{code}</code></pre>")
            continue

        if not line.strip():
            flush_para()
            close_lists()
            i += 1
            continue

        header_m = _HEADER_RE.match(line)
        if header_m:
            flush_para()
            close_lists()
            level = len(header_m.group(1))
            out.append(f"<h{level}>{_inline(header_m.group(2), idx)}</h{level}>")
            i += 1
            continue

        if _HR_RE.match(line):
            flush_para()
            close_lists()
            out.append("<hr>")
            i += 1
            continue

        quote_m = _QUOTE_RE.match(line)
        if quote_m:
            flush_para()
            close_lists()
            out.append(f"<blockquote>{_inline(quote_m.group(1), idx)}</blockquote>")
            i += 1
            continue

        list_m = _LIST_RE.match(line)
        ol_m = _OL_RE.match(line)
        if list_m or ol_m:
            flush_para()
            tag = "ul" if list_m else "ol"
            item_text = (list_m or ol_m).group(2)
            if not list_stack:
                out.append(f"<{tag}>")
                list_stack.append(tag)
            out.append(f"<li>{_inline(item_text, idx)}</li>")
            i += 1
            continue

        close_lists()
        para.append(line.strip())
        i += 1

    flush_para()
    close_lists()
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Supersede-chain timeline (note view): walk the "## Links" bullets
# memsub/vault.py already writes, no store access needed.
# --------------------------------------------------------------------------- #
def _supersede_links(relpath: str, idx: VaultIndex) -> tuple[str | None, str | None]:
    """(older, newer) relpaths this note points at via its own `supersedes` /
    `superseded by` link lines. A note with more than one `supersedes` line
    (a consolidation merge of several old memories) collapses to the last
    one found -- this timeline shows one linear chain, not a full DAG, same
    simplification memsub/vault.py's own `_supersede_maps` makes ("one
    terminal event is enough")."""
    older = newer = None
    for m in _SUPERSEDE_RE.finditer(idx.notes.get(relpath, "")):
        kind, target = m.group(1), m.group(2)
        resolved = resolve_wikilink(target, idx)
        if resolved is None:
            continue
        if kind == "supersedes":
            older = resolved
        else:
            newer = resolved
    return older, newer


def supersede_chain(relpath: str, idx: VaultIndex, max_hops: int = 20) -> list[str]:
    """Ordered relpaths from oldest to newest, including relpath itself.
    Cycle- and length-capped (max_hops each direction) so a malformed vault
    can't hang a request."""
    chain = [relpath]
    seen = {relpath}
    cur = relpath
    for _ in range(max_hops):
        older, _ = _supersede_links(cur, idx)
        if older is None or older in seen:
            break
        chain.insert(0, older)
        seen.add(older)
        cur = older
    cur = relpath
    for _ in range(max_hops):
        _, newer = _supersede_links(cur, idx)
        if newer is None or newer in seen:
            break
        chain.append(newer)
        seen.add(newer)
        cur = newer
    return chain


def _render_supersede_timeline(chain: list[str], current: str, idx: VaultIndex) -> str:
    if len(chain) <= 1:
        return ""
    nodes = []
    for rel in chain:
        href = "/note/" + urllib.parse.quote(rel)
        cls = "timeline-node current" if rel == current else "timeline-node"
        nodes.append(f'<li class="{cls}"><a href="{href}">{html.escape(note_title(rel, idx))}</a></li>')
    return (
        '<div class="timeline"><h3>Supersede chain</h3>'
        f'<ul class="timeline-list">{"".join(nodes)}</ul></div>'
    )


# --------------------------------------------------------------------------- #
# Search: matching (unchanged behavior) + presentation (folder + snippet)
# --------------------------------------------------------------------------- #
def search_notes(idx: VaultIndex, query: str, cap: int = SEARCH_CAP) -> list[str]:
    q = query.lower().strip()
    if not q:
        return []
    hits = []
    for rel, text in idx.notes.items():
        if q in rel.lower() or q in text.lower():
            hits.append(rel)
            if len(hits) >= cap:
                break
    return hits


def _snippet_html(text: str, query_lower: str, window: int = 50) -> str:
    """HTML-safe snippet around the first match, with <mark> around it. Falls
    back to the note's opening text when the match was filename-only."""
    lower = text.lower()
    pos = lower.find(query_lower) if query_lower else -1
    if pos == -1:
        return html.escape(" ".join(text[: window * 2].split()))
    start = max(0, pos - window)
    end = min(len(text), pos + len(query_lower) + window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    before = html.escape(text[start:pos])
    match = html.escape(text[pos:pos + len(query_lower)])
    after = html.escape(text[pos + len(query_lower):end])
    return f"{prefix}{before}<mark>{match}</mark>{after}{suffix}"


def search_results(idx: VaultIndex, query: str, cap: int = SEARCH_CAP) -> list[dict]:
    q = query.lower().strip()
    out = []
    for rel in search_notes(idx, query, cap=cap):
        text = idx.notes[rel]
        folder = str(Path(rel).parent) if Path(rel).parent != Path(".") else "(root)"
        out.append({
            "relpath": rel,
            "title": note_title(rel, idx),
            "folder": folder,
            "snippet_html": _snippet_html(text, q),
        })
    return out


# --------------------------------------------------------------------------- #
# Dashboard stats: store-backed when available, vault-frontmatter fallback
# otherwise. Never writes anything, never creates a database file.
# --------------------------------------------------------------------------- #
def _open_store_readonly(db_path: Path | None) -> EpisodicStore | None:
    if db_path is None:
        return None
    try:
        p = Path(db_path)
        if not p.exists():  # sqlite3.connect would silently create a missing
            return None      # file -- never let a GET request do that
        return EpisodicStore(p)
    except Exception:
        return None


def _iso_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _stats_from_store(store: EpisodicStore) -> dict:
    episodes = store.all()
    events = store.events()

    terminal: dict[str, str] = {}
    confirmed_ids: set[str] = set()
    for ev in events:  # newest first: first hit per episode is the latest
        if ev.event in TERMINAL_EVENTS and ev.episode_id not in terminal:
            terminal[ev.episode_id] = ev.event
        if ev.event == "confirmed":
            confirmed_ids.add(ev.episode_id)

    active_n = superseded_n = forgotten_n = quarantined_n = 0
    observed_b = inferred_b = verified_b = quarantined_b = 0
    namespaces: set[str] = set()
    daily: dict[str, int] = {}
    last_extraction_ts: float | None = None

    for ep in episodes:
        status = terminal.get(ep.id, "active")
        if status == "active":
            active_n += 1
        elif status == "superseded":
            superseded_n += 1
        elif status == "forgotten":
            forgotten_n += 1
        elif status == "quarantined":
            quarantined_n += 1
        namespaces.add(ep.namespace)
        day = _iso_date(ep.ts)
        daily[day] = daily.get(day, 0) + 1
        if ep.provenance.get("origin") == "extraction":
            if last_extraction_ts is None or ep.ts > last_extraction_ts:
                last_extraction_ts = ep.ts

        is_inferred = ep.kind == "inference" or bool(ep.provenance.get("inferred"))
        if ep.id in confirmed_ids:
            verified_b += 1
        elif status == "quarantined":
            quarantined_b += 1
        elif is_inferred:
            inferred_b += 1
        else:
            observed_b += 1

    return {
        "source": "store",
        "total": len(episodes),
        "active": active_n,
        "superseded": superseded_n,
        "quarantined": quarantined_n,
        "namespaces": len(namespaces),
        "human_verified": len(confirmed_ids),
        "last_extraction_ts": last_extraction_ts,
        "daily": daily,
        "donut": [
            ("observed", observed_b, "var(--c-observed)"),
            ("inferred", inferred_b, "var(--c-inferred)"),
            ("human-verified", verified_b, "var(--c-verified)"),
            ("quarantined", quarantined_b, "var(--c-quarantined)"),
        ],
    }


def _stats_from_vault(idx: VaultIndex) -> dict:
    active_n = superseded_n = quarantined_n = 0
    observed_b = inferred_b = verified_b = quarantined_b = 0
    namespaces: set[str] = set()
    daily: dict[str, int] = {}

    for fields in idx.frontmatter.values():
        status = fields.get("status", "active")
        if status == "active":
            active_n += 1
        elif status == "superseded":
            superseded_n += 1
        elif status == "quarantined":
            quarantined_n += 1
        ns = fields.get("namespace")
        if ns:
            namespaces.add(ns)
        created = fields.get("created", "")
        if len(created) >= 10:
            day = created[:10]
            daily[day] = daily.get(day, 0) + 1

        confirmed = fields.get("human_confirmed") == "True"
        is_inferred = fields.get("type") == "inferred"
        if confirmed:
            verified_b += 1
        elif status == "quarantined":
            quarantined_b += 1
        elif is_inferred:
            inferred_b += 1
        else:
            observed_b += 1

    return {
        "source": "vault",
        "total": len(idx.notes),
        "active": active_n,
        "superseded": superseded_n,
        "quarantined": quarantined_n,
        "namespaces": len(namespaces),
        "human_verified": verified_b,
        "last_extraction_ts": None,
        "daily": daily,
        "donut": [
            ("observed", observed_b, "var(--c-observed)"),
            ("inferred", inferred_b, "var(--c-inferred)"),
            ("human-verified", verified_b, "var(--c-verified)"),
            ("quarantined", quarantined_b, "var(--c-quarantined)"),
        ],
    }


def compute_dashboard_stats(idx: VaultIndex, db_path: Path | None) -> dict:
    store = _open_store_readonly(db_path)
    if store is None:
        return _stats_from_vault(idx)
    try:
        return _stats_from_store(store)
    except Exception:
        return _stats_from_vault(idx)
    finally:
        store.close()


def _sparkline_series(daily: dict[str, int], days: int = 30) -> list[int]:
    today = datetime.now().date()
    return [daily.get((today - timedelta(days=i)).strftime("%Y-%m-%d"), 0) for i in range(days - 1, -1, -1)]


def _relative_time(ts: float | None) -> str:
    if ts is None:
        return "unknown"
    delta = time.time() - ts
    if delta < 0:
        delta = 0
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 86400 * 30:
        return f"{int(delta // 86400)}d ago"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Inline SVG: provenance donut + 30-day sparkline (no chart library)
# --------------------------------------------------------------------------- #
def _svg_donut(segments: list[tuple[str, int, str]], size: int = 128, stroke: int = 18) -> str:
    total = sum(n for _, n, _ in segments)
    r = (size - stroke) / 2
    circ = 2 * math.pi * r
    c = size / 2
    if total == 0:
        return (
            f'<svg class="donut" viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" '
            f'aria-label="no memories yet">'
            f'<circle cx="{c}" cy="{c}" r="{r:.1f}" fill="none" stroke="var(--border)" stroke-width="{stroke}"/>'
            f"</svg>"
        )
    parts = []
    cumulative = 0.0
    delay = 0
    for label, n, color in segments:
        if n <= 0:
            continue
        arc = circ * (n / total)
        style = f"--arc:{arc:.2f};--circ:{circ:.2f};--dashoffset:{-cumulative:.2f};--delay:{delay}ms"
        parts.append(
            f'<circle class="donut-seg" cx="{c}" cy="{c}" r="{r:.1f}" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="butt" style="{style}" '
            f'transform="rotate(-90 {c} {c})"><title>{html.escape(label)}: {n}</title></circle>'
        )
        cumulative += arc
        delay += 120
    return (
        f'<svg class="donut" viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" '
        f'aria-label="provenance breakdown">' + "".join(parts) + "</svg>"
    )


def _svg_sparkline(series: list[int], width: int = 280, height: int = 48) -> str:
    if not series or max(series) == 0:
        return (
            f'<svg class="sparkline" viewBox="0 0 {width} {height}">'
            f'<text x="6" y="{height / 2 + 4:.0f}" class="sparkline-empty">no activity in the last 30 days</text>'
            f"</svg>"
        )
    n = len(series)
    m = max(series)
    step = width / max(1, n - 1)
    points = []
    for i, v in enumerate(series):
        x = i * step
        y = height - (v / m) * (height - 8) - 3
        points.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(points)
    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'role="img" aria-label="memories created per day, last 30 days">'
        f'<polyline points="{poly}" pathLength="100" fill="none" stroke="var(--accent)" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="spark-draw"/>'
        f"</svg>"
    )


def _tile(label: str, value: int, accent: bool = False) -> str:
    cls = "tile-value accent" if accent else "tile-value"
    return (
        f'<div class="tile"><div class="{cls}" data-count="{value}">{value:,}</div>'
        f'<div class="tile-label">{html.escape(label)}</div></div>'
    )


def _tile_text(label: str, text: str) -> str:
    return (
        f'<div class="tile"><div class="tile-value tile-text">{html.escape(text)}</div>'
        f'<div class="tile-label">{html.escape(label)}</div></div>'
    )


def _namespace_breakdown(idx: VaultIndex) -> list[tuple[str, int]]:
    """Namespace/source-project counts, vault-text-derived (works in both
    store and vault-only mode). Sorted by count desc."""
    counts: dict[str, int] = {}
    for fields in idx.frontmatter.values():
        ns = fields.get("namespace")
        if ns:
            counts[ns] = counts.get(ns, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _recent_activity(idx: VaultIndex, n: int = 5) -> list[tuple[str, float]]:
    """5 newest notes by frontmatter `created`, (relpath, epoch) newest first."""
    dated = []
    for rel, fields in idx.frontmatter.items():
        ts = _epoch_from_iso(fields.get("created", ""))
        if ts:
            dated.append((rel, ts))
    dated.sort(key=lambda kv: -kv[1])
    return dated[:n]


def _render_brain_section(idx: VaultIndex, graph, sim_index) -> str:
    """"What your brain knows": top entity chips (tap = search), a projects
    breakdown in prose, a recent-activity list, and connection-of-the-day --
    the "more informative website" upgrades Rustom asked for, on top of the
    existing stat tiles/donut/sparkline (untouched)."""
    parts = []

    if graph is not None and graph.hubs:
        chips = "".join(
            f'<a class="entity-chip" href="/?q={urllib.parse.quote(entity)}">{html.escape(entity)}'
            f'<span class="n">{node.degree}</span></a>'
            for entity, node in list(graph.hubs.items())[:10]
        )
        parts.append(f'<div class="brain-section"><h3>what your brain knows</h3><div class="entity-chips">{chips}</div></div>')

    breakdown = _namespace_breakdown(idx)
    if breakdown:
        items = "".join(
            f"<li>{html.escape(namespace_in_words(ns))} <span class=\"when\">{count:,} note(s)</span></li>"
            for ns, count in breakdown
        )
        parts.append(f'<div class="brain-section"><h3>projects</h3><ul class="prose-list">{items}</ul></div>')

    recent = _recent_activity(idx)
    if recent:
        items = "".join(
            f'<li><a href="/note/{urllib.parse.quote(rel)}">{html.escape(note_title(rel, idx))}</a>'
            f'<span class="when">{html.escape(_relative_time(ts))}</span></li>'
            for rel, ts in recent
        )
        parts.append(f'<div class="brain-section"><h3>recent activity</h3><ul class="prose-list">{items}</ul></div>')

    if sim_index is not None:
        today = datetime.now().strftime("%Y-%m-%d")
        cod = connection_of_the_day(sim_index, idx, today)
        if cod is not None:
            card = (
                '<div class="cod-card"><div class="pair">'
                f'<a href="/note/{urllib.parse.quote(cod["a"]["relpath"])}">{html.escape(cod["a"]["title"])}</a>'
                f'<a href="/note/{urllib.parse.quote(cod["b"]["relpath"])}">{html.escape(cod["b"]["title"])}</a>'
                "</div>"
                f'<p class="why">why: shares {html.escape(cod["why"])}</p></div>'
            )
            parts.append(f'<div class="brain-section"><h3>connection of the day</h3>{card}</div>')

    return "".join(parts)


def _render_dashboard(idx: VaultIndex, db_path: Path | None, graph=None, sim_index=None) -> str:
    stats = compute_dashboard_stats(idx, db_path)
    mode_note = ""
    if stats["source"] != "store":
        mode_note = (
            '<p class="mode-note">vault-only mode: no memory store found at the configured '
            "<code>--db</code> path, so these counts are derived from note frontmatter, not "
            "the live store.</p>"
        )
    tiles = "".join([
        _tile("total memories", stats["total"], accent=True),
        _tile("active", stats["active"]),
        _tile("superseded", stats["superseded"]),
        _tile("namespaces", stats["namespaces"]),
        _tile("human-verified", stats["human_verified"]),
        _tile("quarantined", stats["quarantined"]),
        _tile_text("last extraction", _relative_time(stats["last_extraction_ts"])),
    ])
    legend = "".join(
        f'<li><span class="legend-dot" style="background:{color}"></span>{html.escape(label)}'
        f'<span class="legend-count">{count:,}</span></li>'
        for label, count, color in stats["donut"]
    )
    donut_svg = _svg_donut(stats["donut"])
    sparkline_svg = _svg_sparkline(_sparkline_series(stats["daily"]))
    brain_html = _render_brain_section(idx, graph, sim_index)
    return (
        '<section class="dashboard">'
        f"{mode_note}"
        f'<div class="tiles">{tiles}</div>'
        '<div class="viz-row">'
        f'<div class="viz-card"><h3>provenance</h3>'
        f'<div class="donut-wrap">{donut_svg}<ul class="legend">{legend}</ul></div></div>'
        f'<div class="viz-card"><h3>30-day activity</h3>{sparkline_svg}</div>'
        "</div>"
        f"{brain_html}"
        "</section>"
    )


# --------------------------------------------------------------------------- #
# Review queue: parse review-queue.md's generated structure into cards
# --------------------------------------------------------------------------- #
def parse_review_queue(body: str, idx: VaultIndex) -> list[dict]:
    sections: list[dict] = []
    current: dict | None = None
    for line in body.splitlines():
        heading_m = _REVIEW_HEADING_RE.match(line)
        if heading_m:
            heading = heading_m.group(1)
            cat_m = _REVIEW_CATEGORY_RE.match(heading)
            name, meta = (cat_m.group(1), cat_m.group(2)) if cat_m else (heading, "")
            current = {"category": name, "meta": meta, "items": []}
            sections.append(current)
            continue
        item_m = _REVIEW_ITEM_RE.match(line)
        if item_m and current is not None:
            target, rest = item_m.group(1), item_m.group(2)
            resolved = resolve_wikilink(target, idx)
            current["items"].append({
                "target": note_title(resolved, idx) if resolved else target,
                "href": ("/note/" + urllib.parse.quote(resolved)) if resolved else None,
                "body_html": _inline(rest, idx),
            })
    return sections


def render_review_page(idx: VaultIndex) -> bytes:
    text = idx.notes.get("review-queue.md")
    if text is None:
        content = (
            "<h1>Review queue</h1>"
            '<p class="count">No <code>review-queue.md</code> in this folder. Run '
            "<code>scripts/triage_vault.py</code> against a vault to generate one.</p>"
        )
        return _page("Review queue", content, note_count=len(idx.notes), breadcrumb="review queue")

    _, body = split_frontmatter(text)
    sections = parse_review_queue(body, idx)
    total_items = sum(len(s["items"]) for s in sections)
    howto = (
        '<p class="howto">Read-only triage view -- no verdicts are applied here. Set '
        "<code>verdict: confirm</code> or <code>verdict: reject</code> in a memory note's "
        "frontmatter (or edit its body), then run <code>memsub vault apply</code> to apply "
        "it.</p>"
    )
    cards = []
    for section in sections:
        badge_cls = _REVIEW_CATEGORY_CLASS.get(section["category"], "badge-review-other")
        items_html = "".join(
            '<li class="review-card">'
            + (
                f'<a href="{it["href"]}">{html.escape(it["target"])}</a>'
                if it["href"]
                else f'<span class="wikilink dead">{html.escape(it["target"])}</span>'
            )
            + f'<p>{it["body_html"]}</p></li>'
            for it in section["items"]
        )
        cards.append(
            f'<div class="review-section"><h2><span class="badge {badge_cls}">'
            f'{html.escape(section["category"])}</span> '
            f'<span class="count">({html.escape(section["meta"])})</span></h2>'
            f'<ul class="review-list">{items_html}</ul></div>'
        )
    plural = "y" if len(sections) == 1 else "ies"
    content = (
        "<h1>Review queue</h1>"
        f'<p class="count">{total_items} candidate(s) across {len(sections)} categor{plural}</p>'
        f"{howto}" + "".join(cards)
    )
    return _page("Review queue", content, note_count=len(idx.notes), breadcrumb="review queue")


# --------------------------------------------------------------------------- #
# Page chrome: dark Mission Control, monospace headings, one accent color
# --------------------------------------------------------------------------- #
_CSS = """
:root {
  color-scheme: dark;
  --bg: #0a0b0d; --bg-raised: #14161a; --bg-card: #191c22; --border: #262a33;
  --text: #d8dbe2; --text-dim: #8b909c; --text-faint: #5a5f6b;
  --accent: #6fd6ff; --accent-dim: rgba(111, 214, 255, 0.15);
  --mono: ui-monospace, "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace;
  --sans: -apple-system, system-ui, sans-serif;
  --c-observed: #7cb0ff; --c-inferred: #b98cff; --c-verified: #4ade80; --c-quarantined: #ff6b6b;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  background: var(--bg); color: var(--text); font-family: var(--sans);
  line-height: 1.55; margin: 0; padding: 0 0 3rem;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1, h2, h3, h4 { font-family: var(--mono); color: #f0f2f6; line-height: 1.3; letter-spacing: -0.01em; }
.page h1 { font-size: 1.4rem; margin: 0.9rem 0 0.75rem; word-break: break-word; }
code { background: var(--bg-raised); padding: 0.1rem 0.3rem; border-radius: 4px; font-size: 0.9em; font-family: var(--mono); }
pre { background: var(--bg-raised); padding: 0.75rem; overflow-x: auto; border-radius: 8px; border: 1px solid var(--border); }
pre code { background: none; padding: 0; }
blockquote { border-left: 3px solid var(--border); margin: 0.5rem 0; padding: 0.2rem 1rem; color: var(--text-dim); }
mark { background: var(--accent-dim); color: var(--accent); border-radius: 3px; padding: 0 0.1rem; }
hr { border: none; border-top: 1px solid var(--border); margin: 1.5rem 0; }

.page { max-width: 1100px; margin: 0 auto; padding: 0 1rem; }

/* sticky nav */
.topnav {
  position: sticky; top: 0; z-index: 20; display: flex; align-items: center; gap: 0.9rem;
  padding: 0.65rem 1rem; background: rgba(10, 11, 13, 0.9); backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border);
}
.topnav .brand { font-family: var(--mono); font-weight: 600; color: #f0f2f6; white-space: nowrap; }
.topnav .brand .accent { color: var(--accent); }
.topnav .crumb { color: var(--text-dim); font-family: var(--mono); font-size: 0.85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.topnav .nav-spacer { flex: 1; }
.topnav .nav-btn { color: var(--text-dim); font-size: 0.85rem; border: 1px solid var(--border); padding: 0.25rem 0.6rem; border-radius: 6px; white-space: nowrap; }
.topnav .nav-btn:hover { color: var(--accent); border-color: var(--accent); text-decoration: none; }

/* dashboard */
.dashboard { margin: 1.25rem 0 1.75rem; }
.mode-note { font-size: 0.8rem; color: var(--text-dim); background: var(--bg-raised); border: 1px dashed var(--border); border-radius: 6px; padding: 0.5rem 0.75rem; margin-bottom: 1rem; }
.tiles { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.6rem; }
.tile { background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px; padding: 0.75rem 0.9rem; }
.tile-value { font-family: var(--mono); font-size: 1.5rem; font-weight: 600; color: var(--text); line-height: 1.1; }
.tile-value.accent { color: var(--accent); }
.tile-value.tile-text { font-size: 1.05rem; color: var(--text-dim); }
.tile-label { font-size: 0.72rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 0.25rem; }

.viz-row { display: grid; grid-template-columns: 1fr; gap: 0.75rem; margin-top: 0.75rem; }
.viz-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px; padding: 0.9rem 1rem; }
.viz-card h3 { margin: 0 0 0.6rem; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); font-weight: 600; }
.donut-wrap { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
.legend { list-style: none; margin: 0; padding: 0; font-size: 0.82rem; color: var(--text-dim); }
.legend li { display: flex; align-items: center; gap: 0.4rem; padding: 0.15rem 0; }
.legend-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.legend-count { margin-left: auto; padding-left: 0.6rem; color: var(--text); font-family: var(--mono); }
.sparkline { width: 100%; height: 48px; display: block; }
.sparkline-empty { fill: var(--text-faint); font-size: 10px; font-family: var(--sans); }

.donut-seg { stroke-dasharray: var(--arc) var(--circ); stroke-dashoffset: var(--dashoffset); }
@media (prefers-reduced-motion: no-preference) {
  .donut-seg { animation: donut-grow 900ms ease-out backwards; animation-delay: var(--delay, 0ms); }
  .spark-draw { stroke-dasharray: 100; stroke-dashoffset: 100; animation: spark-draw 1100ms ease-out forwards; }
  .timeline-list::before { animation: timeline-draw 700ms ease-out; }
}
@keyframes donut-grow { from { stroke-dasharray: 0 var(--circ); } }
@keyframes spark-draw { to { stroke-dashoffset: 0; } }
@keyframes timeline-draw { from { transform: scaleY(0); } to { transform: scaleY(1); } }

/* search */
.search-section { margin-top: 0.5rem; }
input[type=text] {
  width: 100%; padding: 0.65rem 0.8rem; border-radius: 8px; border: 1px solid var(--border);
  background: var(--bg-raised); color: var(--text); font-size: 1rem; font-family: var(--sans);
}
input[type=text]:focus { outline: none; border-color: var(--accent); }
.count { color: var(--text-faint); font-size: 0.85em; }
.folder { margin-top: 1.25rem; }
.folder h2 { font-size: 0.95rem; color: var(--text-dim); border-bottom: 1px solid var(--border); padding-bottom: 0.3rem; }
ul.notelist { list-style: none; padding-left: 0; }
ul.notelist li { padding: 0.15rem 0; }
a.result-item { display: block; padding: 0.35rem 0.5rem; border-radius: 6px; }
a.result-item:hover, a.result-item.active { background: var(--bg-card); text-decoration: none; }
a.result-item .snippet { display: block; color: var(--text-dim); font-size: 0.82rem; margin-top: 0.15rem; }

/* wikilinks */
.wikilink.dead { color: #ff8a8a; border-bottom: 1px dashed #ff8a8a; cursor: default; }
.wikilink-preview {
  position: absolute; z-index: 50; max-width: 22rem; background: var(--bg-card);
  border: 1px solid var(--border); border-radius: 8px; padding: 0.6rem 0.8rem;
  font-size: 0.85rem; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
}
.wikilink-preview strong { display: block; margin-bottom: 0.25rem; font-family: var(--mono); }
.wikilink-preview p { margin: 0.35rem 0 0; color: var(--text-dim); }

/* note view */
.note-body { max-width: 42rem; margin-top: 0.5rem; }
.note-body p, .note-body li { max-width: 42rem; }
.meta { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 0.7rem 1rem; font-size: 0.85em; color: var(--text-dim); margin-bottom: 1rem; }
.meta-badge { margin-bottom: 0.5rem; }
.meta-rows span { display: inline-block; margin-right: 1rem; }
.badge { display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px; font-size: 0.72rem; font-family: var(--mono); text-transform: uppercase; letter-spacing: 0.03em; border: 1px solid; }
.badge-observed { background: rgba(124, 176, 255, 0.12); color: var(--c-observed); border-color: var(--c-observed); }
.badge-inferred { background: rgba(185, 140, 255, 0.12); color: var(--c-inferred); border-color: var(--c-inferred); }
.badge-human-verified { background: rgba(74, 222, 128, 0.12); color: var(--c-verified); border-color: var(--c-verified); }
.badge-quarantined { background: rgba(255, 107, 107, 0.12); color: var(--c-quarantined); border-color: var(--c-quarantined); }

.timeline { margin: 1.25rem 0; }
.timeline h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); margin-bottom: 0.5rem; }
.timeline-list { list-style: none; padding-left: 1.4rem; margin: 0; position: relative; }
.timeline-list::before {
  content: ""; position: absolute; left: 0.28rem; top: 0.5rem; bottom: 0.5rem; width: 2px;
  background: var(--border); transform-origin: top;
}
.timeline-node { position: relative; padding: 0.3rem 0 0.3rem 0.6rem; }
.timeline-node::before {
  content: ""; position: absolute; left: -1.16rem; top: 0.68rem; width: 8px; height: 8px;
  border-radius: 50%; background: var(--border); border: 2px solid var(--bg);
}
.timeline-node.current::before { background: var(--accent); }
.timeline-node.current a { color: var(--accent); font-weight: 600; }

/* review queue */
.howto { background: var(--bg-raised); border: 1px dashed var(--border); border-radius: 6px; padding: 0.6rem 0.85rem; font-size: 0.85rem; color: var(--text-dim); }
.review-section { margin-top: 1.5rem; }
.review-section h2 { font-size: 1rem; display: flex; align-items: center; gap: 0.5rem; }
ul.review-list { list-style: none; padding: 0; display: grid; gap: 0.5rem; margin-top: 0.6rem; }
.review-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 0.6rem 0.85rem; }
.review-card p { margin: 0.35rem 0 0; color: var(--text-dim); font-size: 0.88rem; }
.badge-review-warn { background: rgba(255, 200, 87, 0.12); color: #ffc857; border-color: #ffc857; }
.badge-review-dup { background: rgba(124, 176, 255, 0.12); color: var(--c-observed); border-color: var(--c-observed); }
.badge-review-stale { background: rgba(255, 107, 107, 0.12); color: var(--c-quarantined); border-color: var(--c-quarantined); }
.badge-review-other { background: var(--bg-raised); color: var(--text-dim); border-color: var(--border); }

.stamp { max-width: 1100px; margin: 2rem auto 0; padding: 0.75rem 1rem; color: var(--text-faint); font-size: 0.78rem; font-family: var(--mono); border-top: 1px solid var(--border); }

@media (min-width: 720px) {
  .tiles { grid-template-columns: repeat(4, 1fr); }
  .viz-row { grid-template-columns: 1.1fr 1fr; }
}

/* Layer 0: readability -- meta strip, small chips, closed details block */
.meta-strip { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; margin: 0.4rem 0 0.9rem; font-size: 0.85rem; color: var(--text-dim); }
.meta-date, .meta-project { color: var(--text-dim); }
.chip { display: inline-block; padding: 0.05rem 0.5rem; border-radius: 999px; font-size: 0.72rem; background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-dim); }
.promoted-from { color: var(--text-dim); font-size: 0.88rem; margin: 0 0 0.5rem; }
.note-details { margin-top: 1.5rem; background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 0.6rem 0.9rem; font-size: 0.85rem; color: var(--text-dim); }
.note-details summary { cursor: pointer; color: var(--text); font-family: var(--mono); font-size: 0.8rem; }
.note-details .meta-rows span { display: inline-block; margin: 0.2rem 1rem 0.2rem 0; }
.note-details h4 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-dim); margin: 0.8rem 0 0.3rem; }

/* Layer 0/3: related ideas panel (grouped entity links + discovery set) */
.related-panel { margin-top: 1.5rem; }
.related-panel h3 { font-size: 0.9rem; margin-bottom: 0.6rem; }
.related-block { margin-bottom: 0.9rem; }
.related-block h4 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-dim); margin: 0 0 0.4rem; }
.related-group { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 0.5rem 0.8rem; margin-bottom: 0.4rem; font-size: 0.88rem; }
.related-group summary { cursor: pointer; }
ul.related-list { list-style: none; margin: 0.4rem 0 0; padding: 0; }
ul.related-list li { padding: 0.15rem 0; }
ul.related-list.discovery { display: grid; gap: 0.4rem; }
.related-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 0.5rem 0.8rem; }
.related-card .why { margin: 0.2rem 0 0; color: var(--text-faint); font-size: 0.78rem; }

/* Layer 2: graph canvas (full page + mini constellation), shared styling.
   Dark radial-gradient backdrop lives on the wrap (not repainted on canvas
   every frame -- cheaper, and the canvas stays transparent so it shows
   through), matching the product's --bg/--bg-card tones. */
.graph-canvas-wrap {
  position: relative; border: 1px solid var(--border); border-radius: 10px; overflow: hidden;
  background: radial-gradient(ellipse at 50% 42%, #181c24 0%, #0e1015 55%, #08090b 100%);
}
.graph-canvas { display: block; width: 100%; height: 100%; touch-action: none; cursor: grab; }
.graph-canvas:active { cursor: grabbing; }
.graph-hint { font-size: 0.72rem; color: var(--text-faint); padding: 0.4rem 0.7rem; margin: 0; }
.constellation { margin-top: 1.5rem; }
.constellation h3 { font-size: 0.9rem; margin-bottom: 0.5rem; }
.constellation .graph-canvas-wrap { height: 260px; }
.graph-canvas-wrap.graph-page { height: 62vh; min-height: 360px; }
.graph-controls { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.75rem 0; align-items: center; }
.graph-controls select, .graph-controls input[type=text] { width: auto; min-width: 8rem; padding: 0.4rem 0.6rem; font-size: 0.85rem; }
.graph-status { font-size: 0.78rem; color: var(--text-dim); margin: 0.4rem 0; }
.graph-status button.expand { background: none; border: 1px solid var(--border); color: var(--accent); border-radius: 6px; padding: 0.1rem 0.5rem; font-size: 0.78rem; cursor: pointer; margin-left: 0.5rem; }
.graph-legend { display: flex; flex-wrap: wrap; gap: 0.75rem 1rem; font-size: 0.78rem; color: var(--text-dim); margin-bottom: 0.5rem; }
.graph-legend span { display: inline-flex; align-items: center; gap: 0.35rem; }
.graph-legend .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; box-shadow: 0 0 6px currentColor; }
.graph-preview-card { position: absolute; z-index: 30; max-width: 16rem; background: var(--bg-raised); border: 1px solid var(--border); border-radius: 8px; padding: 0.55rem 0.75rem; font-size: 0.82rem; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6); }
.graph-preview-card strong { display: block; margin-bottom: 0.3rem; }
.graph-preview-card a { font-size: 0.78rem; }

/* dashboard: brain-knows chips, projects breakdown, recent activity, connection of the day */
.brain-section { margin-top: 1.25rem; }
.brain-section h3 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); margin: 0 0 0.5rem; }
.entity-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.entity-chip { background: var(--bg-card); border: 1px solid var(--border); color: var(--text); border-radius: 999px; padding: 0.25rem 0.7rem; font-size: 0.82rem; }
.entity-chip:hover { border-color: var(--accent); color: var(--accent); text-decoration: none; }
.entity-chip .n { color: var(--text-faint); margin-left: 0.3rem; }
.prose-list { list-style: none; margin: 0; padding: 0; font-size: 0.88rem; }
.prose-list li { padding: 0.3rem 0; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: baseline; gap: 0.75rem; }
.prose-list li:last-child { border-bottom: none; }
.prose-list li a { flex: 1 1 auto; min-width: 0; }
.prose-list .when { color: var(--text-faint); font-size: 0.78rem; white-space: nowrap; flex-shrink: 0; }
.cod-card { background: var(--bg-card); border: 1px solid var(--accent); border-radius: 10px; padding: 0.8rem 1rem; }
.cod-card .pair { display: flex; flex-direction: column; gap: 0.3rem; margin: 0.4rem 0; }
.cod-card .why { color: var(--text-dim); font-size: 0.82rem; margin-top: 0.4rem; }

/* Ask your brain */
.ask-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.75rem 0 1rem; }
.ask-chip { background: var(--bg-card); border: 1px solid var(--border); color: var(--text-dim); border-radius: 999px; padding: 0.3rem 0.75rem; font-size: 0.82rem; cursor: pointer; }
.ask-chip:hover { border-color: var(--accent); color: var(--accent); }
.ask-form { display: flex; gap: 0.5rem; margin-bottom: 0.5rem; }
.ask-form input[type=text] { flex: 1; }
.ask-form button { background: var(--accent-dim); color: var(--accent); border: 1px solid var(--accent); border-radius: 8px; padding: 0 1rem; font-family: var(--sans); font-size: 1rem; cursor: pointer; }
.ask-form button:disabled { opacity: 0.5; cursor: default; }
.ask-status { color: var(--text-dim); font-size: 0.85rem; margin: 0.4rem 0; }
.ask-turn { margin: 1.25rem 0; padding-top: 1rem; border-top: 1px solid var(--border); }
.ask-turn:first-child { border-top: none; }
.ask-question { font-family: var(--mono); color: #f0f2f6; font-size: 0.95rem; margin-bottom: 0.5rem; }
.ask-answer { max-width: 42rem; }
.ask-answer p { margin: 0.4rem 0; }
.ask-error { color: #ff8a8a; font-size: 0.9rem; }
.ask-refusal { color: var(--text-dim); font-style: italic; }
a.cite-chip { display: inline-block; background: var(--accent-dim); color: var(--accent); border-radius: 4px; padding: 0 0.3rem; font-size: 0.78rem; font-family: var(--mono); margin: 0 0.05rem; }
ol.ask-sources, ul.ask-nearest { margin: 0.6rem 0 0; padding-left: 1.4rem; font-size: 0.85rem; color: var(--text-dim); }
ol.ask-sources li, ul.ask-nearest li { padding: 0.15rem 0; }
.ask-meta { color: var(--text-faint); font-size: 0.75rem; margin-top: 0.4rem; }
"""

_MERMAID_INIT = f"""
<script src="{MERMAID_CDN}"></script>
<script>mermaid.initialize({{ startOnLoad: true, theme: 'dark' }});</script>
"""

# Debounced instant-feel search: fetches /api/search.json, re-renders the
# results list grouped by folder, keyboard nav (up/down/enter). On an empty
# query it restores the server-rendered full listing rather than
# reimplementing pagination client-side.
_SEARCH_JS = """
<script>
(function(){
  var input = document.getElementById('search-input');
  var results = document.getElementById('results');
  var countEl = document.getElementById('results-count');
  if (!input || !results) return;
  var defaultHTML = results.innerHTML;
  var defaultCount = countEl ? countEl.textContent : '';
  var timer = null, active = -1;

  function esc(s){ var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  function render(items, q){
    active = -1;
    if (!items.length){
      results.innerHTML = '<p class="count">no results for &ldquo;' + esc(q) + '&rdquo;</p>';
      if (countEl) countEl.textContent = '0 result(s) for \\u201c' + q + '\\u201d';
      return;
    }
    var byFolder = {};
    items.forEach(function(it){ (byFolder[it.folder] = byFolder[it.folder] || []).push(it); });
    var folders = Object.keys(byFolder).sort();
    var out = '';
    folders.forEach(function(folder){
      out += '<div class="folder"><h2>' + esc(folder) + '</h2><ul class="notelist">';
      byFolder[folder].forEach(function(it){
        out += '<li><a class="result-item" href="/note/' + encodeURIComponent(it.relpath) + '">' +
          esc(it.title) + '<span class="snippet">' + it.snippet_html + '</span></a></li>';
      });
      out += '</ul></div>';
    });
    results.innerHTML = out;
    if (countEl) countEl.textContent = items.length + ' result(s) for \\u201c' + q + '\\u201d';
  }

  function search(){
    var q = input.value.trim();
    if (!q){ results.innerHTML = defaultHTML; if (countEl) countEl.textContent = defaultCount; active = -1; return; }
    fetch('/api/search.json?q=' + encodeURIComponent(q))
      .then(function(r){ return r.json(); })
      .then(function(data){ render(data.results, q); })
      .catch(function(){});
  }

  input.addEventListener('input', function(){
    clearTimeout(timer);
    timer = setTimeout(search, 150);
  });

  input.addEventListener('keydown', function(e){
    var items = results.querySelectorAll('.result-item');
    if (!items.length) return;
    if (e.key === 'ArrowDown'){ e.preventDefault(); active = Math.min(items.length - 1, active + 1); }
    else if (e.key === 'ArrowUp'){ e.preventDefault(); active = Math.max(0, active - 1); }
    else if (e.key === 'Enter'){ if (active >= 0){ e.preventDefault(); items[active].click(); } return; }
    else { return; }
    items.forEach(function(el, i){ el.classList.toggle('active', i === active); });
    items[active].scrollIntoView({ block: 'nearest' });
  });

  input.addEventListener('keydown', function(e){
    if (e.key === 'Enter' && active < 0){ e.preventDefault(); search(); }
  });
})();
</script>
"""

# Stat-tile count-up on load. Skipped entirely (server-rendered final value
# stands) when the user has requested reduced motion.
_DASHBOARD_JS = """
<script>
(function(){
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  document.querySelectorAll('.tile-value[data-count]').forEach(function(el){
    var target = parseInt(el.getAttribute('data-count'), 10) || 0;
    if (target === 0){ return; }
    var start = null, dur = 700;
    function step(ts){
      if (start === null) start = ts;
      var p = Math.min(1, (ts - start) / dur);
      el.textContent = Math.floor(p * target).toLocaleString();
      if (p < 1) requestAnimationFrame(step); else el.textContent = target.toLocaleString();
    }
    requestAnimationFrame(step);
  });
})();
</script>
"""

# Wikilink hover preview: small GET fetch to /api/preview/<path> on
# mouseenter, popover with title/badge/snippet. Only wired on note pages
# that actually contain a resolved wikilink.
_WIKILINK_PREVIEW_JS = """
<script>
(function(){
  var popover = null, hideTimer = null;
  function esc(s){ var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
  function hide(){ if (popover){ popover.remove(); popover = null; } }
  function show(anchor, data){
    hide();
    popover = document.createElement('div');
    popover.className = 'wikilink-preview';
    popover.innerHTML = '<strong>' + esc(data.title) + '</strong>' +
      '<span class="badge badge-' + data.badge + '">' + esc(data.badge) + '</span>' +
      '<p>' + esc(data.snippet) + '</p>';
    document.body.appendChild(popover);
    var r = anchor.getBoundingClientRect();
    popover.style.left = Math.max(8, r.left + window.scrollX) + 'px';
    popover.style.top = (r.bottom + window.scrollY + 6) + 'px';
  }
  document.querySelectorAll('a.wikilink').forEach(function(a){
    a.addEventListener('mouseenter', function(){
      clearTimeout(hideTimer);
      var path = a.getAttribute('href').replace('/note/', '');
      fetch('/api/preview/' + path).then(function(r){ return r.ok ? r.json() : null; })
        .then(function(data){ if (data) show(a, data); }).catch(function(){});
    });
    a.addEventListener('mouseleave', function(){ hideTimer = setTimeout(hide, 150); });
  });
})();
</script>
"""


_GRAPH_JS = """
<script>
(function(){
  function esc(s){ var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
  function matchesQ(node, q){ return !q || node.label.toLowerCase().indexOf(q) !== -1; }
  function clamp(x, lo, hi){ return Math.max(lo, Math.min(hi, x)); }
  function hashStr(s){
    var h = 0;
    for (var i = 0; i < s.length; i++){ h = (h * 31 + s.charCodeAt(i)) | 0; }
    return Math.abs(h);
  }

  // Curated 8-color jewel-tone palette, desaturated for a dark canvas and
  // spaced for colorblind distinguishability (adapted from Okabe-Ito).
  // Cluster index comes from the server (memsub/vault_graph.py's
  // _assign_clusters: a stable hash of a note's biggest hub entity, or its
  // namespace as fallback) -- -1 means no hub/namespace, drawn neutral.
  var PALETTE = ["#7cb0ff", "#6fd6ff", "#7fe0b0", "#e6c860", "#ffb37c", "#ff8aa8", "#b98cff", "#8ce0e0"];
  var NEUTRAL = "#6b7280";
  function colorFor(node){
    return node.cluster >= 0 && node.cluster < PALETTE.length ? PALETTE[node.cluster] : NEUTRAL;
  }

  // Soft-glow node sprites: one radial-gradient disc per palette color,
  // pre-rendered once to an offscreen canvas and reused via drawImage every
  // frame. Building a CanvasGradient per node per frame is the expensive
  // way to get "radial gradient fill + bloom" -- this sprite-cache approach
  // gets the same look at 60fps for hundreds of nodes on a phone.
  var SPRITE_SIZE = 96;
  var spriteCache = {};
  function glowSprite(color){
    if (spriteCache[color]) return spriteCache[color];
    var c = document.createElement("canvas");
    c.width = c.height = SPRITE_SIZE;
    var g = c.getContext("2d");
    var cx = SPRITE_SIZE / 2;
    var grad = g.createRadialGradient(cx, cx, 0, cx, cx, cx);
    grad.addColorStop(0, color);
    grad.addColorStop(0.35, color);
    grad.addColorStop(0.65, color + "55");
    grad.addColorStop(1, color + "00");
    g.fillStyle = grad;
    g.fillRect(0, 0, SPRITE_SIZE, SPRITE_SIZE);
    spriteCache[color] = c;
    return c;
  }

  // "full" (the /graph page): nodes are seeded from the server's semantic
  // layout (memsub/vault_graph.py compute_layout), fit to the container.
  // "mini" (note-page constellation): too small to be a hairball, keeps a
  // plain random-seeded local force layout -- see WORKLOG.md 2026-07-21.
  function makeSim(data, W, H, mode){
    var full = mode !== "mini";
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    if (full){
      data.nodes.forEach(function(n){
        minX = Math.min(minX, n.x || 0); maxX = Math.max(maxX, n.x || 0);
        minY = Math.min(minY, n.y || 0); maxY = Math.max(maxY, n.y || 0);
      });
      if (!isFinite(minX)){ minX = maxX = minY = maxY = 0; }
    }
    var rangeX = Math.max(1e-6, maxX - minX), rangeY = Math.max(1e-6, maxY - minY);
    var worldSize = Math.max(W, H) * 0.9;
    var scaleTo = worldSize / Math.max(rangeX, rangeY);
    var midX = (minX + maxX) / 2, midY = (minY + maxY) / 2;

    var nodes = data.nodes.map(function(n){
      var x, y;
      if (full){
        x = (n.x - midX) * scaleTo + W / 2;
        y = (n.y - midY) * scaleTo + H / 2;
      } else {
        x = W / 2 + (Math.random() - 0.5) * W * 0.7;
        y = H / 2 + (Math.random() - 0.5) * H * 0.7;
      }
      return {
        id: n.id, kind: n.kind, label: n.label, degree: n.degree || 0,
        cluster: typeof n.cluster === "number" ? n.cluster : -1,
        x: x, y: y, homeX: x, homeY: y, vx: 0, vy: 0, fixed: false,
        phase: Math.random() * Math.PI * 2, neighbors: null
      };
    });
    var byId = {};
    nodes.forEach(function(n){ byId[n.id] = n; });
    var edges = [];
    (data.edges || []).forEach(function(e){
      var s = byId[e.source], t = byId[e.target];
      if (!s || !t) return;
      // stable per-edge curve bend (from a hash, not Math.random) so the
      // curve shape never reshuffles between redraws
      var h = (hashStr(e.source + "|" + e.target) % 1000) / 1000;
      edges.push({ s: s, t: t, w: e.weight || 1, bend: (h - 0.5) * 2 });
    });
    nodes.forEach(function(n){ n.neighbors = new Set(); });
    edges.forEach(function(e){ e.s.neighbors.add(e.t); e.t.neighbors.add(e.s); });
    return { nodes: nodes, edges: edges, byId: byId, full: full };
  }

  // Gentle local relax, not global physics: the semantic layout already
  // carries cluster structure, so repulsion only acts between nodes that
  // are already close (never fights the seeded positions apart), and a
  // weak pull back toward each node's seeded "home" position keeps a
  // dragged/jostled node from drifting out of its cluster. Mini mode keeps
  // the original stronger full force-directed constants -- it's seeded
  // randomly and needs real convergence, not a relax.
  function step(sim, W, H){
    var nodes = sim.nodes, n = nodes.length;
    var repel = sim.full ? Math.min(140, 2200 / Math.max(1, n)) : Math.min(2200, 70000 / Math.max(1, n));
    var localCutoff2 = sim.full ? 6000 : Infinity;
    var homePull = sim.full ? 0.01 : 0;
    var centerPull = sim.full ? 0 : 0.002;
    var damping = sim.full ? 0.5 : 0.55;
    var springTarget = sim.full ? 40 : 70;
    var springStrength = sim.full ? 0.006 : 0.02;

    for (var i = 0; i < n; i++){
      var a = nodes[i];
      if (a.fixed) continue;
      var fx = 0, fy = 0;
      for (var j = 0; j < n; j++){
        if (i === j) continue;
        var b = nodes[j];
        var dx = a.x - b.x, dy = a.y - b.y;
        var d2 = dx * dx + dy * dy + 0.01;
        if (d2 > localCutoff2) continue;
        var f = repel / d2;
        fx += dx * f; fy += dy * f;
      }
      fx += (W / 2 - a.x) * centerPull + (a.homeX - a.x) * homePull;
      fy += (H / 2 - a.y) * centerPull + (a.homeY - a.y) * homePull;
      a.vx = (a.vx + fx) * damping;
      a.vy = (a.vy + fy) * damping;
    }
    sim.edges.forEach(function(e){
      var dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
      var dist = Math.sqrt(dx * dx + dy * dy) || 1;
      var f = (dist - springTarget) * springStrength;
      var fx = (dx / dist) * f, fy = (dy / dist) * f;
      if (!e.s.fixed){ e.s.vx += fx; e.s.vy += fy; }
      if (!e.t.fixed){ e.t.vx -= fx; e.t.vy -= fy; }
    });
    nodes.forEach(function(a){
      if (a.fixed) return;
      a.x += clamp(a.vx, -8, 8);
      a.y += clamp(a.vy, -8, 8);
      a.x = clamp(a.x, 10, W - 10);
      a.y = clamp(a.y, 10, H - 10);
    });
  }

  function draw(ctx, sim, W, H, view, dpr, opts, clock){
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.setTransform(view.scale * dpr, 0, 0, view.scale * dpr, view.tx * dpr, view.ty * dpr);

    var focus = opts.hovered || opts.selected;
    var focusSet = focus ? focus.neighbors : null;
    function dimmed(node){
      if (!matchesQ(node, opts.q)) return true;
      if (focus && node !== focus && !(focusSet && focusSet.has(node))) return true;
      return false;
    }

    // edges: slightly curved quadratics, alpha proportional to weight --
    // most sit near-invisible at rest, the trick that kills the hairball
    sim.edges.forEach(function(e){
      var dim = dimmed(e.s) || dimmed(e.t);
      var base = clamp(0.04 + 0.09 * e.w, 0.04, 0.5);
      ctx.strokeStyle = "rgba(140,150,168," + (dim ? base * 0.25 : base) + ")";
      ctx.lineWidth = clamp(0.6 + 0.3 * e.w, 0.6, 2.2) / view.scale;
      var mx = (e.s.x + e.t.x) / 2, my = (e.s.y + e.t.y) / 2;
      var dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
      var cx = mx - dy * e.bend * 0.14, cy = my + dx * e.bend * 0.14;
      ctx.beginPath();
      ctx.moveTo(e.s.x, e.s.y);
      ctx.quadraticCurveTo(cx, cy, e.t.x, e.t.y);
      ctx.stroke();
    });

    var t = clock || 0;
    sim.nodes.forEach(function(node){
      var isHub = node.kind === "hub";
      var r = (isHub ? 6.5 : 3) + Math.min(7, Math.sqrt(node.degree || 0));
      var dim = dimmed(node);
      var isFocus = node === focus || (focusSet && focusSet.has(node));
      var dx = opts.drift ? Math.sin(t * 0.35 + node.phase) * 1.6 : 0;
      var dy = opts.drift ? Math.cos(t * 0.27 + node.phase) * 1.6 : 0;
      var sx = node.x + dx, sy = node.y + dy;

      var sprite = glowSprite(colorFor(node));
      var glowR = r * (isHub ? 4.2 : 3.2) / view.scale;
      ctx.globalAlpha = dim ? 0.15 : (isFocus ? 1 : 0.92);
      ctx.drawImage(sprite, sx - glowR, sy - glowR, glowR * 2, glowR * 2);

      if (isHub){
        ctx.globalAlpha = dim ? 0.2 : 0.85;
        ctx.strokeStyle = "rgba(240,242,246,0.55)";
        ctx.lineWidth = 1 / view.scale;
        ctx.beginPath();
        ctx.arc(sx, sy, r / view.scale, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (node === opts.selected){
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "#f0f2f6";
        ctx.lineWidth = 2 / view.scale;
        ctx.beginPath();
        ctx.arc(sx, sy, (r + 3) / view.scale, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      var showLabel = !dim && (
        isFocus ||
        (isHub && view.scale > 0.45) ||
        (view.scale > 1.4 && node.degree >= 3) ||
        view.scale > 2.4
      );
      if (showLabel){
        ctx.font = (10.5 / view.scale) + "px sans-serif";
        var label = node.label.slice(0, 32);
        var lx = sx + r / view.scale + 4, ly = sy + 3;
        ctx.lineWidth = 3 / view.scale;
        ctx.strokeStyle = "rgba(6,7,10,0.8)";
        ctx.strokeText(label, lx, ly);
        ctx.fillStyle = "#e4e7ee";
        ctx.fillText(label, lx, ly);
      }
    });
  }

  function nodeAt(sim, view, cssX, cssY){
    var wx = (cssX - view.tx) / view.scale, wy = (cssY - view.ty) / view.scale;
    var best = null, bestD = 18 / view.scale;
    sim.nodes.forEach(function(n){
      var d = Math.hypot(n.x - wx, n.y - wy);
      if (d < bestD){ bestD = d; best = n; }
    });
    return best;
  }

  function initWrap(wrap){
    if (wrap.__memsubInit) return;
    wrap.__memsubInit = true;
    var canvas = wrap.querySelector("canvas.graph-canvas");
    if (!canvas) return;
    var mode = wrap.dataset.mode;
    var statusEl = document.getElementById(wrap.dataset.status || "");
    var searchEl = document.getElementById(wrap.dataset.search || "");
    var nsEl = document.getElementById(wrap.dataset.namespace || "");
    var periodEl = document.getElementById(wrap.dataset.period || "");
    var limitEl = document.getElementById(wrap.dataset.limit || "");

    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var view = { scale: 1, tx: 0, ty: 0 };
    var target = { scale: 1, tx: 0, ty: 0 };
    var opts = { q: "", selected: null, hovered: null, drift: !reducedMotion };
    var sim = null, settleFrames = 0, raf = null, rafIsTimeout = false, previewCard = null, clockStart = 0;
    var dragTarget = null, panning = false;

    function schedule(fn, active){
      rafIsTimeout = !active;
      return active ? requestAnimationFrame(fn) : setTimeout(fn, 90);
    }
    function cancelScheduled(id){
      if (id == null) return;
      if (rafIsTimeout) clearTimeout(id); else cancelAnimationFrame(id);
    }

    function size(){
      var rect = wrap.getBoundingClientRect();
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, rect.width * dpr);
      canvas.height = Math.max(1, rect.height * dpr);
      return { W: rect.width, H: rect.height, dpr: dpr };
    }

    function redraw(){
      var s = size();
      var ctx = canvas.getContext("2d");
      var clock = (performance.now() - clockStart) / 1000;
      draw(ctx, sim, s.W, s.H, view, s.dpr, opts, clock);
    }

    // smooth zoom-to-fit / zoom-to-cursor: wheel and window resize only set
    // `target`, this lerps `view` toward it a little every scheduled frame
    function lerpView(){
      var d = Math.abs(view.scale - target.scale) + Math.abs(view.tx - target.tx) + Math.abs(view.ty - target.ty);
      if (d < 0.02){ view.scale = target.scale; view.tx = target.tx; view.ty = target.ty; return false; }
      view.scale += (target.scale - view.scale) * 0.22;
      view.tx += (target.tx - view.tx) * 0.22;
      view.ty += (target.ty - view.ty) * 0.22;
      return true;
    }

    function fitView(s){
      if (!sim || !sim.nodes.length) return;
      var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      sim.nodes.forEach(function(n){
        minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
        minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
      });
      var w = Math.max(40, maxX - minX), h = Math.max(40, maxY - minY);
      var sc = clamp(Math.min(s.W / w, s.H / h) * 0.86, 0.15, 4);
      target.scale = sc;
      target.tx = s.W / 2 - (minX + maxX) / 2 * sc;
      target.ty = s.H / 2 - (minY + maxY) / 2 * sc;
      if (reducedMotion){ view.scale = target.scale; view.tx = target.tx; view.ty = target.ty; }
    }

    function animate(){
      var s = size();
      var settleCap = sim && sim.full ? 50 : 140;
      var stillSettling = settleFrames < settleCap;
      var stillZooming = lerpView();
      if (stillSettling){ step(sim, s.W, s.H); settleFrames++; }
      var active = stillSettling || stillZooming || !!dragTarget || panning;
      redraw();
      if (active || opts.drift){
        raf = schedule(animate, active);
      } else {
        raf = null;
      }
    }

    function start(data){
      var s = size();
      sim = makeSim(data, s.W, s.H, mode);
      settleFrames = 0;
      opts.selected = null; opts.hovered = null;
      hidePreview();
      clockStart = performance.now();
      if (sim.full){ fitView(s); } else { target.scale = view.scale = 1; target.tx = view.tx = 0; target.ty = view.ty = 0; }
      if (reducedMotion){
        var cap = sim.full ? 50 : 140;
        for (var k = 0; k < cap; k++){ step(sim, s.W, s.H); }
        settleFrames = cap;
      }
      cancelScheduled(raf);
      raf = schedule(animate, true);
    }

    function reheat(){
      settleFrames = 0;
      cancelScheduled(raf);
      raf = schedule(animate, true);
    }

    function hidePreview(){
      if (previewCard){ previewCard.remove(); previewCard = null; }
    }

    function showPreview(node, cssX, cssY){
      hidePreview();
      previewCard = document.createElement("div");
      previewCard.className = "graph-preview-card";
      var href = node.kind === "hub"
        ? "/?q=" + encodeURIComponent(node.label)
        : "/note/" + node.id.slice(5).split("/").map(encodeURIComponent).join("/");
      var sub = node.kind === "hub" ? node.degree + " note(s)" : "note";
      previewCard.innerHTML = "<strong>" + esc(node.label) + "</strong>" +
        "<span>" + esc(sub) + "</span><br><a href='" + href + "'>open &rarr;</a>";
      wrap.appendChild(previewCard);
      var rect = wrap.getBoundingClientRect();
      previewCard.style.left = Math.max(4, Math.min(cssX, rect.width - 200)) + "px";
      previewCard.style.top = Math.max(4, cssY) + "px";
    }

    // pointer interaction: hover = focus mode, drag node, pan background,
    // click = preview/open
    var activePointers = {};
    var moved = false, lastX = 0, lastY = 0;

    canvas.addEventListener("pointerdown", function(e){
      canvas.setPointerCapture(e.pointerId);
      activePointers[e.pointerId] = { x: e.offsetX, y: e.offsetY };
      moved = false;
      var hit = sim ? nodeAt(sim, view, e.offsetX, e.offsetY) : null;
      if (hit){
        dragTarget = hit; hit.fixed = true;
      } else {
        panning = true; lastX = e.offsetX; lastY = e.offsetY;
      }
      cancelScheduled(raf);
      raf = schedule(animate, true);
    });
    canvas.addEventListener("pointermove", function(e){
      if (!(e.pointerId in activePointers)){
        var hover = sim ? nodeAt(sim, view, e.offsetX, e.offsetY) : null;
        if (hover !== opts.hovered){ opts.hovered = hover; redraw(); }
        return;
      }
      activePointers[e.pointerId] = { x: e.offsetX, y: e.offsetY };
      if (dragTarget){
        moved = true;
        dragTarget.x = (e.offsetX - view.tx) / view.scale;
        dragTarget.y = (e.offsetY - view.ty) / view.scale;
        redraw();
      } else if (panning){
        moved = true;
        view.tx += e.offsetX - lastX; view.ty += e.offsetY - lastY;
        target.tx = view.tx; target.ty = view.ty;
        lastX = e.offsetX; lastY = e.offsetY;
        redraw();
      }
    });
    canvas.addEventListener("pointerleave", function(){
      if (opts.hovered){ opts.hovered = null; redraw(); }
    });
    function endPointer(e){
      delete activePointers[e.pointerId];
      if (dragTarget){
        if (!moved){
          hitTap(dragTarget, e.offsetX, e.offsetY);
        }
        dragTarget.fixed = false;
        dragTarget = null;
        reheat();
      } else if (panning && !moved){
        hidePreview();
        opts.selected = null;
        redraw();
      }
      panning = false;
    }
    canvas.addEventListener("pointerup", endPointer);
    canvas.addEventListener("pointercancel", endPointer);
    canvas.addEventListener("wheel", function(e){
      e.preventDefault();
      var factor = e.deltaY < 0 ? 1.15 : 0.87;
      var newScale = clamp(target.scale * factor, 0.15, 6);
      var wx = (e.offsetX - view.tx) / view.scale;
      var wy = (e.offsetY - view.ty) / view.scale;
      target.tx = e.offsetX - wx * newScale;
      target.ty = e.offsetY - wy * newScale;
      target.scale = newScale;
      if (reducedMotion){
        view.scale = target.scale; view.tx = target.tx; view.ty = target.ty;
        redraw();
        return;
      }
      cancelScheduled(raf);
      raf = schedule(animate, true);
    }, { passive: false });

    function hitTap(node, cssX, cssY){
      if (opts.selected === node){
        var href = node.kind === "hub"
          ? "/?q=" + encodeURIComponent(node.label)
          : "/note/" + node.id.slice(5).split("/").map(encodeURIComponent).join("/");
        window.location.href = href;
        return;
      }
      opts.selected = node;
      showPreview(node, cssX, cssY);
      redraw();
    }

    if (searchEl){
      searchEl.addEventListener("input", function(){
        opts.q = searchEl.value.trim().toLowerCase();
        redraw();
      });
    }

    function currentSrc(){
      var base = wrap.dataset.src;
      var params = new URLSearchParams(base.split("?")[1] || "");
      if (nsEl && nsEl.value) params.set("namespace", nsEl.value); else params.delete("namespace");
      if (periodEl && periodEl.value) params.set("period", periodEl.value); else params.delete("period");
      if (limitEl && limitEl.value) params.set("limit", limitEl.value);
      return base.split("?")[0] + "?" + params.toString();
    }

    function load(){
      fetch(currentSrc()).then(function(r){ return r.json(); }).then(function(data){
        if (statusEl){
          var msg = "showing " + data.shown_notes + " of " + data.total_notes + " note(s), ranked by connectivity";
          statusEl.textContent = msg;
        }
        start(data);
      }).catch(function(){});
    }

    [nsEl, periodEl, limitEl].forEach(function(el){
      if (el) el.addEventListener("change", load);
    });

    window.addEventListener("resize", function(){
      if (!sim) return;
      if (sim.full){ fitView(size()); }
      cancelScheduled(raf);
      raf = schedule(animate, true);
    });

    if (mode === "mini"){
      var script = wrap.querySelector("script.graph-data");
      var data = script ? JSON.parse(script.textContent) : { nodes: [], edges: [] };
      start(data);
    } else {
      load();
    }
  }

  document.querySelectorAll(".graph-canvas-wrap").forEach(initWrap);
})();
</script>
"""


def _page(
    title: str,
    body: str,
    include_mermaid: bool = False,
    note_count: int = 0,
    breadcrumb: str | None = None,
) -> bytes:
    mermaid = _MERMAID_INIT if include_mermaid else ""
    crumb = f'<span class="crumb">{html.escape(breadcrumb)}</span>' if breadcrumb else ""
    nav = (
        '<nav class="topnav">'
        '<a class="brand" href="/">memsub<span class="accent">::</span>vault</a>'
        f"{crumb}"
        '<span class="nav-spacer"></span>'
        '<a class="nav-btn" href="/ask">ask</a>'
        '<a class="nav-btn" href="/graph">graph</a>'
        '<a class="nav-btn" href="/random">random</a>'
        '<a class="nav-btn" href="/review">review queue</a>'
        "</nav>"
    )
    footer = (
        f'<footer class="stamp">memsub vault-serve v{SERVE_VERSION} '
        f"&middot; {note_count:,} note(s)</footer>"
    )
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
{nav}
<main class="page">
{body}
</main>
{footer}
{mermaid}
</body>
</html>
"""
    return html_doc.encode("utf-8")


def _epoch_from_iso(iso: str) -> float | None:
    if not iso:
        return None
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
    except ValueError:
        return None


def _render_kind_badges(fields: dict) -> str:
    """kind/status as small badges with hover text -- the provenance badge
    (observed/inferred/human-verified/quarantined) is the one prominent
    badge; these are extra machine vocabulary downgraded to small chips,
    per Layer 0 ("kind/type/status become at most small badges with hover
    text")."""
    chips = []
    for key in ("kind", "status"):
        val = fields.get(key)
        if val and not (key == "status" and val == "active"):
            chips.append(
                f'<span class="chip" title="{html.escape(key)}: {html.escape(val)}">{html.escape(val)}</span>'
            )
    return "".join(chips)


def _render_details_block(fields: dict, sections: NoteSections, idx: VaultIndex) -> str:
    """Every machine field (id, hashes, run/session ids, turn spans, backend,
    model, audit log) collapses into one closed <details> at the bottom --
    Layer 0's literal fix for Rustom's "i dont understand shit here"."""
    if not fields and not sections.provenance and not sections.audit:
        return ""
    rows = "".join(
        f"<span><strong>{html.escape(k)}</strong>: {html.escape(str(v))}</span>"
        for k, v in fields.items()
    )
    prov_html = render_markdown(sections.provenance, idx) if sections.provenance else ""
    audit_html = render_markdown(sections.audit, idx) if sections.audit else ""
    return (
        "<details class=\"note-details\"><summary>Details (ids, provenance, audit log)</summary>"
        f'<div class="meta-rows">{rows}</div>'
        f"{prov_html}"
        f"{('<h4>Audit events</h4>' + audit_html) if audit_html else ''}"
        "</details>"
    )


def _render_related_panel(relpath: str, idx: VaultIndex, sections: NoteSections, graph, sim_index) -> str:
    """Layer 3's related-ideas panel, split into two clearly labeled groups.
    "Already linked" doubles as Layer 0's fix for the entity link-soup
    ("shares entity Chat with <slug>" x8 -> one grouped, expandable, prose
    line per entity). "Similar but unlinked" is the discovery set: TF-IDF
    cosine, top 5, excluding anything already sharing a wikilink or entity
    -- computed once at server start (memsub/vault_similarity.py)."""
    linked_html = ""
    if sections.links:
        groups = group_related_entities(sections.links, idx, relpath)
        if groups:
            items = []
            for g in groups:
                members_html = "".join(
                    f'<li><a href="/note/{urllib.parse.quote(rel)}">{html.escape(title)}</a></li>'
                    for rel, title in g.members
                )
                plural = "s" if g.count != 1 else ""
                ns_note = ""
                items.append(
                    "<details class=\"related-group\">"
                    f"<summary>Related: {g.count} note{plural} about "
                    f"{html.escape(g.entity)}{ns_note}</summary>"
                    f'<ul class="related-list">{members_html}</ul></details>'
                )
            linked_html = "".join(items)
    if not linked_html:
        linked_html = '<p class="count">no linked notes yet</p>'

    unlinked_html = ""
    if sim_index is not None:
        candidates = related_ideas(relpath, sim_index, idx)
        if candidates:
            items = "".join(
                '<li class="related-card">'
                f'<a href="/note/{urllib.parse.quote(c["relpath"])}">{html.escape(c["title"])}</a>'
                + (f'<p class="why">shares: {html.escape(c["why"])}</p>' if c["why"] else "")
                + "</li>"
                for c in candidates
            )
            unlinked_html = f'<ul class="related-list discovery">{items}</ul>'
    if not unlinked_html:
        unlinked_html = '<p class="count">no similar-but-unlinked notes found yet</p>'

    return (
        '<section class="related-panel">'
        "<h3>Related ideas</h3>"
        '<div class="related-block"><h4>Already linked</h4>' + linked_html + "</div>"
        '<div class="related-block"><h4>Similar but unlinked <span class="count">(discovery)</span></h4>'
        + unlinked_html
        + "</div>"
        "</section>"
    )


def _render_constellation(relpath: str, graph) -> str:
    """Mini constellation: this note's immediate neighborhood, canvas,
    tap-through. Embedded inline (no extra HTTP round trip) since the graph
    was already built once at server start."""
    if graph is None:
        return ""
    data = note_constellation(graph, relpath)
    if len(data.get("nodes", [])) <= 1:
        return ""
    payload = json.dumps(data)
    return (
        '<section class="constellation">'
        "<h3>Constellation</h3>"
        '<div class="graph-canvas-wrap" data-mode="mini">'
        f'<script type="application/json" class="graph-data">{payload}</script>'
        '<canvas class="graph-canvas" height="260"></canvas>'
        '<p class="graph-hint">drag to explore &middot; tap a node to preview &middot; tap again to open</p>'
        "</div></section>"
        f"{_GRAPH_JS}"
    )


def render_note_page(relpath: str, idx: VaultIndex, graph=None, sim_index=None) -> bytes:
    text = idx.notes[relpath]
    fields, body = split_frontmatter(text)
    sections = split_note_sections(body)
    main_md = sections.content or body

    title = note_title(relpath, idx)
    badge = _provenance_badge(fields) if fields else ""
    created_ts = _epoch_from_iso(fields.get("created", ""))
    project_words = namespace_in_words(fields.get("namespace", "")) if fields.get("namespace") else ""

    meta_html = ""
    if fields:
        badge_html = f'<span class="badge badge-{badge}">{badge}</span>' if badge else ""
        date_html = f'<span class="meta-date">{html.escape(_relative_time(created_ts))}</span>' if created_ts else ""
        project_html = f'<span class="meta-project">from {html.escape(project_words)}</span>' if project_words else ""
        kind_badges = _render_kind_badges(fields)
        meta_html = (
            '<div class="meta-strip">'
            f"{badge_html}{date_html}{project_html}{kind_badges}"
            "</div>"
        )

    editorial = parse_editorial_links(sections.links) if sections.links else []
    promoted_from = next((t for k, t in editorial if k == "promoted from"), None)
    promoted_html = ""
    if promoted_from:
        resolved = resolve_wikilink(promoted_from, idx)
        if resolved:
            promoted_html = (
                f'<p class="promoted-from">Promoted from <a href="/note/{urllib.parse.quote(resolved)}">'
                f"{html.escape(note_title(resolved, idx))}</a></p>"
            )

    body_html = render_markdown(main_md, idx)
    chain = supersede_chain(relpath, idx)
    timeline_html = _render_supersede_timeline(chain, relpath, idx)
    related_html = _render_related_panel(relpath, idx, sections, graph, sim_index)
    constellation_html = _render_constellation(relpath, graph)
    details_html = _render_details_block(fields, sections, idx)

    folder = str(Path(relpath).parent) if Path(relpath).parent != Path(".") else ""
    breadcrumb = f"{folder} / {title}" if folder else title

    preview_js = _WIKILINK_PREVIEW_JS if '<a class="wikilink"' in body_html else ""
    content = (
        f"<h1>{html.escape(title)}</h1>"
        f"{meta_html}"
        f"{promoted_html}"
        f'<div class="note-body">{body_html}</div>'
        f"{timeline_html}"
        f"{constellation_html}"
        f"{related_html}"
        f"{details_html}"
        f"{preview_js}"
    )
    return _page(
        title,
        content,
        include_mermaid="```mermaid" in body,
        note_count=len(idx.notes),
        breadcrumb=breadcrumb,
    )


def render_graph_page(idx: VaultIndex, graph) -> bytes:
    """Layer 2: /graph -- interactive force-directed canvas graph. Namespace
    filter, this-week/this-month time filter, search-highlight, drag/zoom/
    pan, tap-to-preview/tap-through. Node data comes from /api/graph.json,
    already capped with an honest "showing top N of M" status line."""
    namespaces = sorted({n.namespace for n in graph.notes.values() if n.namespace}) if graph else []
    ns_options = "".join(
        f'<option value="{html.escape(ns)}">{html.escape(namespace_in_words(ns))}</option>' for ns in namespaces
    )
    total_notes = len(graph.notes) if graph else 0
    total_hubs = len(graph.hubs) if graph else 0
    empty_note = "" if total_notes else '<p class="count">no notes indexed yet.</p>'
    content = (
        "<h1>Graph</h1>"
        f'<p class="count">{total_notes:,} note(s) &middot; {total_hubs} hub entit{"y" if total_hubs == 1 else "ies"}</p>'
        '<div class="graph-legend">'
        '<span><span class="dot" style="background:#e4e7ee;box-shadow:0 0 6px #e4e7ee88;'
        'border:1px solid rgba(240,242,246,0.7)"></span>hub (biggest entities, ringed)</span>'
        f'<span>note, colored by topic cluster: {_CLUSTER_SWATCHES}</span>'
        "</div>"
        '<div class="graph-controls">'
        '<input type="text" id="graph-search" placeholder="search node labels...">'
        f'<select id="graph-namespace"><option value="">all namespaces</option>{ns_options}</select>'
        '<select id="graph-period"><option value="">all time</option>'
        '<option value="week">this week</option><option value="month">this month</option></select>'
        '<select id="graph-limit"><option value="150">top 150</option><option value="300">top 300</option>'
        f'<option value="600">top 600</option><option value="{MAX_NODE_LIMIT}">top {MAX_NODE_LIMIT} (max)</option></select>'
        "</div>"
        '<p class="graph-status" id="graph-status">loading&hellip;</p>'
        '<div class="graph-canvas-wrap graph-page" data-mode="full" data-src="/api/graph.json?limit=150" '
        'data-status="graph-status" data-search="graph-search" data-namespace="graph-namespace" '
        'data-period="graph-period" data-limit="graph-limit">'
        '<canvas class="graph-canvas"></canvas>'
        "</div>"
        f"{empty_note}"
        f"{_GRAPH_JS}"
    )
    return _page("Graph", content, note_count=len(idx.notes), breadcrumb="graph")


# --------------------------------------------------------------------------- #
# "Ask your brain": /ask page + /api/ask (memsub/ask.py owns the flow --
# retrieval, sufficiency gate, prompt, citation parsing; this file only owns
# the page chrome and the route)
# --------------------------------------------------------------------------- #
_ASK_JS = """
<script>
(function(){
  var form = document.getElementById('ask-form');
  var input = document.getElementById('ask-input');
  var button = document.getElementById('ask-submit');
  var status = document.getElementById('ask-status');
  var conversation = document.getElementById('ask-conversation');
  if (!form) return;

  function esc(s){ var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  function renderTurn(question, data){
    var turn = document.createElement('div');
    turn.className = 'ask-turn';
    var qEl = document.createElement('div');
    qEl.className = 'ask-question';
    qEl.textContent = question;
    turn.appendChild(qEl);

    var body = document.createElement('div');
    body.className = 'ask-answer';
    if (data.error) {
      body.innerHTML = '<p class="ask-error">' + esc(data.error) + '</p>';
    } else if (!data.sufficient) {
      body.innerHTML = '<p class="ask-refusal">' + esc(data.message) + '</p>' +
        (data.nearest_html ? '<p class="ask-meta">maybe related:</p>' + data.nearest_html : '');
    } else {
      body.innerHTML = '<p>' + data.answer_html + '</p>' +
        (data.sources_html ? '<p class="ask-meta">sources:</p>' + data.sources_html : '');
    }
    if (typeof data.latency_ms === 'number') {
      var meta = document.createElement('p');
      meta.className = 'ask-meta';
      meta.textContent = (data.backend || 'local model') + ' · ' + data.latency_ms + ' ms';
      body.appendChild(meta);
    }
    turn.appendChild(body);
    conversation.insertBefore(turn, conversation.firstChild);
  }

  function ask(question){
    if (!question) return;
    button.disabled = true;
    status.style.display = '';
    status.textContent = 'thinking...';
    fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question })
    }).then(function(resp){ return resp.json(); }).then(function(data){
      renderTurn(question, data);
    }).catch(function(err){
      renderTurn(question, { error: 'request failed: ' + err });
    }).then(function(){
      button.disabled = false;
      status.style.display = 'none';
      input.value = '';
      input.focus();
    });
  }

  form.addEventListener('submit', function(ev){
    ev.preventDefault();
    ask(input.value.trim());
  });

  document.querySelectorAll('.ask-chip').forEach(function(chip){
    chip.addEventListener('click', function(){
      ask(chip.getAttribute('data-q'));
    });
  });
})();
</script>
"""


def _ask_sample_chips(graph) -> str:
    """Sample question chips filled from the graph's top entities (same
    source `_render_brain_section` already uses for entity chips)."""
    if graph is None or not graph.hubs:
        return ""
    chips = []
    for entity in list(graph.hubs.keys())[:6]:
        q = f"what did I decide about {entity}"
        chips.append(f'<span class="ask-chip" data-q="{html.escape(q)}">{html.escape(q)}</span>')
    return f'<div class="ask-chips">{"".join(chips)}</div>'


def render_ask_page(idx: VaultIndex, graph=None) -> bytes:
    content = (
        "<h1>Ask your brain</h1>"
        '<p class="count">ask a question; answers are grounded only in your own notes, with citations.</p>'
        f"{_ask_sample_chips(graph)}"
        '<form id="ask-form" class="ask-form">'
        '<input type="text" id="ask-input" autocomplete="off" placeholder="what did I decide about...">'
        '<button type="submit" id="ask-submit">ask</button>'
        "</form>"
        '<p class="ask-status" id="ask-status" style="display:none">thinking...</p>'
        '<div id="ask-conversation"></div>'
        f"{_ASK_JS}"
    )
    return _page("Ask your brain", content, note_count=len(idx.notes), breadcrumb="ask")


def render_index_page(
    idx: VaultIndex, query: str = "", db_path: Path | None = None, graph=None, sim_index=None
) -> bytes:
    by_folder: dict[str, list] = {}
    sections: list[str] = []

    if query:
        results = search_results(idx, query)
        heading = f"{len(results)} result(s) for &ldquo;{html.escape(query)}&rdquo;"
        for r in results:
            by_folder.setdefault(r["folder"], []).append(r)
        for folder in sorted(by_folder):
            items = "".join(
                f'<li><a class="result-item" href="/note/{urllib.parse.quote(r["relpath"])}">'
                f'{html.escape(r["title"])}<span class="snippet">{r["snippet_html"]}</span></a></li>'
                for r in sorted(by_folder[folder], key=lambda r: r["relpath"])
            )
            sections.append(f'<div class="folder"><h2>{html.escape(folder)}</h2><ul class="notelist">{items}</ul></div>')
    else:
        heading = f"{len(idx.notes)} note(s)"
        for rel in sorted(idx.notes):
            folder = str(Path(rel).parent) if Path(rel).parent != Path(".") else "(root)"
            by_folder.setdefault(folder, []).append(rel)
        for folder in sorted(by_folder):
            items = "".join(
                f'<li><a href="/note/{urllib.parse.quote(rel)}">{html.escape(note_title(rel, idx))}</a></li>'
                for rel in sorted(by_folder[folder])
            )
            sections.append(f'<div class="folder"><h2>{html.escape(folder)}</h2><ul class="notelist">{items}</ul></div>')

    dashboard_html = _render_dashboard(idx, db_path, graph, sim_index)
    content = (
        f"{dashboard_html}"
        '<section class="search-section">'
        '<form action="/" method="get" id="search-form">'
        '<input type="text" name="q" id="search-input" placeholder="search filenames and content..." '
        f'value="{html.escape(query)}" autocomplete="off">'
        "</form>"
        f'<p class="count" id="results-count">{heading}</p>'
        f'<div id="results">{"".join(sections)}</div>'
        "</section>"
        f"{_DASHBOARD_JS}"
        f"{_SEARCH_JS}"
    )
    return _page("memsub vault", content, note_count=len(idx.notes))


# --------------------------------------------------------------------------- #
# HTTP server (stdlib only)
# --------------------------------------------------------------------------- #
def make_handler(
    idx: VaultIndex,
    db_path: Path | None = None,
    graph=None,
    sim_index=None,
    ask_backend=None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "memsub-vault-serve/2.0"

        def log_message(self, fmt, *args):  # quieter default logging
            pass

        def _send(self, body: bytes, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 (stdlib-mandated name)
            parsed = urllib.parse.urlparse(self.path)

            if parsed.path == "/":
                query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
                self._send(render_index_page(idx, query, db_path, graph, sim_index))
                return

            if parsed.path == "/random":
                if not idx.notes:
                    self._send(b"no notes", status=404, content_type="text/plain")
                    return
                rel = random.choice(list(idx.notes.keys()))
                self._redirect("/note/" + urllib.parse.quote(rel))
                return

            if parsed.path == "/review":
                self._send(render_review_page(idx))
                return

            if parsed.path == "/graph":
                self._send(render_graph_page(idx, graph))
                return

            if parsed.path == "/ask":
                self._send(render_ask_page(idx, graph))
                return

            if parsed.path == "/api/graph.json":
                if graph is None:
                    payload = json.dumps(
                        {"nodes": [], "edges": [], "total_notes": 0, "shown_notes": 0, "truncated": False}
                    ).encode("utf-8")
                    self._send(payload, content_type="application/json; charset=utf-8")
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                namespace = qs.get("namespace", [""])[0]
                period = qs.get("period", [""])[0]
                try:
                    limit = int(qs.get("limit", [str(DEFAULT_NODE_LIMIT)])[0])
                except ValueError:
                    limit = DEFAULT_NODE_LIMIT
                payload = json.dumps(filter_and_cap(graph, namespace=namespace, period=period, limit=limit)).encode(
                    "utf-8"
                )
                self._send(payload, content_type="application/json; charset=utf-8")
                return

            if parsed.path == "/api/search.json":
                query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
                results = search_results(idx, query) if query else []
                payload = json.dumps({"results": results, "total": len(results)}).encode("utf-8")
                self._send(payload, content_type="application/json; charset=utf-8")
                return

            if parsed.path.startswith("/api/preview/"):
                rel = parsed.path[len("/api/preview/"):]
                resolved = safe_path(idx.root, rel)
                if resolved is None:
                    self._send(b"{}", status=404, content_type="application/json; charset=utf-8")
                    return
                relpath = resolved.relative_to(idx.root).as_posix()
                if relpath not in idx.notes:
                    self._send(b"{}", status=404, content_type="application/json; charset=utf-8")
                    return
                fields, body = split_frontmatter(idx.notes[relpath])
                snippet = " ".join(body.strip().split())[:220]
                payload = json.dumps({
                    "title": note_title(relpath, idx),
                    "snippet": snippet,
                    "badge": _provenance_badge(fields),
                }).encode("utf-8")
                self._send(payload, content_type="application/json; charset=utf-8")
                return

            if parsed.path.startswith("/note/"):
                rel = parsed.path[len("/note/"):]
                resolved = safe_path(idx.root, rel)
                if resolved is None:
                    self._send(b"not found", status=404, content_type="text/plain")
                    return
                relpath = resolved.relative_to(idx.root).as_posix()
                if relpath not in idx.notes:
                    # file exists on disk but wasn't indexed at startup (e.g.
                    # non-.md file reached via a crafted /note/ path); refuse
                    self._send(b"not found", status=404, content_type="text/plain")
                    return
                self._send(render_note_page(relpath, idx, graph, sim_index))
                return

            self._send(b"not found", status=404, content_type="text/plain")

        def do_POST(self) -> None:  # noqa: N802 (stdlib-mandated name)
            parsed = urllib.parse.urlparse(self.path)

            if parsed.path == "/api/ask":
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = {}
                question = str(data.get("question", ""))
                if db_path is None or not db_path.exists():
                    payload = json.dumps(
                        {
                            "question": question.strip(),
                            "sufficient": False,
                            "message": (
                                "no local store is configured for this vault-serve "
                                "instance (vault-only mode) -- ask needs --db."
                            ),
                            "nearest_html": "",
                            "latency_ms": 0,
                        }
                    ).encode("utf-8")
                    self._send(payload, content_type="application/json; charset=utf-8")
                    return
                # A fresh MemoryClient per request, not one built once at server
                # startup: EpisodicStore's sqlite connection can only be used
                # from the thread that created it (confirmed live -- sqlite3
                # raises "objects created in a thread can only be used in that
                # same thread"), and ThreadingTCPServer hands each request its
                # own thread. Same per-request-open pattern the dashboard already
                # uses (_open_store_readonly). Cold HybridRetriever build is
                # ~0.65s against the live ~5k-episode store -- negligible next to
                # the multi-second local LLM call that follows.
                namespaces = sorted({ns for ns, _ in _namespace_breakdown(idx)}) or ["default"]
                clients = [
                    MemoryClient(db_path=db_path, namespace=ns, retrieval="hybrid") for ns in namespaces
                ]
                result = ask_mod.answer(question, clients, idx, ask_backend)
                self._send(json.dumps(result).encode("utf-8"), content_type="application/json; charset=utf-8")
                return

            self._send(b"not found", status=404, content_type="text/plain")

    return Handler


def serve_vault(
    root: Path,
    host: str = "127.0.0.1",
    port: int = 8766,
    db_path: Path | str | None = None,
) -> int:
    root = root.expanduser().resolve()
    if not root.is_dir():
        print(f"no such directory: {root}")
        return 1
    idx = build_index(root)
    # Layer 2/3: built once at startup, same "no live reload" contract the
    # note index already has (WORKLOG.md 2026-07-20 assumption 4). Read-only,
    # vault-text-derived -- no store access (assumption 2).
    graph = build_graph_index(idx)
    sim_index = build_similarity_index(idx, linked_neighbors(graph))
    resolved_db = Path(db_path).expanduser() if db_path else None
    ask_backend = None
    if resolved_db and resolved_db.exists():
        try:
            # Hardcoded "ollama", never "auto": "auto" prefers a cloud API
            # key if ANTHROPIC_API_KEY happens to be set, which would break
            # this feature's "zero cloud" requirement regardless of env. The
            # backend itself holds no sqlite state, so it is safe to build
            # once here and reuse across request threads (unlike the
            # per-request MemoryClients in do_POST -- see that comment).
            ask_backend = build_backend(prefer="ollama", model="qwen3:8b")
        except RuntimeError as exc:
            print(f"ask: local ollama backend unavailable ({exc}); /ask will show an error until it is")
    handler = make_handler(idx, resolved_db, graph, sim_index, ask_backend)
    with socketserver.ThreadingTCPServer((host, port), handler) as httpd:
        db_msg = (
            f", dashboard store: {resolved_db}"
            if resolved_db and resolved_db.exists()
            else ", dashboard: vault-only mode"
        )
        print(
            f"memsub vault serve: {len(idx.notes)} note(s) from {root}{db_msg}, "
            f"graph: {len(graph.notes)} note node(s) / {len(graph.hubs)} hub(s)"
        )
        print(f"listening on http://{host}:{port} (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
