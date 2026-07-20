# Vault serve: a small memory product, not a file server

`memsub vault serve` (`memsub/vault_serve.py`) is a read-only HTTP server for
browsing `~/memsub-vault` (4000+ notes with `[[wikilinks]]`) or any markdown
folder from a browser. Built for working over SSH with no Obsidian installed
on the client (a Mac in this case).

Zero third-party dependencies: `.venv` has no `markdown`, `mistune`, `yaml`,
or web framework installed, so this is a small stdlib-only markdown renderer
(`http.server`, `re`, `html`) plus the mermaid CDN script for diagrams and a
handful of inline `<svg>`/CSS/vanilla-JS blocks for the dashboard, search,
and hover-preview UI -- no chart library, no frontend framework, no other
CDN script.

2026-07-19: rebuilt from a plain functional viewer into a dashboard-first
product UI (dark Mission Control aesthetic, animated but battery-cheap,
phone-first). See "What changed 2026-07-19" below for the full list.

## Start

```bash
cd /home/user/projects/memory-sublayer
.venv/bin/python -m memsub.cli vault serve --vault ~/memsub-vault --port 8766
```

Defaults are `--vault ~/memsub-vault --port 8766 --db $MEMSUB_DB` (or
`~/.memsub/memory.db`), so `memsub vault serve` with no flags does the same
thing. Binds `127.0.0.1` only (never reachable off the box).

`--db <path>` points the dashboard at a memsub SQLite store for its real
counts. It is best-effort: if the path does not exist, can't be opened, or
you pass `--db ""`/an unrelated file, the dashboard falls back to
**vault-only mode** (see below) instead of erroring.

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
the identical index/render/serve code path. Root mode almost always ends up
in vault-only mode for the dashboard (a docs/ folder isn't a memsub store)
and usually has no `review-queue.md`, both handled gracefully.

## Stop

```bash
pkill -f "memsub.cli vault serve"
# or: kill <pid> from the start command's output
```

## What it does

### Dashboard (`/`)

The landing page. Real numbers, not decoration:

- **Stat tiles**: total memories, active, superseded, namespaces,
  human-verified, quarantined, last-extraction time. Count up on load
  (vanilla JS, skipped entirely under `prefers-reduced-motion`, in which
  case the server-rendered final number just sits there -- no broken state
  for reduced-motion or no-JS clients).
- **Provenance donut**: inline `<svg>`, four segments (observed / inferred /
  human-verified / quarantined), draw-in animation via a CSS
  `stroke-dasharray` keyframe (no chart library), gated behind
  `@media (prefers-reduced-motion: no-preference)`.
- **30-day activity sparkline**: inline `<svg><polyline>`, memories created
  per day, same draw-in/reduced-motion treatment.
- **Vault-only mode**: if no store is reachable at `--db`, every number
  above is instead derived from note frontmatter (`status`, `type`,
  `human_confirmed`, `namespace`, `created`) cached once at index-build time
  in `VaultIndex.frontmatter`, and a small banner says so. Nothing is ever
  fabricated or silently zeroed.
- Numbers are **live, uncached, per-request**: two `EpisodicStore` read
  calls (`all()`, `events()`) per dashboard load, no memoization. If another
  process is actively writing the store (daily extraction, consolidation)
  while you have the page open, a refresh can show different numbers --
  that's by design, not a bug. (Observed directly during this build: the
  shared `~/.memsub/memory.db` this was tested against was being written to
  concurrently by unrelated extraction work, and every snapshot the
  dashboard rendered was internally consistent with the live store at that
  instant -- confirmed by comparing an HTTP response to a simultaneous
  direct store query.)

### Search (`/`, same page)

The dashboard page also hosts the browse/search surface (one page, not two
routes -- the folder-grouped listing you'd want from a "browse" page is
exactly what an empty search shows). Typing debounces (150ms) a fetch to
`/api/search.json`, which returns matches grouped by folder with a
highlighted (`<mark>`) snippet around the match; results replace the list
in place, Up/Down move a highlighted selection, Enter opens it. No query
means the full listing (server-rendered, works with JS off). `?q=` in the
URL still works server-side too (same code path `search_results()` powers
both the JSON endpoint and the plain HTML render).

### Note pages (`/note/<relpath>`)

- Markdown rendered to HTML: headers, bold/italic, inline code, code
  fences, lists, blockquotes, links, exactly as before.
- **Frontmatter as a metadata card** with a provenance badge --
  `observed` / `inferred` / `human-verified` / `quarantined`, each a
  distinct color, derived only from the frontmatter fields
  `memsub/vault.py` already writes (`status`, `type`, `human_confirmed`),
  precedence human-verified > quarantined > inferred > observed.
