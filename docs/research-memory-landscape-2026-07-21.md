# Memory-systems landscape scan (lean pass, 2026-07-21 night)

Method: direct web research by the orchestrating agent, no agent fan-out
(per Rustom's explicit instruction after the July 20 workflow blowout).
Purpose: validate/sharpen the three feature picks for the overnight build.

## Competitive read

- **Zep / Graphiti** (temporal knowledge graph): the reference point for
  temporal memory. Bi-temporal edges (valid_at/invalid_at + ingestion
  time), point-in-time "as-of" retrieval slices, contradiction handling by
  edge invalidation. Weaknesses relevant to us: heavy stack (Neo4j/cloud),
  reported ~600k tokens of graph per conversation vs Mem0's ~1.8k, hours of
  post-ingestion lag before answers appear, and their own docs say temporal
  filtering in the search API is still planned, not shipped. Sources:
  getzep.com/platform/graphiti, github.com/getzep/graphiti, Zep arXiv
  2501.13956, help.getzep.com.
- **Mem0**: three-tier scopes (user/session/agent), vector+graph+KV; the
  managed drop-in leader. LongMemEval (GPT-4o): Zep 63.8% vs Mem0 49.0%
  per the comparison press. No belief timeline, no signed provenance.
- **Letta (MemGPT)**: OS-style paging + self-editing memory blocks;
  recursive summarization costs fidelity for verbatim recall.
- **claude-mem / local tools**: capture+inject with FTS/Chroma; no belief
  revision, no temporal validity, no provenance (see 2026-07-21 session
  comparison).
- **Research systems**: RMM (prospective reflection = periodic distillation
  into a personal memory bank; retrospective reflection = retrieval
  self-tuning), A-Mem (agentic self-organizing notes), MemGen, GAM
  (hierarchical graph memory). Common thread: reflection loops and
  self-editing are where the field is heading; none ship local-first with
  auditable provenance.

## What this validates for tonight

1. **As-of queries + belief timelines** (F1): parity with Zep's flagship
   concept, delivered locally on SQLite, with our extra: every step in the
   timeline carries signed-channel provenance and an audit event. Zep has
   the model; their search-side filtering is "soon". We ship it working.
2. **Morning brief** (F2): productized prospective reflection (RMM), and
   the direct answer to the founding complaint ("recount yesterday").
   Nobody in the local-first cohort has a daily belief digest.
3. **Belief-health dashboard + review queue** (F3): visible self-editing
   with human sovereignty (approve/reject staged consolidate actions).
   Letta self-edits opaquely; we self-edit with receipts and a veto.

## Explicitly deferred (good ideas, not tonight)

- Retrieval self-tuning from feedback (retrospective reflection, RMM).
- Spaced-repetition resurfacing cadence per memory (rehearsal scheduling).
- Procedural memory (learned preferences/skills as a separate kind).
- Graph-hop retrieval fusion at query time (Zep-style multi-hop ranking).

No em dash. Sources gathered 2026-07-21 ~22:20 EDT.

---

# Addendum (same night, ~23:30): the proper deep-research pass

Rustom's correction: features must pass the Linus test (build what YOU
need). Two sonnet agents (within the vault's own 2-4 agent rule) mined
issue trackers and the tooling landscape; the orchestrator verified
Claude Code checkpoint semantics directly.

## Verified gaps (with receipts)

1. **Cross-session file time machine + rollback DETECTION: nothing does
   it.** Claude Code checkpoints: per-session, Write/Edit-only, 30-day,
   restore-only (code.claude.com/docs/en/checkpointing). Cursor: same
   pattern. Aider: real git auto-commits, no alerting. ShadowGit: closest
   (background save snapshots + MCP) but no regression alerting, not
   transcript-sourced. -> Feature X (memsub filehist).
2. **Dropped-task resurfacing: does not exist anywhere.** Users beg:
   anthropics/claude-code #11455 (session handoff), #63023 (background
   agents die silently, no recovery), letta #2666 (blank slate per
   session). claude-mem treats all sessions uniformly, no abnormal-
   termination logic (verified in docs). -> Feature Y (memsub loops).
3. **Decision ENFORCEMENT vs retrieval: only an unstarted proposal**
   (letta #3320 "memory governance"). CLAUDE.md documented as unreliable
   at scale ("read but not reliably followed"). -> Feature Z
   (memsub decisions + SessionStart preflight hook).
4. Cross-tool corroboration of our existing theses: mem0 #4573 (user
   audit: "97.8% of 10,134 entries were junk") = consolidate/curation;
   mem0 #2820 + claude-mem #2469 + graphiti #819 = cost-accounting gap
   (roadmap); claude-mem's top complaints are reliability/noise, which
   endorses memsub's zero-daemon, silence-default design.

## Same-night outcomes (executed, not planned)

- X shipped: 6,775 file states indexed in 9s; first live run found and
  restored 33 rolled-back files in memory-sublayer and 19 in book-dna
  that the manual recovery missed (ledgers in results/). Both repos now
  clean vs transcript truth; suites 530 and 366 passing. 30-min cron
  sweep installed with inbox alerting.
- Y shipped: memsub loops found 16 dead genome-annotation sessions
  (collapsed to one) and a genuinely lost July 16 ask about the
  writer-facing tool / serialized fiction test.
- Z shipped: memsub decisions + scripts/memsub-decisions-hook.sh
  (SessionStart preflight; installation is the user's call since it
  edits ~/.claude/settings.json).

Known limitation, on the record: rollback detection only fires when disk
EXACTLY matches an older known snapshot; a rolled-back-then-diverged file
is invisible until a dependent import breaks (hit twice in book-dna,
fixed by restoring dependents). Roadmap: restore --with-deps and a
similarity-based detector. No em dash.
