# Obsidian vault export layer

`memsub vault sync --vault PATH [--namespace NS]` (`memsub/vault.py`) turns
the episodic store into a plain folder of markdown files that Obsidian opens
natively, no plugin required. The point is the graph view: `[[wiki-links]]`
between memories that share an entity, or that were superseded, make the
store's own history browsable instead of only queryable.

`memsub vault apply --vault PATH [--namespace NS]` is the other direction
(round 2): the vault is the surface where a human reviews and corrects what
the agent remembers, and those corrections flow back into the store. See
"Annotate/override loop" below for the exact mechanics.

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
- `content_hash` in frontmatter: a sha256 fingerprint of the memory's body
  text, written at every sync. `vault apply` compares a note's current body
  against this hash to detect a human edit; sync itself never sets
  `verdict`, so that field's mere presence in a note is unambiguous human
  intent (see below).
- `templates/verdict-guide.md`: a reference note written (and refreshed) into
  the vault root on every sync, documenting the frontmatter fields on a
  memory note and the two `verdict` values apply understands. It carries a
  fake `id` matching no episode in the store, so it is skipped as an orphan
  if `vault apply` is ever pointed at the `templates/` folder, and it has no
  `index.md`, so a namespace-agnostic `vault apply` run never scans it
  either. See "The verdict field is hand-typed" under round 2's UX findings
  in `docs/dogfood-2-2026-07-12.md`, the friction this note addresses.

## Annotate/override loop (`memsub vault apply`)

`memsub vault --db DB apply --vault DIR [--namespace NS] [--run-id ID]`
reads every note sync wrote and turns human edits into store writes. This
is round 2's core feature: the vault stops being read-only and becomes
"memory with receipts" in the fullest sense, a surface where a human
reviews and corrects what the agent remembers, with every correction itself
becoming an audited episode. `templates/verdict-guide.md` (shipped into the
vault on every sync, see above) is the cheat sheet for the two magic
`verdict` strings below.

**Three actions, driven by what a human changed in a note:**

1. **Correction**: edit the text under `## Content` to something other
   than what sync wrote. Apply supersedes the old episode with a new one
   carrying the edited text (`superseded` event, `reason: human_correction`
   on the old episode) and provenance `{"origin": "human_correction",
   "corrected_from": <old id>, "human_verified": true, "confidence": 1.0}`
   on the new one. The new episode is never `kind="inference"` and never
   sets `provenance.inferred`, so it is already "observed" by
   `consolidate._is_inferred` and the existing observed-beats-inferred rule
   ranks it above any agent-inferred memory with zero new logic
   (`tests/test_vault.py::test_apply_correction_outranks_inferred_memory_on_contradiction`).
2. **Rejection**: set `verdict: reject` in frontmatter. Apply logs a
   `forgotten` event (`reason: human_reject`), the same terminal state
   decay produces. The memory stays in the store (nothing is ever deleted)
   but stops being active.
3. **Confirm / pin**: set `verdict: confirm` in frontmatter, with no body
   edit. Apply logs a `confirmed` event. The memory stays active and
   `consolidate`'s decay phase now skips any episode with a `confirmed`
   event unconditionally, so a pinned memory never ages out.

**Precedence when a note has more than one signal:** `reject` beats a body
edit beats `confirm` beats no-op. Rejecting is the strongest, simplest
signal ("drop this"), so it wins even if the body was also edited. A body
edit is a stronger signal than a bare confirm (you cannot correct and pin
the same text in one step; correct first, then confirm the new note on the
next sync).

**Detecting an edit is safe by construction, not by inference:** episode
content never changes in place (the store is append-only), so the
`content_hash` frontmatter sync wrote for a note is a stable fingerprint of
that specific episode id forever. Apply recomputes the hash of the note's
current body and compares; a match means the note is exactly what sync
would still write today, and apply does nothing to it. This is what makes
`sync -> apply (no edits) -> sync` a true no-op, and what makes `sync ->
edit -> apply -> sync` converge with no spurious diffs (both round-trips
are covered by tests: `test_apply_unedited_note_is_noop`,
`test_round_trip_sync_edit_apply_sync_converges`).

