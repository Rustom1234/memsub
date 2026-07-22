# Engineering review: temporal blindness, NFS corruption, and the reliability program

Date: 2026-07-21 (evening). Status: PLAN ONLY, no code written yet.
Scope: everything surfaced by the 2026-07-21 session: the "recount yesterday"
eval failure, the real-vault corruption, the tracked-file rollback, and a
test program to find what else is broken before OpenAI Build Week.

Evidence base: transcript-derived ground truth for July 20 (verified against
docs and git), live queries against the recovered vault, sqlite forensics,
and store.py/cron inspection. Every claim below is from an executed check,
not recollection.

---

## Part 1: Issues, root causes, and decisions

### I1. Temporal blindness (P0, the pitch-critical one)

**Symptom.** "what did I work on yesterday" against the real vault returns
July 13 cold-email memories. Targeted, vocabulary-matched queries DO recall
July 20 accurately (Build Week decision at rank 1, the $247.81 deep-research
memory, the Poppy War fix with commit hash).

**Three stacked root causes:**
1. Episodes carry transaction time (when extracted), not event time (when it
   happened). The July 15-20 extraction gap meant a week of events all got
   stamped 2026-07-21.
2. The bi-temporal fields (`valid_from`/`valid_to`, memsub/temporal.py) exist
   and the Arjun demo showcases them, but the real extraction pipeline never
   populates them. The demo demos a feature the product does not run on real
   data.
3. No temporal query layer: "yesterday" is treated as a lexical token, not a
   date range. No date filters exist on SDK search, CLI search, MCP
   search_memory, or /ask.

**Decision (proposed).** Event time becomes `ts`:
- Extraction stamps `ts` from the source transcript's message timestamps
  (the JSONL has them; format_episode's session_date handling shows the
  plumbing half-exists). `provenance.extracted_at` records transaction time.
- Rationale: every consumer (recency ranking, decay, timelines, dashboard
  activity) keys on `ts`, and users always mean event time. Audit purposes
  are served by provenance plus the event log.
- Alternative considered and rejected: a separate `event_ts` column keeps
  `ts` pure but forces every ranking/decay/timeline call site to choose,
  which is exactly the ambiguity that caused this bug.
- Migration: a backfill pass over all 6,031 episodes re-deriving story time
  from source transcripts via `extraction_checkpoints.source_path` +
  provenance turn spans. Backfill is a new-db-then-swap operation, never an
  in-place UPDATE (append-only discipline).

**Temporal query layer (P0, minimal form).** Deterministic parser for
relative and absolute dates ("yesterday", "last week", "on July 20",
"this month") resolving to a date range; range becomes a hard filter plus a
boost, exposed as `date_from`/`date_to` on SDK/CLI/MCP/ask. No LLM in the
parse path for v1. LLM fallback for exotic phrasings is P2.

**Acceptance criteria.** "what did I work on yesterday" run the day after a
worked day returns >= 3 of 5 top hits from that day. The pre-registered
day-recount eval (Part 2) score improves on temporal questions from 0/5 to
>= 4/5 without regressing non-temporal questions.

### I2. SQLite WAL over NFS with multi-machine writers (P0, root cause of both corruptions)

**Evidence.** `stat -f`: both `~/.memsub` and the repo are NFS. store.py
line 181 forces `journal_mode = WAL` (commit 3cc6c89, July 16 01:28). The
corrupt db's main image mtime is July 16 01:45, seventeen minutes after WAL
was enabled, while its WAL grew to 6.9MB through July 21 17:30. SQLite
documents WAL as unsupported on network filesystems (the -shm file is
host-local mmap coordination). Writers existed on at least host-a and host-b
(vault serve instances, daily_extract cron, MCP servers). The tracked-file
rollback (store.py and 9 others reverting to July 16 images with preserved
mtimes) is consistent with the same NFS client-cache incoherence.

**Decision (proposed).** Single-writer, local-disk architecture:
1. The live db moves to machine-local disk on ONE designated writer machine
   (or the future VPS, which solves this permanently). `~/.memsub/memory.db`
   becomes a pointer/config, not the live file.
2. Every other machine is a reader: talks to the writer's vault-serve HTTP
   API, or reads a snapshot copy explicitly marked read-only.
3. Guardrail in store init: detect NFS (statfs magic) and REFUSE to open
   WAL-mode rw, with a clear error and an explicit override flag. This turns
   the next misconfiguration into an error message instead of corruption.
4. Snapshot discipline: the writer machine pushes a checkpointed snapshot to
   NFS nightly (backup, not live).

