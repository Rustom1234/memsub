---
id: "memsub-template-do-not-edit"
verdict: ""
---

# Memory note template & verdict guide

This note documents the fields on every memory note in this vault and the verdict values `memsub vault apply` understands. It is not itself a memory: its `id` matches no episode in the store, so `vault apply` skips it as an orphan.

## Frontmatter fields (written by `memsub vault sync`, read-only)

- `id` - the memory's stable id in the store.
- `namespace` - which namespace this memory belongs to.
- `kind` - `fact` | `preference` | `decision` | `event`.
- `type` - `observed` (from a transcript or a human correction) or `inferred` (agent-derived, e.g. by `memsub sleep`).
- `status` - `active` | `superseded` | `forgotten`.
- `created`, `last_updated`, `last_used` - ISO timestamps.
- `confidence` - extractor confidence, 0.0-1.0, if the memory carries one.
- `content_hash` - fingerprint of the body text, used to detect a human edit; do not hand-edit this field.

## Reviewing a memory: the `verdict` field

Set `verdict` in a note's frontmatter, or edit the text under `## Content`, to tell `memsub vault apply` what to do. Only two verdict values are understood; anything else is ignored:

- `verdict: confirm` - pin this memory. It stays active and is never aged out by consolidation's decay pass.
- `verdict: reject` - forget this memory. It is marked forgotten (kept in the store for audit, no longer active or retrievable).

A third gesture needs no `verdict` field at all: edit the text under `## Content` to something else. That is a correction - the old memory is superseded by a new, human-verified one carrying your edited text.

**Precedence if a note carries more than one signal:** reject beats a body edit beats confirm beats no-op. Full mechanics: docs/obsidian-layer.md.
