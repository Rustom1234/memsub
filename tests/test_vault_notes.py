"""Layer 0 readability: memsub/vault_notes.py's title/section helpers."""

from __future__ import annotations

from memsub.vault_graph import build_graph_index
from memsub.vault_notes import (
    group_related_entities,
    namespace_in_words,
    note_title,
    parse_editorial_links,
    split_note_sections,
)
from memsub.vault_serve import build_index

STANDARD_NOTE = """---
id: "abc123"
namespace: "claude-code"
kind: "event"
type: "observed"
status: "active"
created: "2026-07-12T23:54:04"
---

# Background tasks are executing system commands to check vite installatio

## Content

Background tasks are executing system commands to check vite installation, \
install duckdb, run data pipelines, and manage uvicorn server. This is a \
second sentence that should never appear in the title.

## Provenance

- Run: `dogfood2`
- Session: `abc`

### Audit events

- none recorded

## Links

- shares entity **Background** with [[other-note]]
- shares entity **Background** with [[third-note]]
- shares entity **Vite** with [[other-note]]
- supersedes [[old-note]]
"""


def _write(root, relpath, text):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_note_title_is_first_sentence_of_content_not_slug(tmp_path):
    _write(tmp_path, "note.md", STANDARD_NOTE)
    _write(tmp_path, "other-note.md", "# Other\n\n## Content\n\nOther note body.\n")
    _write(tmp_path, "third-note.md", "# Third\n\n## Content\n\nThird note body.\n")
    _write(tmp_path, "old-note.md", "# Old\n\n## Content\n\nOld note body.\n")
    idx = build_index(tmp_path)

    title = note_title("note.md", idx)
    assert title == "Background tasks are executing system commands to check vite installation, install duckdb, run data pipelines, and manage uvicorn server."
    assert "second sentence" not in title


def test_note_title_falls_back_to_h1_when_no_content_section(tmp_path):
    _write(tmp_path, "index.md", "# Memory Vault\n\n4330 memories.\n")
    idx = build_index(tmp_path)
    assert note_title("index.md", idx) == "Memory Vault"


def test_note_title_falls_back_to_humanized_stem_last_resort(tmp_path):
    _write(tmp_path, "some-weird-file--a1b2c3d4.md", "no headings at all, just text")
    idx = build_index(tmp_path)
    title = note_title("some-weird-file--a1b2c3d4.md", idx)
    assert title == "some weird file"
    assert "--" not in title


def test_split_note_sections_separates_content_provenance_links():
    sections = split_note_sections(STANDARD_NOTE.split("---\n", 2)[-1])
    assert "Background tasks" in sections.content
    assert "second sentence" in sections.content
    assert "dogfood2" in sections.provenance
    assert "none recorded" in sections.audit
    assert "shares entity" in sections.links


def test_parse_editorial_links_keeps_supersedes_out_of_entity_groups():
    links = "- shares entity **X** with [[y]]\n- supersedes [[old]]\n- superseded by [[new]]\n"
    editorial = parse_editorial_links(links)
    assert ("supersedes", "old") in editorial
    assert ("superseded by", "new") in editorial
    assert len(editorial) == 2


def test_group_related_entities_collapses_link_soup(tmp_path):
    _write(tmp_path, "note.md", STANDARD_NOTE)
    _write(tmp_path, "other-note.md", "# Other\n\n## Content\n\nOther note body.\n")
    _write(tmp_path, "third-note.md", "# Third\n\n## Content\n\nThird note body.\n")
    _write(tmp_path, "old-note.md", "# Old\n\n## Content\n\nOld note body.\n")
    idx = build_index(tmp_path)
    sections = split_note_sections(idx.notes["note.md"].split("---\n", 2)[-1])

    groups = group_related_entities(sections.links, idx, "note.md")
    by_entity = {g.entity: g for g in groups}
    assert by_entity["Background"].count == 2
    titles = [t for _, t in by_entity["Background"].members]
    assert "Other note body." in titles and "Third note body." in titles
    assert by_entity["Vite"].count == 1


def test_namespace_in_words_replaces_dashes():
    assert namespace_in_words("claude-code") == "claude code"
    assert namespace_in_words("outreach_desk") == "outreach desk"


_FM = '---\nnamespace: "claude-code"\ncreated: "2026-07-19T00:00:00"\n---\n\n'


def test_graph_index_builds_from_vault_text_only(tmp_path):
    _write(tmp_path, "note.md", STANDARD_NOTE)
    _write(tmp_path, "other-note.md", _FM + "# Other\n\n## Content\n\nOther note body.\n")
    _write(tmp_path, "third-note.md", _FM + "# Third\n\n## Content\n\nThird note body.\n")
    _write(tmp_path, "old-note.md", _FM + "# Old\n\n## Content\n\nOld note body.\n")
    idx = build_index(tmp_path)
    graph = build_graph_index(idx)
    assert "note.md" in graph.notes
    assert "Background" in graph.hubs
    # note.md (the mentioning note) + other-note.md + third-note.md
    assert graph.hubs["Background"].degree == 3