**Open question for Rustom.** Which machine is the writer until the VPS
exists: host-b (this one) or host-a? The cron currently appears to live on
host-a (no memsub crontab on host-b), which also explains I3.

### I3. Extraction pipeline reliability (P0)

**Symptom.** No episodes dated July 15-20 (cron gap), then a catch-up sweep
July 21. The cron is currently crash-looping on the corrupt db (log-only
failure, nobody alerted). The dashboard's LAST EXTRACTION tile reads
"unknown".

**Fixes.**
- Staleness surfacing: dashboard tile and a `memsub doctor` check go red
  when newest episode is > 36h old. The tile exists; wire it.
- Lock hygiene: `daily_extract.lock` gets an expiry (stale-lock takeover
  with logged provenance) so a dead run cannot block forever.
- Failure visibility: extraction failure writes a visible marker the
  accountability hook / dashboard can pick up, not just a log line.
- Cron ownership follows the single-writer decision (I2).

### I4. Audit-trail durability (P0 hardening, P1 re-derivation)

**Damage.** ~93% of memory_events (supersede/quarantine/retrieval history)
sat on the corrupt pages and are gone; 239 of ~3,470 survived. Episode
content survived fully. Consequence: statuses collapse to "active", so
previously-superseded beliefs can resurface in retrieval. That is a direct
hit on the belief-revision pitch until repaired.

**Fixes.**
- Event-log JSONL mirror (P0): every log_event append also appends one line
  to a plain-text JSONL sidecar in the vault directory. Append-only single-
  writer text on NFS is low-risk, git-friendly, and gives a rebuild source
  the next time SQLite loses pages. Rebuild tool: db events from JSONL,
  verified by round-trip test.
- Status re-derivation (P1): run `consolidate --stage` on the recovered db
  and review the proposed supersede/dedup actions before applying (the
  staging feature exists, commit 803c3ba). Manually audit a sample of 30
  proposed supersedes; the measured precision becomes pitch material
  ("belief revision precision on my own 6k-episode vault: X%"), per the
  no-fabricated-results rule.

### I5. Serving and MCP robustness (P1)

**Symptoms.** MCP server died mid-eval (code replaced under a running
process, then corrupt db). vault serve kept serving a malformed db. Three
server instances had accumulated across ports/machines, one serving stale
in-memory state from a rebuilt-under-it vault.

**Fixes.**
- `memsub doctor`: one command that checks db quick_check, WAL size,
  NFS detection, staleness, ollama reachability, embedder mode actually in
  use, and namespace episode counts. First thing any debugging session runs.
- Startup integrity gate: servers and MCP run quick_check on open; on
  failure they start in a degraded read-only mode with a banner, never a
  stack trace to a dead connection.
- Single-instance guard: pid/port lockfile per (db, port); starting a second
  instance names the first and refuses.
- Supervision: systemd --user units (or tmux with restart) for vault serve,
  MCP, and cron on the writer machine, replacing ad-hoc nohup.

### I6. Retrieval quality: vocabulary sensitivity and flat scores (P2)

**Symptoms.** "what does Arjun/Rustom charge" class misses (paraphrase with
no shared vocabulary); "hit usage limits" retrieved July 14 limit events
over the July 20 memory phrased as "$247.81"; RRF scores are flat 1/(60+r),
so consumers cannot threshold on confidence.

**Fixes, in measured order (each gated by the eval in Part 2, no fix ships
without a before/after number):**
1. Temporal boost from I1 (many "wrong week" misses are actually temporal).
2. Query expansion: 2-3 local-LLM paraphrases, union the RRF pools.
3. Optional rerank pass (rerank.py exists) for k>10 candidate pools.
4. Score calibration: expose per-signal scores so /ask and MCP can say
   "weak match" honestly instead of confidently returning rank-1 noise.

### I7. Repo hygiene (P0, one command, standing rule)

HEAD is July 16; ~100 files of Build Week work are uncommitted, twice nearly
lost. Decision: commit immediately after review (already offered), then the
standing rule from project CLAUDE.md (push at session end) actually gets
followed; the accountability hook already surfaces modified-repo state at
session start, which is the reminder mechanism.

---

## Part 2: Test program to find the NEXT issues

The principle: every fix above gets a pre-registered, executed check, and a
separate adversarial track goes looking for what we have not found yet.

### T1. Day-recount eval (pre-register BEFORE any I1 work)

