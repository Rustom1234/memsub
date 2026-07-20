"""Shared read model for one vault note: title, sections, and small prose
helpers. Single source of truth for "what does this note look like when
shown to a human" so every surface in vault_serve.py (search results, folder
listings, graph node labels, related-ideas panels, wikilink hover preview,
connection-of-the-day) renders the same title the same way.

Read-only: every function here takes already-loaded text (a VaultIndex or a
raw string) and returns derived data. Nothing opens a file or a database.

Design source: docs/second-brain-spec-2026-07-20.md Layer 0, itself Rustom's
verbatim complaint about a raw note page ("not readable, i dont understand
shit here"). memsub/vault.py's render_note() writes a fixed structure this
module knows how to read: a `# <h1>` (currently the 80-char-truncated
content, which Layer 0 replaces as the *title* everywhere but leaves alone in
the file itself -- that file is out of scope for this task), a `## Content`
section, a `## Provenance` section, a `### Audit events` subsection, and a
`## Links` section whose bullets are `- shares entity **X** with [[Y]]` /
`- supersedes [[Y]]` / `- superseded by [[Y]]` / `- promoted from [[Y]]`.
Notes that don't match this shape (index.md, review-queue.md, templates,
future human-authored notes) degrade gracefully via the fallback chain in
note_title().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SECTION_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
_SHARES_ENTITY_RE = re.compile(r"^- shares entity \*\*([^*]+)\*\* with \[\[([^\]]+)\]\]\s*$")
_EDITORIAL_LINK_RE = re.compile(r"^- (supersedes|superseded by|promoted from) \[\[([^\]]+)\]\]\s*$")

MAX_TITLE_LEN = 160  # fallback cap when a "sentence" runs on with no terminator


@dataclass
class NoteSections:
    """Body markdown split by memsub/vault.py's fixed note structure. Any
    section not found is an empty string, so callers on a non-standard note
    (index page, template, human note) just see everything as `other`."""

    content: str = ""
    provenance: str = ""
    audit: str = ""
    links: str = ""
    other: str = ""  # anything before the first recognized section header


@dataclass
class RelatedGroup:
    """One "Related: N notes about X" line, expandable to prose titles."""

    entity: str
    count: int
    members: list[tuple[str, str]] = field(default_factory=list)  # (relpath, title)


def split_note_sections(body: str) -> NoteSections:
    """Split a note body into named sections by its `## <Name>` headers.
    Pure text split, no markdown rendering -- render_markdown() is applied
    per-section afterward by the caller."""
    sections = NoteSections()
    current = "other"
    buf: list[str] = []
    name_to_field = {
        "content": "content",
        "provenance": "provenance",
        "audit events": "audit",
        "links": "links",
    }

    def flush() -> None:
        text = "\n".join(buf).strip("\n")
        if current == "other":
            sections.other = text
        elif current == "content":
            sections.content = text
        elif current == "provenance":
            sections.provenance = text
        elif current == "audit":
            sections.audit = text
        elif current == "links":
            sections.links = text

    for line in body.splitlines():
        m = _SECTION_RE.match(line)
        if m and m.group(2).strip().lower() in name_to_field:
            flush()
            current = name_to_field[m.group(2).strip().lower()]
            buf = []
            continue
        buf.append(line)
    flush()
    return sections


def _first_sentence(text: str, max_len: int = MAX_TITLE_LEN) -> str:
    text = " ".join(text.split())  # collapse whitespace/newlines
    if not text:
        return ""
    m = _SENTENCE_END_RE.search(text)
    sentence = text[: m.end()].strip() if m else text
    if len(sentence) > max_len:
        sentence = sentence[:max_len].rstrip() + "…"
    return sentence


def _humanize_stem(relpath: str) -> str:
    stem = Path(relpath).stem
    # memsub note filenames end "--<8-hex-id>"; drop that suffix for a
    # readable last-resort title.
    stem = re.sub(r"--[0-9a-f]{8}$", "", stem)
    return stem.replace("-", " ").replace("_", " ").strip() or Path(relpath).stem


def note_title(relpath: str, idx) -> str:
    """The one title-resolution function every surface must call. Precedence:
    1. First sentence of the note's `## Content` section (the standard
       memsub-generated note -- this is the fix for the readability
       complaint: never the slug, never an 80-char hard truncation).
    2. The note's own `# <h1>` markdown heading, when the note has no
       `## Content` section but does have a top-level heading (index.md,
       review-queue.md, human-authored notes).
    3. A humanized version of the filename stem (dashes/id-suffix stripped)
       as the last resort, so a title is never a raw slug with dashes.
    """
    text = idx.notes.get(relpath, "") if hasattr(idx, "notes") else str(idx)
    fields_body = text
    if fields_body.startswith("---"):
        # strip a frontmatter fence without importing vault_serve (avoid a
        # circular import); a minimal repeat of the same fence pattern.
        end = fields_body.find("\n---", 3)
        if end != -1:
            nl = fields_body.find("\n", end + 1)
            fields_body = fields_body[nl + 1 :] if nl != -1 else ""
    sections = split_note_sections(fields_body)
    if sections.content:
        title = _first_sentence(sections.content)
        if title:
            return title
    for line in fields_body.splitlines():
        m = re.match(r"^#\s+(.*)$", line)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return _humanize_stem(relpath)


def namespace_in_words(namespace: str) -> str:
    """'claude-code' -> 'claude code'. Prose, not a slug."""
    return namespace.replace("-", " ").replace("_", " ").strip() or namespace


def parse_editorial_links(links_section: str) -> list[tuple[str, str]]:
    """(kind, target) pairs for the human-meaningful link lines
    (supersedes / superseded by / promoted from) -- these stay visible
    outside the machine-details block, unlike entity-share bullets."""
    out = []
    for line in links_section.splitlines():
        m = _EDITORIAL_LINK_RE.match(line.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def group_related_entities(links_section: str, idx, current_relpath: str) -> list[RelatedGroup]:
    """Collapse "- shares entity **X** with [[Y]]" bullets into one
    RelatedGroup per entity, titles resolved via note_title() (never slugs).
    Unresolvable wikilink targets are skipped (dead link, nothing to show)."""
    # local import avoids a hard circular dependency at module load time
    from .vault_serve import resolve_wikilink

    by_entity: dict[str, list[tuple[str, str]]] = {}
    for line in links_section.splitlines():
        m = _SHARES_ENTITY_RE.match(line.strip())
        if not m:
            continue
        entity, target = m.group(1), m.group(2)
        resolved = resolve_wikilink(target, idx)
        if resolved is None or resolved == current_relpath:
            continue
        by_entity.setdefault(entity, []).append((resolved, note_title(resolved, idx)))
    groups = [
        RelatedGroup(entity=entity, count=len(members), members=members)
        for entity, members in by_entity.items()
    ]
    groups.sort(key=lambda g: (-g.count, g.entity.lower()))
    return groups