**Safe to rerun without a resync.** Apply only acts on episodes that are
still active in the store. Once a correction or rejection is applied, the
underlying episode becomes inactive, so running apply a second time against
the same (now-stale) note file skips it instead of double-applying
(`test_apply_is_safe_to_rerun_without_resync`). Apply never rewrites vault
files itself; running `vault sync` afterward is what makes the vault catch
up to what apply just did to the store.

**What apply will do:**
- Read every `.md` note under the given vault (or one `--namespace`
  subfolder), skipping `index.md` files.
- Turn a body edit, `verdict: reject`, or `verdict: confirm` into exactly
  one store write (append + supersede, forget, or confirm) per note, per
  run.
- Skip, not error on: notes whose `id` is not in the store (orphans), notes
  whose episode is no longer active, and malformed notes (no frontmatter
  block): each counted separately in the returned `VaultApplyResult` so a
  run's output is auditable.

**What apply will not do (explicitly out of scope for round 2):**
- **No new memories from new notes.** A `.md` file in the vault whose `id`
  frontmatter field does not match an existing episode is skipped as an
  orphan, never absorbed as a new memory. Promoting a human's own note into
  a first-class memory (second-layer idea #1 below) is a different,
  unbuilt feature.
- **No editing of frontmatter fields other than `verdict`.** Changing
  `kind`, `namespace`, `confidence`, or anything else in a note's
  frontmatter is silently ignored; only the `## Content` body and the
  `verdict` field are read.
- **No merge/diff UI, no partial correction.** A body edit is taken
  verbatim as the full corrected content; there is no word-level diff
  against the original, no three-way merge if the store changed underneath
  the note since the last sync (apply just checks "is this episode still
  active" and skips if not).
- **No live sync or file watcher.** Apply is a CLI command you run once
  over the current state of the vault, same operating model as sync.
- **No staging/approval workflow before consolidate runs.** Second-layer
  idea #3 below (approve contradictions/forgets before they happen) is a
  different feature; apply only ever acts on what a human already wrote
  into a note that already exists.

## Human notes promotion (`memsub vault promote`)

`memsub vault --db DB promote --vault DIR [--namespace NS] [--run-id ID]`
is round 3's build of second-layer idea #1 below: a `notes/` folder at the
vault root where the owner writes free-form markdown, never touched or
rewritten by `vault sync` (see the reserved-name guard on `sync_vault`: a
namespace that would slugify to `notes/` is rejected, so the two can never
collide). Promote is the explicit "absorb this into memory" action idea
#1 called for; nothing under `notes/` becomes a memory on its own.

- **One note, one memory.** Each `.md` file under `<vault>/notes/`
  (recursively) becomes exactly one episode, `provenance.origin:
  human_authored`, `kind="fact"`. It is never `kind="inference"` and never
  sets `provenance.inferred`, so `consolidate._is_inferred` already treats
  it as observed, and the existing observed-beats-inferred rule
  (`memsub/consolidate.py:64`) ranks it above any agent-inferred memory
  with zero new logic, the same mechanism an apply correction relies on.
- **Idempotent by content hash**, the same mechanism `vault apply` uses to
  detect a human edit: `promote_notes` records `source_content_hash` in
  the promoted episode's provenance and skips a note whose hash matches
  what was last promoted. Running `vault promote` twice with no edits
  promotes nothing the second time.
- **A changed note supersedes its prior promoted episode**, the same
  append-only pattern as an apply correction: the old episode gets a
  `superseded` event (`reason: human_note_updated`), and a new episode
  carries the updated text.
- **Round-trips into the vault.** A promoted episode is a normal episode
  once it exists, so the next `vault sync` writes it a normal note in its
  namespace folder like anything else, with a `- promoted from
  [[<source note>]]` link back to the `notes/` file it came from (bare
  wikilink, resolved by Obsidian's basename search, same as every other
  cross-note link this layer writes).
- **What it will not do:** promote does not delete or rewrite notes under
  `notes/` (a human note stays exactly as the human wrote it), does not
  write anything back into the source note (no reverse link from note to
  memory: keeping this one-directional avoids surprising edits to a
  human's own file), and does not react to a note being deleted from
  `notes/` (the promoted memory it produced stays active; deletion
  handling is unbuilt, out of scope for round 3).

## What it deliberately does not do

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

## AI triage review queue (`scripts/triage_vault.py`)

A one-shot heuristic pass over the store (no LLM call, no new dependency:
`difflib` + keyword regex) that writes `review-queue.md` at the vault
**root**, outside any namespace folder, so `memsub vault apply` (which only
scans directories carrying an `index.md`) never reads it and no verdict is
ever applied automatically. It only ever suggests; a human `verdict:
confirm|reject` or a body edit in an actual memory note is still the only
real verdict path (see "Annotate/override loop" above).

Three heuristic categories, each scored and capped (default 100 total,
interleaved round-robin across categories so a high-volume category like
near-duplicates cannot crowd out a low-volume one):

- **corrected_reassertion**: an active memory whose significant-word
  overlap (Jaccard) with an episode already superseded elsewhere for
  `reason: human_correction` clears a threshold -- i.e. the same corrected
  claim resurfacing from an independent extraction. Looked up generically
  from the store's own supersede audit trail, not hardcoded to any one
  fact (verified on the real store: it found 4 independent reassertions of
  the OpenClaw-attribution correction, 2 more than a manual keyword grep
  turned up).
- **near_duplicate**: active/active pairs with high lexical similarity
  (`difflib.SequenceMatcher`, candidates narrowed first with a word-overlap
  inverted index so this stays roughly linear) that consolidation's
  embedding-based dedup pass (cosine >= 0.92) did not merge.
- **stale_transient**: content matching transient system/process-state
  patterns (pid, port, "currently running/disabled", "today") stored as a
  durable fact.

Run: `scripts/triage_vault.py [--db PATH] [--vault DIR] [--cap N] [--dry-run]`.

## Second-layer ideas (not built, honestly speculative)

Rustom mentioned a "second layer" direction for this vault; round 1 wrote
down three candidate ideas. Round 2 built idea #2 (the annotate/override
loop, documented above as `memsub vault apply`), round 3 built idea #1
(human notes promotion, documented above as `memsub vault promote`), and
idea #3 remains not built:

1. ~~Vault as a shared human+agent memory surface.~~ **Built in round 3.**
   `memsub vault promote` turns notes under `<vault>/notes/` into
   first-class, retrievable episodes (`provenance.origin: human_authored`),
   idempotent by content hash, superseding on edit, round-tripping back
   into the vault as a normal note linked to its source. `vault apply`
   still does not do this (a note whose `id` does not match an existing
   episode is skipped, not promoted); promotion is the separate, explicit
   action this idea called for. See "Human notes promotion" above.
2. ~~Review/annotate workflow for consolidation decisions.~~ **Built in
   round 2.** `memsub vault apply` lets a human correct, reject, or confirm
   a memory directly in Obsidian, and the result is a first-class, audited
   store write (a new human-verified episode, a `forgotten` event, or a
   `confirmed` event), not a comment sitting in a markdown file. See
   "Annotate/override loop" above.
3. **Vault-driven curation before sleep/consolidate.** Sleep and
   consolidation currently run unattended. A staging vault (write candidates,
   let a human accept/reject in Obsidian by moving files or checking boxes,
   then apply only the accepted set to the store) would trade the current
   "audit after the fact" model for "approve before the fact" on the
   highest-stakes decisions (contradictions, forgets), without touching the
   store's append-only guarantees. Note this is a different mechanism from
   `vault apply`: apply reviews memories that already exist in the store
   after the fact; a staging vault would gate memories before they ever
   become active. Not built; assessed in round 3 as too large to land
   safely alongside idea #1 in the same pass (a `--stage` flag on
   consolidate, a new staging note format, and apply learning to execute
   accepted / drop rejected proposals, each needing its own idempotency and
   test coverage). Scoped instead of built; see the tracked bead for the
   concrete design.

Idea #3 is scoped (tracked in beads) but not yet built or estimated as a
commitment.