This session produced transcript-verified ground truth for July 13-21.
Freeze 20 questions across four classes, with expected evidence episodes:
- Temporal-relative: "what did I do yesterday", "last Monday" (5)
- Decision recall: "what did I decide about X and why" (5)
- Number surfacing: "what did the deep-research run cost", "what rate" (5)
- Follow-through: "what happened after I complained about Poppy War" (5)
Score: evidence-hit@5 per class. Run once on the current vault (baseline,
temporal class is 0/5 today), rerun after each I1/I6 change. This doubles as
the Build Week before/after exhibit, and it is exactly the vault-eval
methodology already validated on 2026-07-16.

### T2. Fault-injection drills (find crashes before reality does)

Fixture dbs checked into tests (small, synthetic): page-corrupted main file,
WAL-without-main mismatch, stale -shm, empty namespace, missing entities
tables (the schema-drift case that already happened once). For each surface
(SDK, CLI, MCP, vault serve, extraction): assert actionable error or
degraded mode, never a traceback or silent wrong answer. Plus: ollama down
during embedder=auto must LOUDLY log the hashing fallback (silent fallback
already caused confusion twice).

### T3. Property tests on the memory model

- Append-only invariant: nothing but the quarantine tamper-detector ever
  observes a content-row mutation; episodes table row count is monotonic.
- Status derivation is a pure function of the event log (rebuild from JSONL
  mirror equals live status for every episode).
- Bi-temporal chains: for any supersede chain, validity windows are
  contiguous and non-overlapping (the consulting-rate/freelance-rate
  invariant, generalized).
- Determinism: same db, same query, same k => identical ranking across two
  processes (catches env-dependent embedder drift).

### T4. Cross-surface consistency sweep

Same query through SDK, CLI, MCP, and /ask must agree on top-k ids. Catches
namespace/env drift (the default-vs-claude-code namespace confusion cost 20
minutes today and would confuse any Build Week judge instantly).

### T5. Consolidate audit on real data (P1, produces a pitch number)

`consolidate --stage` on the recovered 6k-episode vault; sample 30 proposed
actions; hand-grade. Deliverables: measured supersede precision, measured
false-supersede rate, runtime and LLM-call count on qwen3:8b. If precision
is bad, that is a finding we want BEFORE a judge finds it.

### T6. Demo reproducibility drill

Clean clone on a fresh machine: `build_demo_vault.py --fresh` + vault serve
+ the six screenshots, following only the docs. Time it, note every
undocumented prerequisite (ollama model pull, port, --no-sandbox flag for
headless chromium on kernels with user-ns restrictions). The Build Week demo
must survive a machine we have never run it on: that is the actual submission
environment.

### T7. Corruption recovery runbook test

The row-wise salvage that worked today becomes a documented, tested script
(`scripts/salvage_db.py` equivalent, written in the implementation phase):
given a malformed db+WAL, produce clean.db + a loss report (rows recovered
per table, bad rowids). Test against the T2 fixtures. Today it was improvised
under pressure; next time it should be one command.

---

## Part 3: Sequencing

**P0, before Build Week submission (order matters):**
1. Rustom runs the staged db swap (commands in session notes), designates
   the writer machine, db moves to local disk there (I2).
2. Commit the repo (I7). Everything else lands as reviewed commits.
3. Pre-register and run T1 baseline (before any retrieval/temporal change).
4. Event-log JSONL mirror (I4) so no further history is losable.
5. Story-time stamping + backfill + minimal temporal query layer (I1).
6. Extraction staleness surfacing + lock expiry (I3).
7. Rerun T1; the temporal before/after goes in the submission.

**P1, same week if time allows:** memsub doctor, startup integrity gates,
single-instance guard, NFS-WAL refusal guard, consolidate re-derivation +
T5 audit number.

**P2, post-submission:** query expansion + rerank + score calibration (gated
on T1 deltas), T3 property suite, T4 sweep in CI, supervision units.

**Explicitly deferred:** any UI work, multi-user namespaces, cloud sync.
None of them matter until the memory substrate stops eating itself.

---

## Part 4: Open questions for Rustom (blocking P0 item 1 only)

1. Writer machine until the VPS: host-b or host-a? (Recommendation: host-b,
   it is the machine you drive remote sessions from now; move the cron here.)
2. `ts` semantics: agree that `ts` becomes event time with
   `provenance.extracted_at` as transaction time? (Recommendation: yes;
   the alternative doubles every call site's ambiguity.)
3. Consolidate re-derivation budget: local qwen3:8b is free but the 6k-
   episode staging pass will take real wall-clock; run overnight?

No em dash used. Nothing in this document has been implemented.