- **Supersede-chain timeline**: when a note's `## Links` section has
  `supersedes` / `superseded by` lines (already written by
  `memsub/vault.py`), a vertical animated timeline (draw-in line,
  reduced-motion-safe) shows the full chain oldest-to-newest with the
  current note highlighted. Walked purely from vault text (no store access,
  capped at 20 hops each direction against cycles). A note with no
  supersede relationship shows no timeline section at all.
- **Wikilink hover preview**: hovering a live `[[wikilink]]` fetches
  `/api/preview/<path>` (title, snippet, provenance badge) and shows a
  small popover. Dead links (`[[missing]]`) stay non-interactive `<span>`s,
  same reddish dashed style as before -- no fetch attempted, since there's
  nothing to preview.
- `[[wikilink]]` and `[[wikilink|alias]]` still become a link to the target
  note; a target that does not exist still renders in the distinct dead-link
  style instead of a broken link.
- Mermaid diagrams unchanged: a ` ```mermaid ` fence becomes
  `<pre class="mermaid">`, CDN script loaded only on pages that use it.
- Typographic pass: readable measure (~42rem) on the note body, tighter
  heading sizing so a long slugified title (common in this vault) doesn't
  dominate the viewport.

### Review queue (`/review`)

Renders `review-queue.md` (memsub/scripts/triage_vault.py's output) as an
actual triage UI: each category is a colored badge heading, each candidate a
card linking to its note. Still strictly read-only -- the page states in
plain text that verdicts are applied by editing a note's frontmatter
(`verdict: confirm` / `verdict: reject`) and running `memsub vault apply`,
never from this page. Root mode / a vault with no `review-queue.md` shows a
graceful empty state, not an error.

### Navigation

Sticky header on every page: brand link (`memsub::vault`, doubles as
back-to-dashboard), a breadcrumb on note/review pages, a `random` button
(`/random`, 302 to a random note), and a `review queue` link. A small footer
stamp shows the vault-serve version and total note count.

### API endpoints (read-only, JSON, used by the page's own JS)

- `GET /api/search.json?q=...` -- `{"results": [...], "total": n}`, same
  matching as the server-rendered search (`search_notes`), same snippet
  highlighting (`_snippet_html`).
- `GET /api/preview/<relpath>` -- `{"title", "snippet", "badge"}` for the
  wikilink hover popover. `safe_path` + index-membership checked exactly
  like `/note/<relpath>`; a missing or out-of-root path 404s with `{}`.

## What it does not do

- **No write endpoints anywhere.** This is a viewer, not an editor. Use
  `memsub vault apply` / `memsub vault promote` for the human-edit loop.
  The dashboard's store access is equally read-only: it never opens (let
  alone creates) a database file that doesn't already exist on disk, and
  only ever calls `EpisodicStore.all()` / `.events()`.
- **No live reload.** The note index is built once at server start by
  walking the folder; a note added, edited, or deleted while the server is
  running will not show up until the server is restarted. The dashboard's
  *store* numbers are the exception -- those are re-queried live on every
  request (see above); only the note/wikilink *index* is static per process
  lifetime.
- **Not exposed beyond localhost.** Binds `127.0.0.1` only; reach it from
  another machine via SSH port-forward, never by opening the port.
- **Path-traversal safe by construction**: every `/note/<path>` and
  `/api/preview/<path>` request is resolved against the served root and
  rejected (404) if the resolved path escapes the root, whether via `../`,
  an absolute path, or a URL-encoded variant. Covered by
  `tests/test_vault_serve.py::test_safe_path_rejects_traversal_outside_root`
  and an end-to-end HTTP smoke test in the same file.

## What changed 2026-07-19

Product-UI pass (dashboard, search, note view, review queue, nav) requested
by Rustom: "make it marketable and more animated for each project -- like an
actual website and product." Everything above reflects the shipped state;
the concrete diff from the prior plain viewer:

- New: dashboard landing page with live stat tiles, provenance donut,
  30-day sparkline, vault-only fallback mode.
- New: `--db` flag on `memsub vault serve` (defaults to the standard
  `$MEMSUB_DB` / `~/.memsub/memory.db` convention).
- New: instant-feel debounced search with folder grouping, highlighted
  snippets, keyboard nav, via `/api/search.json`.
- New: provenance badge, animated supersede-chain timeline, and wikilink
  hover preview (`/api/preview/<path>`) on note pages.
- New: `/review` review-queue triage page, `/random` serendipity route.
- New: sticky nav header, breadcrumb, footer version/note-count stamp.
- Unchanged (by design, verified against the existing test suite): wikilink
  resolution/dead-link styling, frontmatter parsing, mermaid rendering,
  `search_notes` matching, `safe_path` traversal guarantees. All 333 tests
  pass; no third-party dependency was added.
- Screenshots (390x844 and 1280x800, dashboard / search-in-action / note
  with provenance card / review queue): `docs/screenshots/2026-07-19/`.
