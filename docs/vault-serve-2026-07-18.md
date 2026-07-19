# Vault serve: browser viewer for the memory vault and markdown docs

`memsub vault serve` (`memsub/vault_serve.py`) is a read-only HTTP server for
browsing `~/memsub-vault` (4000+ notes with `[[wikilinks]]`) or any markdown
folder from a browser. Built for working over SSH with no Obsidian installed
on the client (a Mac in this case).

Zero third-party dependencies: `.venv` has no `markdown`, `mistune`, `yaml`,
or web framework installed, so this is a small stdlib-only markdown renderer
(`http.server`, `re`, `html`) plus the mermaid CDN script for diagrams.

## Start

```bash
cd /home/user/projects/memory-sublayer
.venv/bin/python -m memsub.cli vault serve --vault ~/memsub-vault --port 8766
```

Defaults are `--vault ~/memsub-vault --port 8766`, so `memsub vault serve`
with no flags does the same thing. Binds `127.0.0.1` only (never reachable
off the box).

## From the Mac: port-forward over SSH

```bash
ssh -L 8766:localhost:8766 user@<this-host>
```

Then open `http://localhost:8766` in the Mac's browser.

## Root mode: viewing a project's docs/ instead

Vault mode and root mode are **the same viewer**, just pointed at a
different folder. Pass `--root` to serve any markdown directory (e.g. a
project's `docs/` full of design docs with mermaid diagrams) instead of the
vault:

```bash
.venv/bin/python -m memsub.cli vault serve --root ~/projects/memory-sublayer/docs --port 8766
```

`--root`, when given, overrides `--vault` entirely; both modes go through
the identical index/render/serve code path.

## Stop

```bash
pkill -f "memsub.cli vault serve"
# or: kill <pid> from the start command's output
```

## What it does

- **Index page** (`/`): every note grouped by folder, with a plain
  server-side substring search box (matches filenames and file content,
  capped at 200 results).
- **Note pages** (`/note/<relpath>`): markdown rendered to HTML. Headers,
  bold/italic, inline code, code fences, lists, blockquotes, links.
  `[[wikilink]]` and `[[wikilink|alias]]` become a link to the target note;
  a target that does not exist renders in a distinct dead-link style
  (reddish, dashed underline, not clickable) instead of a broken link.
  Frontmatter (`id: "..."`, `namespace: "..."`, etc.) is parsed into a small
  metadata block at the top of the page, never shown as raw YAML.
- **Mermaid diagrams**: a ```` ```mermaid ```` code fence is rendered as
  `<pre class="mermaid">` and the page loads the mermaid CDN script
  (`cdn.jsdelivr.net/npm/mermaid@10`) with dark theme, so a doc's flowcharts
  render as real diagrams, not code blocks. The CDN script is only included
  on pages that actually contain a mermaid block.
- Dark, minimal, phone-friendly inline CSS. No page-load JS beyond the
  mermaid CDN script (only loaded when needed).

## What it does not do

- **No write endpoints anywhere.** This is a viewer, not an editor. Use
  `memsub vault apply` / `memsub vault promote` for the human-edit loop.
- **No live reload.** The note index is built once at server start by
  walking the folder; a note added, edited, or deleted while the server is
  running will not show up until the server is restarted. Fine for a
  personal read-only browsing session, not meant to be left running for
  days across active edits.
- **Not exposed beyond localhost.** Binds `127.0.0.1` only; reach it from
  another machine via SSH port-forward, never by opening the port.
- **Path-traversal safe by construction**: every `/note/<path>` request is
  resolved against the served root and rejected (404) if the resolved path
  escapes the root, whether via `../`, an absolute path, or a URL-encoded
  variant. Covered by `tests/test_vault_serve.py::test_safe_path_rejects_traversal_outside_root`
  and an end-to-end HTTP smoke test in the same file.
