# Obsidian vault export layer

`memsub vault sync --vault PATH [--namespace NS]` (`memsub/vault.py`) turns
the episodic store into a plain folder of markdown files that Obsidian opens
natively, no plugin required. The point is the graph view: `[[wiki-links]]`
between memories that share an entity, or that were superseded, make the
store's own history browsable instead of only queryable.

## What it does

- One markdown note per memory (active, superseded, or forgotten): YAML
  frontmatter (`id`, `namespace`, `kind`, `type` observed/inferred, `status`,
  `created`, `last_updated`, `last_used`, `confidence` if the memory carries
  one), the memory content, a **Provenance** section (source
  session/transcript, generation time, the full audit event trail: every
  retrieval, injection, supersede, and forget), and a **Links** section.
- Two link types, both cheap and dependency-free, matching what the store
  already records:
  - **Shared entities**: reuses `memsub.sleep._extract_entities`'s naive
    capitalized-word extraction (the same one sleep already uses to build its
    entity co-occurrence links) to cross-link memories that mention the same
    capitalized term. No NER model, no new dependency.
  - **Supersede chains**: a superseded memory links forward
    (`superseded by [[...]]`) and the replacement links back
    (`supersedes [[...]]`). Superseded memories are never removed from the
    vault, so a contradiction's before/after is one click away in Obsidian's
    graph view, exactly like the store's own append-only design intends.
- A per-namespace `index.md` (grouped by kind, newest first) and a top-level
  `index.md` map-of-content linking every namespace index. The MOC reads each
  namespace's display name back out of its index heading, so a
  `--namespace`-scoped sync does not clobber namespaces it did not touch.
- Idempotent, re-runnable sync: filenames are `<slug>--<id8>.md`, derived only
  from the memory's (stable, append-only) id and content, so a note's path
  never moves across runs. A note is rewritten only when its rendered
  content differs from what is on disk; running sync twice against an
  unchanged store touches zero files (verified in
  `tests/test_vault.py::test_rerun_with_no_changes_touches_nothing`). Notes
  whose memory id is no longer in the synced scope are deleted.

## What it deliberately does not do

- **No vault-to-store editing.** The vault is a one-way, generated view. If
  you edit a note in Obsidian, the next sync overwrites it silently (the
  diff check compares rendered content, not what a human touched). There is
  no watcher, no merge, no conflict resolution. Building that is a real
  amount of work (a place to store human edits distinct from generated
  content, a merge policy, a way to feed edits back into the store as new
  episodes with their own provenance) and it is explicitly out of scope
  here.
- **No MCP tool, no live sync.** `vault sync` is a CLI command you run when
  you want a fresh snapshot, not a background process. There is no
  file-watching daemon and no incremental single-memory update path outside
  of running the whole command again (which is cheap: unchanged notes are
  skipped, not rewritten).
- **No graph-database backing.** The wiki-links are computed at export time
  from the same episodic store `sleep.py` already reads; nothing new is
  stored, and there is no separate index to keep in sync with the SQLite
  file. This keeps the zero-infra story (`docs/positioning.md`) intact:
  numpy is still the only runtime dependency.
- **No entity resolution.** "Rustom" the person and "Rustom" appearing in a
  different, unrelated sentence link to each other if the capitalized
  string matches. This is the same trade-off `sleep.py`'s existing link
  generation already makes; the vault layer does not improve on it.

## Second-layer ideas (not built, honestly speculative)

Rustom mentioned a "second layer" direction for this vault; three ideas,
none implemented, none benchmarked, all would need real design work before
committing to any of them:

1. **Vault as a shared human+agent memory surface.** Right now the store is
   agent-facing (SDK/MCP) and the vault is human-facing (read-only). A real
   second layer would let a human's own notes in the vault (separate files,
   never the generated ones) become memories an agent can retrieve, closing
   the loop. This needs a clear boundary between generated and human content
   (e.g. a `notes/` subfolder sync never touches) and an explicit "promote
   this note to a memory" action, not silent absorption of anything sitting
   in the vault.
2. **Review/annotate workflow for consolidation decisions.** Consolidation
   already logs a reason for every supersede/forget (`docs/memory-design.md`);
   the vault already renders it. The natural next step is letting a human
   annotate a note in Obsidian ("this supersede was wrong") and have that
   annotation become a first-class, audited override the next time
   `consolidate` runs, rather than a comment that just sits in a markdown
   file forever.
3. **Vault-driven curation before sleep/consolidate.** Sleep and
   consolidation currently run unattended. A staging vault (write candidates,
   let a human accept/reject in Obsidian by moving files or checking boxes,
   then apply only the accepted set to the store) would trade the current
   "audit after the fact" model for "approve before the fact" on the
   highest-stakes decisions (contradictions, forgets), without touching the
   store's append-only guarantees.

None of these are scoped or estimated; they are directions, not commitments.
