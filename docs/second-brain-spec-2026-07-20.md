# Second Brain: spec (2026-07-20)

Rustom's requirements, verbatim: "i never wanna forget an idea and i want to be
linked up to other ideas sometimes, i wanna find connections. also the UI needs
to be way cooler - i wanna see the obsidian graph. at the same time, i want
things to be more organised - not just a mess of claude notes. i want the
second brain but i want it with summarised high quality notes."

Capture is already live (cron sweep + extraction + recall hook, installed
2026-07-20). This spec covers the four layers on top.

## Layer 1: Distillation (the librarian)

The core fix for "a mess of claude notes". A nightly cron pass (local qwen3:8b
only, zero cloud calls) that turns raw extracted memories into a curated tier:

- Cluster active raw memories by topic: seeded from the entity_links graph
  (entities shared across episodes) refined by embedding similarity; a topic
  is a stable cluster with 3+ memories.
- For each topic, write or update ONE evergreen note at
  vault/topics/<slug>.md with fixed structure: a one-paragraph overview in
  plain prose, key facts as bullets (each fact linking to its source raw note
  by wikilink, preserving provenance), a timeline section when the topic's
  facts carry temporal windows (uses the bi-temporal fields from the temporal
  upgrade), and open questions the raw notes imply but do not answer.
- Incremental and idempotent: a topic is re-distilled only when its raw
  member set changed (hash of member ids + content hashes); unchanged topics
  cost zero LLM calls. Quality bar in the distillation prompt: the note must
  read like a human Zettelkasten note, not a log; no session IDs or
  extraction jargon in prose.
- Human sovereignty: evergreen notes round-trip through the existing vault
  apply machinery; a human edit marks the section human_verified and
  re-distillation must merge around it, never overwrite it. Verdict: reject
  kills a topic note.
- Raw notes are never deleted; they become the archive tier, visually
  de-emphasized in the UI, still searchable and linked.

## Layer 2: The graph (the view he asked for)

Interactive force-directed graph inside vault serve (canvas rendering,
vanilla or d3 via CDN, phone-capable):

- Nodes: evergreen topic notes (large, accent-colored) and raw notes (small,
  dim, toggleable off by default so the default view is the organised brain,
  not the mess).
- Edges: wikilinks plus shared-entity links (from entity_links), with edge
  weight by co-occurrence count.
- Interactions: drag/zoom/pan; tap a node to preview, tap again to open;
  search box highlights matching nodes and fades the rest; namespace and
  time filters (show what my brain learned this week).
- Every note page also gets a local constellation: a mini-graph of its
  immediate neighborhood.

## Layer 3: Connections (serendipity engine)

"Find connections" means surfacing links that do NOT already exist:

- Related-ideas panel on every note: embedding-nearest notes that share no
  wikilink or entity with this one (already-linked neighbors are shown
  separately); the unlinked-but-similar set is where discovery lives.
- Dashboard "connection of the day": the highest-similarity unlinked topic
  pair in the vault, one per day, with a one-tap "link these" action that
  writes a wikilink (a human-authored edge, applied via vault apply).
- All computed from existing embeddings; no LLM calls at query time.

## Layer 4: Deliberate idea capture

Automatic capture gets ambient ideas; deliberate ones deserve a front door:

- memsub idea "..." CLI: writes a first-class idea-type memory immediately
  (user_stated channel, provenance-signed), bypassing the 30-min extraction
  wait, and echoes which existing topics it attached to.
- Ideas render with a distinct badge in vault UI and graph; the recall hook
  already surfaces them in future sessions like any memory.

## Build order and cost

1. Layer 2 graph + Layer 3 connections: one agent, UI + read-only endpoints,
   no LLM cost anywhere.
2. Layer 1 distillation + Layer 4 idea CLI: one agent, local-LLM only;
   first full distillation run is a few hours of local qwen, then nightly
   increments are cheap. Cron line added to the installer.
3. Evaluate honestly: vault-eval (the LongMemEval-style harness in
   ~/projects/vault-eval) re-run over the distilled vault vs raw vault to
   check recall does not regress while note quality rises; plus a hand
   review of 10 topic notes against the Zettelkasten bar.

Constraints throughout: local-first, no cloud calls, opt-in, default paths
untouched, provenance and human-edit sovereignty preserved, everything
committed and bundled per the durable-output rules.
