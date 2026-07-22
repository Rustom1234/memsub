"""memsub garden: a publishable digital-garden static export from a memory
vault.

Design source: 2026-07-22 research pass ("nobody ships this: AI-vault
generators stop at local markdown, garden publishers start from
hand-written notes"). Five trust rules, each fail-closed by construction:

1. Allowlist, never denylist: a note is published ONLY with explicit
   `publish: true` frontmatter. Everything else is invisible to this module
   -- an unflagged note is never even inspected past that one check.
2. Asset leak guard: a non-markdown file referenced by a published note's
   `![alt](path)` / `[text](path)` is copied ONLY if the reference resolves
   to a real file under the vault root. Every other asset on disk, even one
   sitting right next to a published note, is excluded.
3. No silent publish: `garden build` writes to a local output directory
   only (no network, no cron, ever) and requires `garden review` to have
   approved the current publish set first (a content-hash manifest at
   `<vault>/garden.json`); an unreviewed addition or content change fails
   the build unless `--yes`.
4. PII pass: a heuristic regex scan runs on every note about to enter the
   publish set; a hit blocks that one note (excluded from the build,
   reported by name) rather than being silently rewritten. Scrubbing is not
   the design -- the block, plus a human looking again, is.
5. Garden norms from provenance: `user_stated` (or human-confirmed) notes
   render "evergreen" with a verified badge; `llm_inferred` notes render
   "seedling, inferred, unverified" and additionally require
   `publish_inferred: true` in frontmatter before they are even eligible
   (missing that flag reads the same as never having set `publish: true`).

Stdlib-only, mirrors memsub/vault_serve.py's approach (a small line-based
markdown renderer, wikilinks resolved to stable URLs, unresolved wikilinks
render as plain text) but is its own renderer: this module never imports
vault_serve's `render_markdown` because that function's wikilink hrefs and
"note not found" wording are tied to vault_serve's own `/note/<relpath>`
serving scheme, not this module's `id`-prefix URLs, and because a garden's
wikilink universe (only the published set) is deliberately narrower than a
served vault's (every note on disk). Pure-syntax regexes (bold/italic/code/
headers/lists/etc.) are imported from vault_serve rather than re-literalled,
since those are content-agnostic constants with no VaultIndex coupling.
Frontmatter parsing (`split_frontmatter`) and section/title resolution
(`split_note_sections`, `note_title`) are reused as-is for the same reason.

vault_serve.py itself is never edited by this module (owned by another
agent this cycle).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from .vault_notes import note_title, split_note_sections
from .vault_serve import (
    _BOLD_RE,
    _CODE_RE,
    _FENCE_RE,
    _HEADER_RE,
    _HR_RE,
    _ITALIC_RE,
    _LIST_RE,
    _MD_LINK_RE,
    _OL_RE,
    _QUOTE_RE,
    split_frontmatter,
)

MANIFEST_NAME = "garden.json"
DEFAULT_SITE_DIRNAME = "garden-site"

CHANNEL_LLM_INFERRED = "llm_inferred"
CHANNEL_USER_STATED = "user_stated"

STATUS_PUBLISHED = "published"
STATUS_BLOCKED_PII = "blocked_pii"
STATUS_INFERRED_AWAITING_FLAG = "inferred_awaiting_flag"

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
_COMMENT_RE = re.compile(r"^<!--.*-->$")

# --------------------------------------------------------------------------- #
# PII heuristics: findings BLOCK a note from the publish set, they never
# rewrite content. Scanned only against a note's own prose (## Content
# section), never its machine frontmatter/provenance block, so a note's own
# `content_hash`/`id` fields (16-40 hex chars) never self-trigger the
# generic long-token rule below.
# --------------------------------------------------------------------------- #
_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email address", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),
    ("phone number", re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")),
    (
        "IP address",
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
    ),
    (
        "API-key-shaped token",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}"
            # a generic 32+ char run with no whitespace, kept out of false
            # positives on long hyphenated English phrases by requiring at
            # least one digit somewhere in the run (real keys/hashes almost
            # always have one, a compound-word slug rarely does)
            r"|(?=[A-Za-z0-9_\-]{32,}\b)[A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*)\b"
        ),
    ),
    (
        "credential-looking string",
        re.compile(r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*\S+"),
    ),
]


def scan_for_pii(text: str) -> list[str]:
    """Human-readable finding labels for every heuristic that hit. Empty
    list means clean. Heuristic, not a guarantee -- see docs does-NOT-do."""
    findings = []
    for label, pattern in _PII_PATTERNS:
        if pattern.search(text):
            findings.append(label)
    return findings


# --------------------------------------------------------------------------- #
# Publish-set resolution
# --------------------------------------------------------------------------- #
def _parse_bool(value: str | None) -> bool:
    return str(value).strip().lower() == "true"


def _iso_to_epoch(iso: str) -> float | None:
    if not iso:
        return None
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def _id_prefix(relpath: str, fields: dict) -> str:
    """Stable URL slug: the memory id's first 8 hex chars (matching the
    filename suffix memsub/vault.py already writes), never the title or
    slugified content -- so a note surviving a title/content-sentence edit
    keeps the same garden URL. Notes with no `id` frontmatter field (e.g.
    hand-authored or topic pages) fall back to a hash of the relpath, which
    is still stable across a title change (title is not part of relpath)."""
    fid = fields.get("id", "")
    if fid:
        return fid[:8]
    return hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:8]


@dataclass
class GardenNote:
    relpath: str
    fields: dict
    body: str  # published prose only: `## Content` section, or full body when there is no such section
    title: str
    id_prefix: str
    channel: str | None
    publish_inferred_flag: bool
    stage: str  # "evergreen" | "seedling"
    verified: bool
    pii_findings: list[str]
    planted: float | None
    last_tended: float | None
    content_hash: str
    status: str  # published | blocked_pii | inferred_awaiting_flag


class _TitleShim:
    """The one-entry stand-in vault_notes.note_title() needs: it only ever
    reads `idx.notes[relpath]` for the relpath it was asked about, never any
    other note, so a real VaultIndex is unnecessary here."""

    def __init__(self, relpath: str, text: str) -> None:
        self.notes = {relpath: text}
        self.frontmatter: dict = {}


def _dates(fields: dict, store, episode_id: str | None) -> tuple[float | None, float | None]:
    """Planted = first consolidation ts, last tended = latest event ts for
    that memory. Store-backed when a store is given and the note's id
    resolves in it (the authoritative source); file frontmatter otherwise
    (`created` / `last_updated`, the same fields memsub/vault.py writes)."""
    if store is not None and episode_id:
        ep = store.get(episode_id)
        if ep is not None:
            events = store.events(episode_id=episode_id)
            last_tended = events[0].ts if events else ep.ts
            return ep.ts, last_tended
    planted = _iso_to_epoch(fields.get("created", ""))
    last_tended = _iso_to_epoch(fields.get("last_updated", "")) or planted
    return planted, last_tended


def resolve_publish_set(vault_root: Path, store=None) -> dict[str, GardenNote]:
    """Scan every markdown file under `vault_root` and return only the ones
    with explicit `publish: true` frontmatter (fail-closed allowlist -- a
    note with no `publish` key, or `publish: false`, is never even wrapped
    in a GardenNote, let alone published). Each returned note carries its
    own status: published, blocked_pii, or inferred_awaiting_flag."""
    notes: dict[str, GardenNote] = {}
    for f in sorted(vault_root.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        fields, body = split_frontmatter(text)
        if not _parse_bool(fields.get("publish")):
            continue  # the allowlist gate: everything else stops here

        relpath = f.relative_to(vault_root).as_posix()
        channel = fields.get("channel") or None
        publish_inferred_flag = _parse_bool(fields.get("publish_inferred"))
        sections = split_note_sections(body)
        # What actually gets published: the `## Content` prose only, never
        # the machine Provenance/Audit-events/Links sections memsub/vault.py
        # also writes underneath it (run/session ids, HMAC channel_sig,
        # audit timestamps, algorithmic "shares entity" bullets -- none of
        # that is meant for a public reader; vault_serve.py collapses the
        # same fields into a closed <details> block for the same reason).
        # A note with no `## Content` header at all (hand-authored pages,
        # demo/vault/topics/*.md) falls back to its full body, since there
        # is no machine section to strip in the first place.
        content_md = sections.content or body
        title = note_title(relpath, _TitleShim(relpath, text))
        stage = "seedling" if channel == CHANNEL_LLM_INFERRED else "evergreen"
        verified = channel == CHANNEL_USER_STATED or _parse_bool(fields.get("human_confirmed"))
        id_prefix = _id_prefix(relpath, fields)
        planted, last_tended = _dates(fields, store, fields.get("id"))
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

        if channel == CHANNEL_LLM_INFERRED and not publish_inferred_flag:
            status = STATUS_INFERRED_AWAITING_FLAG
            pii_findings: list[str] = []
        else:
            # wikilink targets (id-suffixed slugs) are internal note
            # references, not prose -- stripped before scanning so a
            # `[[...--a1b2c3d4]]` target never trips the API-key-shaped
            # heuristic below (measured false-positive on demo/vault's
            # topics/*.md pages, which embed wikilinks inline in prose).
            scan_text = _WIKILINK_RE.sub(" ", content_md)
            pii_findings = scan_for_pii(scan_text)
            status = STATUS_BLOCKED_PII if pii_findings else STATUS_PUBLISHED

        notes[relpath] = GardenNote(
            relpath=relpath,
            fields=fields,
            body=content_md,
            title=title,
            id_prefix=id_prefix,
            channel=channel,
            publish_inferred_flag=publish_inferred_flag,
            stage=stage,
            verified=verified,
            pii_findings=pii_findings,
            planted=planted,
            last_tended=last_tended,
            content_hash=content_hash,
            status=status,
        )
    return notes


def status_counts(notes: dict[str, GardenNote]) -> dict[str, int]:
    counts = {STATUS_PUBLISHED: 0, STATUS_BLOCKED_PII: 0, STATUS_INFERRED_AWAITING_FLAG: 0}
    for note in notes.values():
        counts[note.status] += 1
    return counts


# --------------------------------------------------------------------------- #
# Review manifest: garden.json at the vault root
# --------------------------------------------------------------------------- #
def _manifest_path(vault_root: Path) -> Path:
    return vault_root / MANIFEST_NAME


def load_manifest(vault_root: Path) -> dict:
    p = _manifest_path(vault_root)
    if not p.exists():
        return {"version": 1, "approved": {}, "approved_at": None, "last_build": {}, "last_build_at": None}
    return json.loads(p.read_text(encoding="utf-8"))


def save_manifest(vault_root: Path, manifest: dict) -> None:
    _manifest_path(vault_root).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _published_hashes(notes: dict[str, GardenNote]) -> dict[str, str]:
    return {rel: n.content_hash for rel, n in notes.items() if n.status == STATUS_PUBLISHED}


@dataclass
class ReviewDiff:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.added or self.changed or self.removed)


def diff_publish_set(current: dict[str, str], approved: dict[str, str]) -> ReviewDiff:
    added = sorted(rel for rel in current if rel not in approved)
    changed = sorted(rel for rel in current if rel in approved and current[rel] != approved[rel])
    removed = sorted(rel for rel in approved if rel not in current)
    return ReviewDiff(added=added, changed=changed, removed=removed)


@dataclass
class ReviewResult:
    diff: ReviewDiff
    approved_count: int


def review(vault_root: Path, store=None) -> ReviewResult:
    """Compute the pending diff against the last approval, record the
    current published set as approved, and return the diff that was just
    approved (for the CLI to print before it's overwritten)."""
    notes = resolve_publish_set(vault_root, store=store)
    current = _published_hashes(notes)
    manifest = load_manifest(vault_root)
    diff = diff_publish_set(current, manifest.get("approved", {}))
    manifest["approved"] = current
    manifest["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_manifest(vault_root, manifest)
    return ReviewResult(diff=diff, approved_count=len(current))


# --------------------------------------------------------------------------- #
# Asset leak guard
# --------------------------------------------------------------------------- #
_ASSET_REF_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def _referenced_assets(vault_root: Path, relpath: str, body: str) -> list[Path]:
    """Non-markdown, non-URL relative references in a note's body that
    resolve to a real file under vault_root. Anything else (a URL, a path
    that escapes the vault, a path that does not exist) is silently
    excluded -- this is the only way an asset is ever selected for copying."""
    note_dir = (vault_root / relpath).parent
    vault_resolved = vault_root.resolve()
    found: list[Path] = []
    for m in _ASSET_REF_RE.finditer(body):
        ref = m.group(1)
        if ref.startswith(("http://", "https://", "mailto:", "/")) or ref.endswith(".md"):
            continue
        try:
            candidate = (note_dir / urllib.parse.unquote(ref)).resolve()
        except (OSError, RuntimeError):
            continue
        if candidate != vault_resolved and vault_resolved not in candidate.parents:
            continue  # traversal attempt: excluded, never copied
        if candidate.is_file():
            found.append(candidate)
    return found


def _copy_assets(vault_root: Path, published: dict[str, GardenNote], out_dir: Path) -> int:
    copied = 0
    seen: set[Path] = set()
    vault_resolved = vault_root.resolve()
    for note in published.values():
        for asset in _referenced_assets(vault_root, note.relpath, note.body):
            if asset in seen:
                continue
            seen.add(asset)
            rel_to_vault = asset.relative_to(vault_resolved)
            dest = out_dir / "assets" / rel_to_vault
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(asset.read_bytes())
            copied += 1
    return copied


# --------------------------------------------------------------------------- #
# Rendering: a compact, independent markdown-to-HTML pass (see module
# docstring for why this does not import vault_serve.render_markdown)
# --------------------------------------------------------------------------- #
def _note_url(note: GardenNote) -> str:
    return f"notes/{note.id_prefix}.html"


def _inline(text: str, resolve) -> str:
    text = html.escape(text, quote=False)

    def _wikilink(m: re.Match) -> str:
        target, alias = m.group(1), m.group(2)
        display = html.escape((alias or target).strip())
        resolved = resolve(target)
        if resolved is None:
            return f'<span class="wikilink private" title="private, not published">{display}</span>'
        return f'<a class="wikilink" href="{_note_url(resolved)}">{display}</a>'

    text = _WIKILINK_RE.sub(_wikilink, text)
    text = _MD_LINK_RE.sub(lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', text)
    text = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    return text


def render_markdown(text: str, resolve) -> str:
    """`resolve(target: str) -> GardenNote | None`: only notes in the
    published set ever resolve, so a wikilink to an unpublished (or
    nonexistent) note always renders as plain, private-marked text."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    para: list[str] = []
    list_stack: list[str] = []

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para), resolve)}</p>")
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
            i += 1
            body_lines: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                body_lines.append(lines[i])
                i += 1
            i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(body_lines))}</code></pre>")
            continue

        if not line.strip() or _COMMENT_RE.match(line.strip()):
            # `<!-- memsub:distill:begin/end -->` markers memsub/distill.py
            # writes around a topic page's body: a machine bookkeeping
            # marker, never meant to display, so it is skipped like a blank
            # line rather than shown as literal escaped text.
            flush_para()
            close_lists()
            i += 1
            continue

        header_m = _HEADER_RE.match(line)
        if header_m:
            flush_para()
            close_lists()
            level = len(header_m.group(1))
            out.append(f"<h{level}>{_inline(header_m.group(2), resolve)}</h{level}>")
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
            out.append(f"<blockquote>{_inline(quote_m.group(1), resolve)}</blockquote>")
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
            out.append(f"<li>{_inline(item_text, resolve)}</li>")
            i += 1
            continue

        close_lists()
        para.append(line.strip())
        i += 1

    flush_para()
    close_lists()
    return "\n".join(out)


_CSS = """
:root { color-scheme: dark light; }
body { background:#14161a; color:#d7dadf; font:16px/1.6 -apple-system,
  BlinkMacSystemFont, "Segoe UI", sans-serif; margin:0; }
main { max-width: 760px; margin: 0 auto; padding: 24px 20px 60px; }
a { color:#7cb0ff; }
a.wikilink.private { color:#8a8f98; border-bottom:1px dotted #8a8f98;
  cursor: default; text-decoration:none; }
h1,h2,h3 { line-height:1.25; }
.badge { display:inline-block; font-size:12px; padding:2px 8px;
  border-radius:999px; margin-right:6px; }
.badge-evergreen { background:#1f3d2c; color:#7fe0b0; }
.badge-seedling { background:#3d331f; color:#ffb37c; }
.topnav { padding:16px 20px; border-bottom:1px solid #262a31; }
.topnav a { text-decoration:none; font-weight:600; }
.note-list, .backlinks { list-style:none; padding-left:0; }
.note-list li, .backlinks li { padding:6px 0; border-bottom:1px solid #21242a; }
.meta { color:#8a8f98; font-size:13px; }
.topic-group h2 { margin-top:36px; }
footer { color:#5c6270; font-size:12px; text-align:center; padding:20px; }
"""


def _page(title: str, body: str, note_count: int, root_href: str = "index.html") -> bytes:
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{'../' if root_href != 'index.html' else ''}garden.css">
</head>
<body>
<nav class="topnav"><a href="{root_href}">memsub garden</a></nav>
<main>
{body}
</main>
<footer>memsub garden &middot; {note_count} note(s) &middot; static, local-only export</footer>
</body>
</html>
"""
    return doc.encode("utf-8")


def _relative_time(ts: float | None) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _render_note_page(
    note: GardenNote, published: dict[str, GardenNote], backlinks: list[GardenNote]
) -> bytes:
    def resolve(target: str):
        target = target.strip()
        cand = target if target.endswith(".md") else f"{target}.md"
        if cand in published:
            return published[cand]
        stem = Path(target).stem
        for rel, n in published.items():
            if Path(rel).stem == stem:
                return n
        return None

    badge = "evergreen" if note.stage == "evergreen" else "seedling"
    badge_label = "evergreen &middot; verified" if note.verified else (
        "seedling &middot; inferred, unverified" if note.stage == "seedling" else "evergreen"
    )
    body_html = render_markdown(note.body, resolve)
    backlinks_html = ""
    if backlinks:
        items = "".join(
            f'<li><a href="{_note_url(b)}">{html.escape(b.title)}</a></li>' for b in backlinks
        )
        backlinks_html = f'<section class="backlinks"><h3>Linked from</h3><ul class="backlinks">{items}</ul></section>'
    meta = (
        f'<p class="meta">planted {html.escape(_relative_time(note.planted))} '
        f"&middot; last tended {html.escape(_relative_time(note.last_tended))}</p>"
    )
    content = (
        f"<h1>{html.escape(note.title)}</h1>"
        f'<span class="badge badge-{badge}">{badge_label}</span>'
        f"{meta}"
        f'<div class="note-body">{body_html}</div>'
        f"{backlinks_html}"
    )
    return _page(note.title, content, len(published), root_href="../index.html")


def _render_index_page(published: dict[str, GardenNote]) -> bytes:
    """Grouped by `kind` frontmatter (fact/decision/idea/...) -- the closest
    allowlist-safe "topic" available without also requiring a vault's
    topics/*.md page to itself pass the publish allowlist (deferred, see
    docs does-NOT-do)."""
    by_kind: dict[str, list[GardenNote]] = {}
    for note in published.values():
        kind = note.fields.get("kind") or "note"
        by_kind.setdefault(kind, []).append(note)

    sections = []
    for kind in sorted(by_kind):
        notes = sorted(by_kind[kind], key=lambda n: n.title.lower())
        items = "".join(
            f'<li><a href="{_note_url(n)}">{html.escape(n.title)}</a></li>' for n in notes
        )
        sections.append(f'<div class="topic-group"><h2>{html.escape(kind)}</h2><ul class="note-list">{items}</ul></div>')

    body = f"<h1>Garden</h1><p class='meta'>{len(published)} published note(s)</p>" + "".join(sections)
    return _page("Garden", body, len(published), root_href="index.html")


@dataclass
class BuildResult:
    published: int = 0
    assets_copied: int = 0
    blocked_pii: int = 0
    inferred_awaiting_flag: int = 0
    out_dir: Path | None = None
    needs_review: ReviewDiff | None = None  # set only when the build was refused


def build_site(vault_root: Path, out_dir: Path, store=None, yes: bool = False) -> BuildResult:
    """Build the static garden. Fails closed (writes nothing, returns a
    result with `needs_review` set) when the current publish set has
    additions or content changes `garden review` has not yet approved,
    unless `yes=True`."""
    notes = resolve_publish_set(vault_root, store=store)
    counts = status_counts(notes)
    published = {rel: n for rel, n in notes.items() if n.status == STATUS_PUBLISHED}
    current = _published_hashes(notes)

    manifest = load_manifest(vault_root)
    diff = diff_publish_set(current, manifest.get("approved", {}))
    if not yes and (diff.added or diff.changed):
        return BuildResult(
            blocked_pii=counts[STATUS_BLOCKED_PII],
            inferred_awaiting_flag=counts[STATUS_INFERRED_AWAITING_FLAG],
            needs_review=diff,
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "notes").mkdir(exist_ok=True)
    (out_dir / "garden.css").write_text(_CSS, encoding="utf-8")

    # backlinks within the published set only
    backlinks: dict[str, list[GardenNote]] = {rel: [] for rel in published}
    for rel, note in published.items():
        for m in _WIKILINK_RE.finditer(note.body):
            target = m.group(1).strip()
            cand = target if target.endswith(".md") else f"{target}.md"
            resolved_rel = cand if cand in published else None
            if resolved_rel is None:
                stem = Path(target).stem
                resolved_rel = next((r for r in published if Path(r).stem == stem), None)
            if resolved_rel and resolved_rel != rel:
                backlinks[resolved_rel].append(note)

    for rel, note in published.items():
        html_bytes = _render_note_page(note, published, backlinks.get(rel, []))
        (out_dir / "notes" / f"{note.id_prefix}.html").write_bytes(html_bytes)

    (out_dir / "index.html").write_bytes(_render_index_page(published))
    assets_copied = _copy_assets(vault_root, published, out_dir)

    if yes:
        manifest["approved"] = current
        manifest["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest["last_build"] = current
    manifest["last_build_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_manifest(vault_root, manifest)

    return BuildResult(
        published=len(published),
        assets_copied=assets_copied,
        blocked_pii=counts[STATUS_BLOCKED_PII],
        inferred_awaiting_flag=counts[STATUS_INFERRED_AWAITING_FLAG],
        out_dir=out_dir,
    )
