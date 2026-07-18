"""Read-only browser viewer for a markdown vault (or any markdown folder).

Built for working over SSH with no Obsidian on the client: `memsub vault
serve` starts a small stdlib-only HTTP server (no third-party web framework,
no markdown library -- .venv has neither installed, and the vault is a few
thousand small files, so a minimal line-based renderer is plenty) that
lists notes, renders `[[wikilinks]]` as real links (with a visible dead-link
style when the target does not exist), shows frontmatter as a small
metadata block instead of raw YAML, and renders ```mermaid code fences as
diagrams client-side via the mermaid CDN script.

Vault mode (--vault, the default) and root mode (--root) are the exact same
viewer pointed at a different folder: root mode exists so a plain docs/
folder full of design docs (mermaid diagrams included) is viewable with the
same command, no memsub-specific structure required.

Read-only and local-only by construction: no write endpoints exist, the
server binds 127.0.0.1 only, and every requested note path is resolved and
checked to stay under the served root before the file is opened (see
`_safe_path`), so a crafted URL cannot read anything outside the vault.
"""

from __future__ import annotations

import html
import re
import socketserver
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"
SEARCH_CAP = 200

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
# Page templates
# --------------------------------------------------------------------------- #
_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  background: #14161a; color: #d8dbe2; font-family: -apple-system, system-ui, sans-serif;
  line-height: 1.55; margin: 0; padding: 1rem 1rem 4rem;
}
a { color: #7cb0ff; text-decoration: none; }
a:hover { text-decoration: underline; }
.wikilink.dead, .wikilink-dead { color: #ff8a8a; border-bottom: 1px dashed #ff8a8a; cursor: default; }
h1, h2, h3, h4 { color: #f0f2f6; line-height: 1.25; }
pre { background: #1c1f26; padding: 0.75rem; overflow-x: auto; border-radius: 6px; }
code { background: #1c1f26; padding: 0.1rem 0.3rem; border-radius: 4px; font-size: 0.9em; }
pre code { background: none; padding: 0; }
blockquote { border-left: 3px solid #3a3f4b; margin: 0.5rem 0; padding: 0.2rem 1rem; color: #a7acb8; }
input[type=text] {
  width: 100%; max-width: 32rem; padding: 0.6rem; border-radius: 6px; border: 1px solid #3a3f4b;
  background: #1c1f26; color: #d8dbe2; font-size: 1rem;
}
.meta { background: #1c1f26; border-radius: 6px; padding: 0.6rem 1rem; font-size: 0.85em; color: #a7acb8; margin-bottom: 1rem; }
.meta span { display: inline-block; margin-right: 1rem; }
.folder { margin-top: 1.5rem; }
.folder h2 { font-size: 1rem; color: #9aa0ab; border-bottom: 1px solid #262a33; padding-bottom: 0.3rem; }
ul.notelist { list-style: none; padding-left: 0; }
ul.notelist li { padding: 0.15rem 0; }
.topbar { margin-bottom: 1rem; }
.topbar a { color: #9aa0ab; }
.count { color: #6b7280; font-size: 0.85em; }
"""

_MERMAID_INIT = f"""
<script src="{MERMAID_CDN}"></script>
<script>mermaid.initialize({{ startOnLoad: true, theme: 'dark' }});</script>
"""


def _page(title: str, body: str, include_mermaid: bool = False) -> bytes:
    mermaid = _MERMAID_INIT if include_mermaid else ""
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
{body}
{mermaid}
</body>
</html>
"""
    return html_doc.encode("utf-8")


def render_note_page(relpath: str, idx: VaultIndex) -> bytes:
    text = idx.notes[relpath]
    fields, body = split_frontmatter(text)
    meta_html = ""
    if fields:
        parts = "".join(
            f"<span><strong>{html.escape(k)}</strong>: {html.escape(v)}</span>"
            for k, v in fields.items()
        )
        meta_html = f'<div class="meta">{parts}</div>'
    title = relpath
    body_html = render_markdown(body, idx)
    content = (
        f'<div class="topbar"><a href="/">&larr; index</a></div>'
        f"<h1>{html.escape(title)}</h1>"
        f"{meta_html}"
        f"{body_html}"
    )
    return _page(title, content, include_mermaid="```mermaid" in body)


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


def render_index_page(idx: VaultIndex, query: str = "") -> bytes:
    by_folder: dict[str, list[str]] = {}
    if query:
        rels = search_notes(idx, query)
        heading = f"{len(rels)} result(s) for &ldquo;{html.escape(query)}&rdquo;"
        for rel in rels:
            folder = str(Path(rel).parent) if Path(rel).parent != Path(".") else "(root)"
            by_folder.setdefault(folder, []).append(rel)
    else:
        heading = f"{len(idx.notes)} note(s)"
        for rel in sorted(idx.notes):
            folder = str(Path(rel).parent) if Path(rel).parent != Path(".") else "(root)"
            by_folder.setdefault(folder, []).append(rel)

    sections = []
    for folder in sorted(by_folder):
        items = "".join(
            f'<li><a href="/note/{urllib.parse.quote(rel)}">{html.escape(Path(rel).stem)}</a></li>'
            for rel in sorted(by_folder[folder])
        )
        sections.append(f'<div class="folder"><h2>{html.escape(folder)}</h2><ul class="notelist">{items}</ul></div>')

    content = (
        '<h1>Vault</h1>'
        '<form action="/" method="get">'
        f'<input type="text" name="q" placeholder="search filenames and content..." value="{html.escape(query)}">'
        "</form>"
        f'<p class="count">{heading}</p>'
        + "".join(sections)
    )
    return _page("Vault", content)


# --------------------------------------------------------------------------- #
# HTTP server (stdlib only)
# --------------------------------------------------------------------------- #
def make_handler(idx: VaultIndex) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "memsub-vault-serve/1.0"

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
                query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
                self._send(render_index_page(idx, query))
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
                self._send(render_note_page(relpath, idx))
                return
            self._send(b"not found", status=404, content_type="text/plain")

    return Handler


def serve_vault(root: Path, host: str = "127.0.0.1", port: int = 8766) -> int:
    root = root.expanduser().resolve()
    if not root.is_dir():
        print(f"no such directory: {root}")
        return 1
    idx = build_index(root)
    handler = make_handler(idx)
    with socketserver.ThreadingTCPServer((host, port), handler) as httpd:
        print(f"memsub vault serve: {len(idx.notes)} note(s) from {root}")
        print(f"listening on http://{host}:{port} (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
