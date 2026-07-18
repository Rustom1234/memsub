
## 2026-07-17 18:55 (Fable orchestrator, remote session)
- Durable capture layer 1 built and tested: ~/scripts/memsub-inbox.sh (SessionEnd/PreCompact hook target), ~/scripts/memsub-inbox-sweep.sh (5-min cron sweep, tested live, captured 2 transcripts into ~/.memsub/inbox/).
- Crontab install and ~/.claude/settings.json hook edit are blocked for agent sessions by the permission classifier. One-shot installer for Rustom: bash ~/scripts/install-durable-capture.sh (idempotent, installs 3 cron lines + 2 hooks with jq merge and .bak).
- Technical visuals written: docs/improvement-visuals-2026-07-17.md (durable capture, fact fusion, bounded decomposition, provenance signing, J-score comparability).
- Background agent (sonnet) implementing Part B items 1+2 (write-time fusion, query decomposition) with eval streaming to results/multihop-improvements-2026-07-17.json.

## 2026-07-17: Multi-hop improvements (Part B items 1 and 2 of docs/improvement-plan-2026-07-17.md), implementation session

### Context

Target: the 94.6% multi-hop failure rate documented in docs/error-analysis-locomo.md
(dev split, budget 1000, PolicySystem/dense-only). Note the shipped default retrieval
is actually hybrid v2, not the plain policy that error-analysis.py measured; per
docs/benchmark.md the shipped hybrid v2 stack's real dev multi-hop number is 20.3%
evidence-all / 77.0% evidence-any at budget 1000 (holdout: 25.0% / 79.3%). That hybrid
v2 number, not the 94.6% figure, is the correct current baseline to improve on, and is
what this session's eval reproduces before measuring deltas.

### Environment check (before coding)

- ollama daemon was running but had zero models pulled (`ollama list` empty). Pulled
  qwen3:8b (`ollama pull qwen3:8b`), confirmed reachable at localhost:11434.
- A default qwen3:8b call (thinking enabled, ollama's default) took ~35s for a trivial
  one-word answer on this machine's GPU (RTX A4000, 16GB). At that rate a per-question
  LLM call over the full 383-question dev slice would take ~3.7 hours, over budget.
  `curl` with `"think": false` in the request body cut the same call to ~1.3s (ollama
  0.31.2 supports the native think toggle for qwen3). Added an optional `think: bool |
  None = None` constructor param to `OllamaBackend` (memsub/backends.py): default None
  omits the key entirely, so every existing caller (consolidate contradiction pass,
  sleep inferences, rerank, CLI) is byte-for-byte unchanged. Only this session's eval
  script constructs `OllamaBackend(think=False)` explicitly. This is the one piece of
  shared-file surgery outside consolidate.py/hybrid.py; without it the Task 3 measurement
  would not fit the time budget.

### Assumptions (stated before coding, per Karpathy rule 1)

1. "Same entity across sessions" (Task 1) reuses `memsub.sleep._extract_entities`
   (regex capitalized-word extraction, already tested and used by sleep.py/vault.py for
   the same cross-session-entity purpose) rather than inventing new NER. "Use the
   existing retrieval/embedding machinery to find candidates" is satisfied by reusing
   the cosine similarity matrix consolidate.py's dedup phase already computes, to pick
   the most mutually-coherent members when a candidate group exceeds the group-size cap.
2. Fusion is gated on the same condition as the existing contradiction pass ("requires a
   backend; skipped cleanly without one"), no new CLI flag. This matches the existing
   `--no-llm` on/off switch instead of adding a second flag for a second LLM phase.
3. Idempotency is implemented via a deterministic fused-episode id (sha1 of
   namespace + entity + sorted source ids). Re-running consolidate against an unchanged
   store recomputes the same id, finds it already in the store, and skips (no LLM call,
   no new row). This is stronger than "no duplicate rows": on an unchanged store the
   fusion pass makes zero LLM calls on rerun, matching the "write time, rare" cost pitch.
4. Sources are preserved by construction: fusion never adds source episodes to the
   `removed` set (dedup/contradiction's mechanism for hiding an episode from `active()`).
   Sources get a non-terminal `fused_into` event (auditability, does not affect status).
5. Task 2's "opt-in mode, off by default" is implemented as new methods
   (`rank_decomposed` / `search_decomposed`) rather than a boolean flag on the existing
   `rank`/`search` path: the existing path is byte-for-byte unchanged unless a caller
   explicitly calls the new method with a backend. Simplest form of "off by default."
6. Task 3's measurement metric is evidence-all / evidence-any at budget 1000 on the dev
   split (LOCOMO conversations 1-3), the exact metric family error-analysis.py and
   dev_eval.py already use and the source of the 94.6% figure this task targets. Not the
   separate end-to-end answer-quality F1 harness (holdout_answer_quality.py), which
   answers a different question (local-model-as-answerer) and was not the harness cited
   for the multi-hop number in question.
7. To isolate fusion's effect from confounds, the eval's ConsolidationConfig disables
   decay (`decay_value_threshold=0.0`, the same convention `PolicySystem(consolidated=
   True)` already uses in memsub/bench.py for synthetic/non-wall-clock timestamps) and
   sets `contradiction_min_sim` above 1.0 so the contradiction pass never fires (LOCOMO
   turns all share kind="turn", so nearly every pair would otherwise qualify and burn the
   LLM budget on a phase outside this task's scope). Only dedup (default threshold,
   already-shipped behavior) and the new fusion phase are active in the eval.
8. Per the repo-level rule "NEVER git push, NEVER add a remote" for this task (the
   project CLAUDE.md's session-end mandatory-push workflow is superseded by this task's
   explicit instruction and by the global rule that pushes need Rustom's explicit
   go-ahead); every checkpoint below is a local commit only.

### Success criteria (stated before coding, per Karpathy rule 4)

- Task 1: `pytest tests/test_consolidate.py` covers (a) a fusion entry is created linking
  >=2 same-entity, cross-session sources, (b) sources remain `active` after fusion
  (never superseded/deleted), (c) re-running consolidate on the unchanged store produces
  zero new fuse actions and zero additional LLM calls, (d) no backend -> fusion phase is
  a no-op like the contradiction phase already is.
- Task 2: a new test module covers (a) `rank_decomposed` with a backend that returns 2
  sub-queries makes exactly 1 LLM call and merges 3 rankings (1 original + 2 sub-query)
  via RRF, (b) a backend returning `[]` (no split needed) falls back to the plain
  `rank()` order with zero extra retrieval calls beyond the original, (c) the existing
  `rank`/`search` path is provably unchanged (no new required constructor args, existing
  tests still pass unmodified).
- Task 3: results/multihop-improvements-2026-07-17.json contains one JSONL record per
  dev question per condition (baseline / fusion / fusion_decompose), streamed and
  flushed as the eval runs, plus one summary record per condition. Numbers reported in
  this file and only this file (never recalled from context).
- Whole-suite `pytest` stays green after each step; no em dashes anywhere written.

### Step log

- Task 1 (write-time fusion) implemented in memsub/consolidate.py (phase 3, between
  contradiction and decay), plus the think= addition to memsub/backends.py. 6 new tests
  in tests/test_consolidate.py, all pass. Full suite: 283 passed. Committed
  (f2717e5): "Add write-time cross-session fact fusion (MIRIX-style) to consolidate".
- Task 2 (bounded decomposition) implemented in memsub/hybrid.py as two new opt-in
  methods, `rank_decomposed` / `search_decomposed`, on `HybridRetriever` (and therefore
  on `HybridSystem` in memsub/bench.py, which subclasses it). 5 new tests in
  tests/test_hybrid_features.py, all pass. Full suite: 288 passed.
- Task 3 (measure): scripts/multihop_improvements_eval.py ran the full LOCOMO dev slice
  (383 questions, conversations 1-3, budget 1000), no smoke-test fallback needed (wall
  time ~150s once `think=False` was applied, not the ~2-3.7h a default-thinking qwen3
  run would have cost). First attempt used memsub.bench.HybridSystem's bare
  benchmark-era defaults (all boosts off) and got a materially weaker baseline (59.5% /
  73.6% total) than the shipped hybrid v2 default; caught this before trusting any
  delta, fixed the eval to pass the frozen v2 boost params explicitly
  (HYBRID_V2_KWARGS), reran, and confirmed the corrected baseline reproduces
  docs/benchmark.md's documented dev totals exactly (72.8% / 85.1%, multi-hop 20.3% /
  77.0%) before reading anything into the fusion/decompose deltas.

  Results (from results/multihop-improvements-2026-07-17.json, n=383, budget 1000):

  | condition | multi-hop all | multi-hop any | TOTAL all | TOTAL any |
  |---|---|---|---|---|
  | baseline | 20.3% | 77.0% | 72.8% | 85.1% |
  | fusion | 20.3% | 74.3% | 72.6% | 84.3% |
  | fusion + decompose | 20.3% | 74.3% | 72.6% | 84.3% |

  Honest result: neither intervention improved multi-hop evidence recall on this dev
  slice. Fusion alone cost 2.7pp of multi-hop evidence-any (fused entries compete for
  the token budget but can never themselves count as evidence under this turn-level
  metric, the same limitation already on record for the sleep extension in
  docs/benchmark.md). Decomposition on top of fusion was an exact wash versus fusion
  alone: per-question diffing of the JSONL records confirms the decompose call did fire
  and did change retrieval for 6/383 questions, 3 gained and 3 lost, netting to zero,
  consistent with the already-reverted "heuristic query decomposition" finding in
  docs/benchmark.md (LOCOMO's "multi-hop" category is mostly single-topic aggregation,
  not real chainable sub-questions). Full writeup: docs/multihop-improvements-2026-07-17.md.
  This does not settle whether fusion has real value: the fair test is the separate
  end-to-end answer-quality harness (scripts/holdout_answer_quality.py's protocol,
  local model as answerer, token-F1), which was out of scope for this session and is
  the natural next step before any ship/no-ship call on fusion as a default.

  Both features remain opt-in and are not wired into any default code path, so this
  negative result costs nothing to ship as-is: the audit-trail/provenance value of
  fusion and the bounded-cost design of decomposition stand independent of this one
  metric's verdict on this one dev slice.

- Final suite check: `pytest` 288 passed. No em dash character in any new or modified
  file (checked with grep for the long-dash character across every touched file).
- Commits (all local, no remote/push per this task's explicit instruction and the
  global no-push-without-go-ahead rule):
  - f2717e5 Add write-time cross-session fact fusion (MIRIX-style) to consolidate
  - a172f1b Add bounded query-time decomposition to HybridRetriever
  - 94fbce8 Measure write-time fusion and bounded decomposition on the LOCOMO dev slice
  - 066e863 Fix stray em dash character in WORKLOG.md self-check note

## 2026-07-17 (follow-up): fair test, answer quality instead of evidence recall

The coordinator's own read of the first measurement was correct: the evidence-all/any
metric structurally cannot credit a fused entry (right information, wrong id), so that
run could only ever cost fusion budget, never show its value. Follow-up:
scripts/multihop_answer_quality_eval.py measures token-F1 answer quality on the
multi-hop dev slice instead, reusing scripts/metrics_local.py's existing machinery
unchanged (ANSWER_SYSTEM, BUDGET, FROZEN params, token_f1: the same protocol behind the
documented 0.152 holdout number) rather than building a new harness.

Slice: LOCOMO dev conversations 1-3, category-1 questions with a non-empty gold answer,
n=74 (full slice, already above the 60-question pre-registered minimum; a timing check
on one real answer call, ~0.65s with think=False, confirmed the full 3-condition run
would finish in minutes, well under the 90-minute cap, so the seed-2026 subset fallback
was never triggered). Actual wall time: 270s. Same fusion isolation as the first eval
(contradiction and decay disabled in ConsolidationConfig so only dedup and fusion touch
the ingested set, see assumption 7 above).

Results (results/multihop-answer-quality-2026-07-17.jsonl, n=74 per condition, budget
1000, qwen3:8b as answerer):

| condition | n | mean token-F1 | exact-substring rate |
|---|---|---|---|
| baseline | 74 | 0.1946 | 0.0676 |
| fusion | 74 | 0.1907 | 0.0676 |
| fusion + decompose | 74 | 0.2029 | 0.0676 |

Verdict: fusion alone still does not help (0.1946 -> 0.1907, -2.0% relative, same
flat-to-negative direction as the evidence-recall run: both metrics now agree fusion by
itself is not moving multi-hop quality on this dev slice). Fusion plus decomposition
shows a modest positive delta (0.2029, +4.3% relative vs baseline, +6.4% vs fusion
alone), flipping the sign from the evidence-recall run's exact wash. Read cautiously:
effect size is 0.008-0.012 absolute F1 at n=74 with one run per condition (no repeated
trials), plausibly within single-run noise, and exact-substring rate is identical
(5/74) across all three conditions. Honest reading: the gain tracks with decomposition,
not fusion. The MIRIX write-time-fusion hypothesis is parked on two independent
measurements now (evidence-recall and answer-quality) that both say fusion alone is not
the lever; decomposition is worth keeping as a cheap opt-in on the strength of this
run, but neither result overrides moving to the graph layer (item 3,
docs/improvement-plan-2026-07-17.md Part B) as the next real lever. Full writeup
appended to docs/multihop-improvements-2026-07-17.md.

Final suite check: `pytest` still 288 passed (this follow-up added only a new eval
script under scripts/, no library code changed). No em dash character in any new file.

---

## 2026-07-17: Part B items 4+5 (HMAC channel provenance, poisoning re-run), implementation session

### Assumptions (stated before coding, per Karpathy rule 1)

1. Channel + signature live inside the existing `provenance` JSON dict on Episode
   (`provenance["channel"]`, `provenance["channel_sig"]`), not as new SQLite columns.
   provenance is already a free-form JSON blob every write path already populates;
   this needs zero schema migration and the "unsigned-legacy" grandfather case falls
   out for free (old rows simply lack the two keys).
2. Trust ranking replaces `_is_inferred`'s binary swap with a 4-level integer rank
   (quarantined -1, legacy-inferred 0, legacy-observed/verified-llm_inferred-or-imported
   1, verified-user_stated 2), chosen so legacy data (no channel/sig) reproduces the
   exact old `_is_inferred` ordering bit-for-bit (verified by running the full suite
   unmodified: all 288 pre-existing tests pass with zero test-file changes to
   test_sleep.py/test_consolidate.py/test_vault*.py), while a signed user_stated row
   can never be outranked by any unsigned-legacy row (max legacy rank 1 < 2).
3. Quarantine reuses the existing supersede/forget event-log mechanism: "quarantined"
   added to `store.TERMINAL_EVENTS`, so `store.active()` (and therefore every existing
   retrieval path that reads it) excludes a quarantined row automatically, with zero
   changes needed in hybrid.py/retrieval.py/bench.py. This is the same append-only,
   never-delete pattern the codebase already uses for supersede/forget.
4. Real write paths tagged per the task's three buckets: extraction (cli.py) and wiki
   ingest (wiki.py) -> user_stated (never produce kind="inference"); sleep.py's
   generate_inferences -> llm_inferred, its summaries/links -> user_stated (same
   kind=="inference" check `_is_inferred` already used); vault.py human correction
   and promote_notes -> user_stated; sdk.py MemoryClient.add (the generic/MCP
   add_memory entry point) -> imported by default, with an optional `channel=`
   override for a caller that genuinely knows better. consolidate.py's own fusion
   output (landed by another agent this session) is deliberately left unsigned this
   round: out of the task's three named buckets, and safe by the grandfather rule
   either way (documented as a known follow-up in docs/poisoning-provenance-2026-07-17.md).
5. Key: `~/.memsub/provenance.key`, 32 random bytes, 0600, created on first write
   (`load_or_create_key`), overridable via `MEMSUB_PROVENANCE_KEY_PATH` for tests.
   Read/consolidation paths use `load_key` (never creates), so a missing key
   fails safe: every claimed-signed row becomes unverifiable -> quarantined, not
   silently trusted.

### What was built

- `memsub/provenance.py` (new): channel constants, key load/create, HMAC-SHA256
  sign/verify over (content, channel, ts, namespace) with a `\x1f`-separated
  message (avoids field-concatenation ambiguity), `tag_episode`, `channel_status`.
- `memsub/consolidate.py`: new phase 0 (quarantine, verifies every active episode's
  signature once per namespace pass, before dedup); `_trust_rank` replaces the
  `_is_inferred`-only swap in both the dedup and contradiction phases; `_is_inferred`
  itself kept unchanged (still the legacy fallback signal, still monkeypatchable by
  scripts/poisoning_benchmark.py and scripts/verify_sleep_cleanrecall.py exactly as
  before). `Action.action` gains "quarantine"; the final apply loop maps action ->
  event via a small dict instead of a two-way ternary.
- `memsub/store.py`: `TERMINAL_EVENTS` gains `"quarantined"`. Found and fixed a real
  latent bug while doing this: `status()`/`active()` had `event IN (?, ?)` hardcoded
  to 2 placeholders instead of `len(TERMINAL_EVENTS)`, which would have broken the
  moment a third terminal event was ever added, regardless of what it was named
  (caught immediately by the full suite, not by design foresight).
- Write-path wiring: `memsub/cli.py` (extract), `memsub/wiki.py` (ingest_wiki),
  `memsub/sleep.py` (sleep_store), `memsub/vault.py` (apply_vault correction,
  promote_notes), `memsub/sdk.py` (MemoryClient.add) -- see assumption 4.
- `memsub/audit.py`: docstring only (event vocabulary now lists "quarantined");
  zero code changes needed for the quarantine event to show up in `memsub audit`,
  since `render_audit` already prints every provenance key and every event generically.
- `tests/test_provenance.py` (new, 12 tests): key create/load/perms, sign/verify
  roundtrip, unsigned-legacy status, forged-channel-claim-in-content-text does not
  gain trust, tampered-content quarantined, tampered-channel-field quarantined,
  key-absence fails safe (both for a signed row and confirms an unsigned-legacy row
  is unaffected), plus three consolidate()-level integration tests: quarantine
  excluded from active()+flagged in the event log+never deleted, verified
  user_stated beats verified llm_inferred despite being older, and unsigned-legacy
  cannot outrank signed user_stated despite being newer (the grandfather guarantee).

Full suite: `pytest` 300 passed (288 existing + 12 new), zero existing test files
modified. No em dash character in any new or touched file (checked with grep for the
UTF-8 em dash byte sequence across every file this session touched).

Commit: HMAC channel provenance (memsub/provenance.py, consolidate/store/cli/wiki/
sleep/vault/sdk wiring, tests/test_provenance.py), local only, not pushed.

### Item 5: re-ran the poisoning benchmark against the new defense

Extended `scripts/poisoning_benchmark.py` minimally: added `--provenance` CLI flag
(default behavior/`main()` untouched, still reproduces docs/poisoning-benchmark.md
unchanged if invoked without the flag), `poisoned_dataset_provenance` (tags the
legit facts `user_stated` and the adversarial episode `llm_inferred` (Attack A) or
`imported` (Attack B), per the task's threat framing: the channel an attacker who
compromised extraction, not a real user turn, can actually reach), and
`run_cell_provenance` (plain `consolidate()`, no `_is_inferred` monkeypatching --
the channel-trust ranking is now the shipping default, so this needed zero new
consolidate.py logic). Old-defense rows in the new report reuse `run_cell()`
completely unchanged, so they are the identical computation as the original round,
not a re-derivation.

Ran `.venv/bin/python scripts/poisoning_benchmark.py --provenance` (wall 0.8s).
Streamed to `results/poisoning-provenance-2026-07-17.json` (24 rows: 2 attacks x 3
(defense, system) combinations x 4 budgets), rewriting the full accumulated list
after each completed cell so a crash mid-run cannot lose prior results.

Result (identical across all 4 budgets, matches the pre-registered expectation
exactly): Attack A unchanged (0% ASR, 98.0% clean recall, old and new defense both).
**Attack B: ASR 98.7% -> 0.0%, clean recall 0.0% -> 98.0%** under the new
channel-provenance defense -- the exact gap the original round's Attack B row
existed to surface is closed for this attack shape, because content dressing
(kind="fact" vs kind="inference") no longer decides trust; the cryptographically
verified write channel does, and neither attack could reach `user_stated`.

Mapped both attacks onto MemPoison's (arXiv:2607.14651) L1/L2/L3 tiers: used
WebSearch + WebFetch this session to pull the paper's actual tier definitions
(confirmed real and quoted verbatim in the doc, not guessed), then applied a
self-defined mapping (both attacks are single-record, non-fragmented,
non-dormant -> L1 by MemPoison's own record-cardinality criterion) since this
project has no access to MemPoison's released test-case labels. Flagged the one
mapping caveat plainly: MemPoison's L1 text bundles in "detectable at write time,"
but Attack B is specifically built to defeat write-time content detectability
while remaining structurally a single record. L2 (compositional) and L3
(dormant-trigger) are explicitly not tested by this round.

Wrote `docs/poisoning-provenance-2026-07-17.md`: old-vs-new table, the threat
model (what channels an attacker can/cannot forge, stated plainly that a
compromised extractor can still poison the llm_inferred channel and a compromised
import path can still poison the imported channel -- channel signing prevents
either from impersonating user_stated, it does not make either channel immune),
the MemPoison tier mapping, and updated claim language in the same measured style
as the original doc (explicitly: not "solved poisoning," scoped to what was
actually measured, L2/L3 stated as untested).

Full suite: `pytest` still 300 passed (this task added only a script extension and
two doc/results files, no library code changed). No em dash character in any new
or touched file.

Commit: poisoning benchmark re-run against channel provenance (scripts/
poisoning_benchmark.py, docs/poisoning-provenance-2026-07-17.md,
results/poisoning-provenance-2026-07-17.json), local only, not pushed.

---

## LOCOMO holdout: LLM-judge (J-score) run (2026-07-17)

Executed Part A's first fix from docs/improvement-plan-2026-07-17.md and closed bead vtr:
ran the LOCOMO holdout slice (n=80, load_locomo(...)[3:], same slice behind the
documented 0.152 F1 headline) through a Sonnet-CLI LLM judge (memsub/judge.py,
same precedent as scripts/judge_capture_holdout.py), four conditions (no_memory,
naive_topk, memsub_hybrid_v2, memsub_decompose -- the last adds today's opt-in
query-time decomposition, docs/multihop-improvements-2026-07-17.md). Local
qwen3:8b answerer throughout; judge blind to condition (sees only
question/gold/answer).

One run, wall time 7331.3s (~122 min), not truncated. Pre-run smoke test
(n=2 x 4 conditions) confirmed pacing before committing to the full run;
full-slice estimate (~1.7h from the smoke-test rate) stayed under the
2.5h threshold so no seed-2026 subset was needed -- ran the full n=80 slice.

Overall judge accuracy: no_memory 2.53% (n=79, 1 malformed judge response
excluded per memsub/judge.py's never-guess policy), naive_topk 37.50%,
memsub_hybrid_v2 47.50%, memsub_decompose 48.75%. Multi-hop row: no_memory
0.0%, naive_topk 55.0%, memsub_hybrid_v2 50.0%, memsub_decompose 55.0%
(n=20/category, one question is a 5-point swing -- directional, not precise).

Full methodology, deviations from the official LOCOMO J-score protocol (local
answerer vs competitors' frontier answerers, judge model family, slice size),
and the competitor comparison table: docs/locomo-jscore-2026-07-17.md. Raw
per-question records streamed incrementally to
results/locomo-jscore-2026-07-17.jsonl (resumable, one line per
condition/question). Script: scripts/locomo_jscore_holdout.py. Added a short
pointer in docs/benchmark-external.md to the completed doc. Bead
memory-sublayer-vtr closed.

pytest: 300 passed, unchanged (this task added a script and two docs, no
library code touched).

Commit: LOCOMO holdout J-score run (scripts/locomo_jscore_holdout.py,
docs/locomo-jscore-2026-07-17.md, results/locomo-jscore-2026-07-17.jsonl,
docs/benchmark-external.md update), local only, not pushed.

---

## Vault serve: browser viewer for the vault and markdown docs (2026-07-18)

Rustom has no Obsidian on his Mac and works over SSH into this box, so
`~/memsub-vault` (4334 notes) was unreadable except as raw markdown files.
Added `memsub vault serve --vault DIR --port PORT` (new memsub/vault_serve.py,
wired into cli.py's vault subcommand group).

.venv has no markdown/mistune/yaml/web-framework library installed (checked
before writing anything), so this is stdlib-only: `http.server` +
`socketserver.ThreadingTCPServer`, a small line-based markdown-to-HTML
renderer, and a permissive `key: "value"` frontmatter parser (not full YAML,
matches exactly what memsub/vault.py's own `_frontmatter` writes). Read-only,
binds 127.0.0.1 only, zero write endpoints.

Features: index page (notes grouped by folder, server-side substring search
over filenames+content, capped at 200 results); note pages with
`[[wikilink]]` resolved to a real link or a visibly dead-link style (reddish,
non-clickable) when the target note does not exist; frontmatter rendered as a
small metadata block instead of raw YAML; ```` ```mermaid ```` code fences
rendered as real diagrams via the mermaid CDN script (only loaded on pages
that have one); dark, minimal, phone-friendly inline CSS. `--root` flag
serves any markdown folder through the identical viewer (e.g. a project's
docs/), documented as the same code path as vault mode.

Path-traversal safety: every `/note/<path>` request is resolved against the
served root and rejected with 404 if the resolved path is not under the
root (`../`, absolute paths, and URL-encoded variants all covered).

Tests: tests/test_vault_serve.py, 9 new tests -- wikilink-to-existing-note
rendering, dead-link styling, frontmatter-as-metadata-block, mermaid block
rendering (with html-escaped content and the CDN script present), search by
content and by filename, path-traversal rejection (`../`, absolute,
URL-encoded, nested), missing-file rejection, and an end-to-end HTTP smoke
test (real ThreadingTCPServer on an ephemeral port, GET / and GET
/note/<path> and a traversal attempt, then shutdown -- never leaves a server
running).

Smoke test against the real vault: started `memsub vault serve --vault
~/memsub-vault --port 8766`, curled `/` (200, 714KB index of all 4334
notes), curled a real note under claude-code/ (200, frontmatter rendered as
a metadata block), curled `/?q=rustom` (200, search results), curled a
traversal attempt (404), then killed the server and confirmed port 8766 was
free again.

Stayed out of memsub/store.py and memsub/consolidate.py entirely per
instruction (another agent is touching those for a graph layer). No
dependency added; no em dash in any new or touched file.

Full suite: `pytest` now 309 passed (was 300; +9 for vault_serve, no
existing test touched or broken).

Commit: vault serve viewer (memsub/vault_serve.py, memsub/cli.py,
tests/test_vault_serve.py, docs/vault-serve-2026-07-18.md), local only, not
pushed.

---

## Graph layer: bipartite entity-episode index (2026-07-18)

Ordered directly by Rustom ("Do the graph layer" / "Add the graph layer, learn
from mem0 and open source things, and yes u can use a better model if need be
(dont use api, use from my subscription)"). Design source:
memory-sublayer-research/oss-source-dive/report-2026-07-18.md section 4.

### Assumptions (stated before coding, Karpathy rule 1)

1. **Entity id is namespace-scoped**: `f"{namespace}::{normalized_text}"`, not
   bare normalized text as the report's schema sketch literally shows. Bare
   normalized-text ids would collide two different namespaces' "Alice" into
   one row, breaking the multi-tenant namespace isolation the rest of memsub
   enforces (memsub/namespaces.py) and the task's own "namespace-aware
   backfill" requirement. The `namespace` column stays as documented; the id
   just also carries it. Documented here and in docs/graph-layer-2026-07-18.md
   as the one deliberate deviation from the report's literal DDL.
2. **Entity type is rule-based, two values**: PERSON (text that was the
   "Speaker: ..." prefix of its source episode) else TOPIC. No NER, no LLM,
   zero cost, matches mem0's "proper nouns / heuristics" precedent, not a
   full mem0-style type taxonomy (no PLACE/EVENT/etc: nothing in memsub's
   corpus format lets rules tell those apart from TOPIC reliably, and a wrong
   guess costs nothing downstream since type is not used for retrieval
   ranking, only stored for future use).
3. **Near-miss threshold**: `difflib.SequenceMatcher.ratio() >= 0.82` against
   an exact non-match. Stdlib-only per the report's explicit "no MinHash lib
   needed" call. 0.82 is a starting point (catches "Melanie"/"Melaine" at
   0.857, "the lisbon trip"/"lisbon trip" at 0.846; "Melanie"/"Melany" was
   tried and measured below threshold at 0.769, so it stays a clean miss,
   not ambiguous), tested directly in step 5, not tuned against a labeled
   set (none exists) -- flagged as a knob, not a claim.
4. **Resolution cache is per-run, in-memory**, scoped to one
   `EntityResolver` instance (one `graph_store()` call = one backfill run or
   one `consolidate(..., enable_graph=True)` call). Satisfies "no pair asks
   twice" within a run. Does NOT persist across separate CLI invocations
   (no third SQLite table added for it -- the report's schema is explicitly
   two tables; a persistent cross-run cache would be a third). Each ambiguous
   pair is bounded to at most one LLM call per run regardless of run count,
   which is the cost property that matters; documented as a scope limit, not
   silently glossed over.
5. **One function, two call sites**: `memsub.graph.graph_store()` is the
   single write-path implementation (rule-based extraction + dedup funnel +
   upsert), reused unchanged by (a) the `memsub graph backfill` CLI command
   and (b) an opt-in `enable_graph=False` parameter on
   `memsub.consolidate.consolidate()`. Idempotent via a `"graphed"` event per
   episode, exactly mirroring `memsub.sleep.sleep_store`'s `"slept"` event
   pattern -- reuses an established idiom instead of inventing a new one.
6. **Traversal**: 1-hop default = query's rule-based entities -> matching
   `entities` rows (exact id match, then difflib fuzzy, no LLM) -> co-mentioned
   episodes via `entity_links`. 2-hop (explicit `hops=2`) extends one more
   entity-episode pair: those episodes' other entities -> their episodes. SQL
   joins carry no relevance score, so the graph list is ordered by episode
   recency (ts desc) before being handed to `rrf_fuse` as one more ranked
   list, same shape as every other signal in `HybridRetriever.rank`.
7. **Backfill test never touches the live store**: copies
   `~/.memsub/memory.db` to a temp path first (`shutil.copy2`), runs backfill
   against the copy only.
8. **Eval naming**: today's runs get their own dated result files
   (`results/graph-layer-eval-2026-07-18.jsonl`,
   `results/locomo-jscore-2026-07-18.jsonl`) rather than appending into
   yesterday's files, so each dated doc's raw records stay self-contained.

### Success criteria (Karpathy rule 4, stated before coding)

- Full test suite green, starting count 309, ending count >= 309 + new tests
  added this session; zero existing tests broken or modified.
- New tests (step 5) cover: additive migration on a store built before the
  graph tables existed; extraction determinism (same input -> same entity
  set); dedup funnel's three paths (clean exact match makes zero LLM calls,
  clean miss with no near neighbor makes zero LLM calls, a stubbed-ambiguous
  pair makes exactly one LLM call and is cached on a repeat); 1-hop vs 2-hop
  traversal boundedness (2-hop reaches a strictly larger or equal episode set
  than 1-hop, never open-ended); RRF fold actually changes ranking when the
  graph signal disagrees with base rank; and a regression test asserting
  `rank()`/`search()` output is byte-identical whether or not
  `rank_graph`/`graph_store` have ever been called against the same store.
- Backfill executed once against a temp copy of the real
  `~/.memsub/memory.db`, entity count / link count / wall time recorded.
- Answer-quality eval (step 6a): 3 conditions (baseline, graph, graph+decompose)
  on the n=74 multi-hop dev slice, qwen3:8b, budget 1000, streamed
  incrementally to `results/graph-layer-eval-2026-07-18.jsonl`.
- J-score eval (step 6b): best config from 6a vs shipped baseline on the n=80
  LOCOMO holdout slice, blind Sonnet-CLI judge, local qwen3:8b answerer,
  appended incrementally to `results/locomo-jscore-2026-07-18.jsonl`.
- `docs/graph-layer-2026-07-18.md` written: design credit to the report,
  backfill stats, both eval tables, an honest verdict (ship or don't) either
  way -- no result gets spun.
- Local commits at each checkpoint (schema+backfill, write path, query path,
  composition, tests, eval+docs); never `git push`; `memsub/vault_serve.py`
  untouched.

### Answer-quality eval result (2026-07-18, honest)

`scripts/graph_layer_eval.py`, n=74 multi-hop dev slice, qwen3:8b, budget
1000: baseline mean F1 0.1964, graph 0.1828 (below baseline), graph_decompose
0.1901 (still below baseline's F1, but the best of the two graph configs,
and the only one to beat baseline's exact-substring rate: 0.0811 vs 0.0676).
Neither graph condition beats the shipped baseline on mean token-F1 on this
slice. graph_decompose carried forward to the J-score holdout run as "the
best new config" per the task's step 6 instruction, not because it won --
because it is the best of the two graph variants measured. Full table and
verdict: docs/graph-layer-2026-07-18.md.

### J-score holdout result (2026-07-18)

`scripts/locomo_jscore_graph_2026-07-18.py`, n=80 LOCOMO holdout slice
(conversations 4-10, seed 123), blind Sonnet-CLI judge, qwen3:8b local
answerer. One run, wall 4006.5s (~67 min), not truncated. Result:
memsub_hybrid_v2 (shipped baseline) 48.10% accuracy (n_ok=79/80, one
malformed judge response excluded), graph_decompose 51.25% accuracy
(n_ok=80/80) -- graph_decompose wins by +3.15pp absolute (+6.5% relative),
multi-hop row specifically 47.37% -> 50.00%.

This flips sign from the answer-quality (token-F1) eval above, where
graph_decompose was 3.2% below baseline. Both numbers are real, from
executed runs, streamed incrementally; the disagreement is reported as a
metric-disagreement finding in docs/graph-layer-2026-07-18.md, not resolved
by picking the flattering one. Recommendation: ship opt-in (already the
case -- enable_graph/rank_graph/etc. are off by default), do not flip the
shipped rank()/search() default on this evidence; the two small single runs
(n=74, n=80) with a sign disagreement between metrics argue for more
measurement before a default-path change, not for one now.

Full suite after this session's work: 328 passed (was 309 at session start,
+19 new tests, zero existing tests touched or broken).

Commits (all local, never pushed): schema+backfill+write-path+query-path+
composition (memsub/store.py, memsub/graph.py, memsub/hybrid.py,
memsub/consolidate.py, memsub/cli.py); tests (tests/test_graph.py,
tests/test_graph_cli.py); answer-quality eval (scripts/graph_layer_eval.py,
results/graph-layer-eval-2026-07-18.jsonl); this final commit (J-score
script+results, docs/graph-layer-2026-07-18.md, this WORKLOG entry).
memsub/vault_serve.py untouched throughout.

---

## Frontier-answerer J-score run (2026-07-18)

Ordered by Rustom: "i need to beat current top locomo score also" /
"yes u can use a better model if need be (dont use api, use from my
subscription)". Puts memsub on the same measurement axis as the competitor
numbers in docs/improvement-plan-2026-07-17.md Part A by swapping the
ANSWERER from local qwen3:8b to Sonnet via the `claude` CLI (subscription,
no API key). Retrieval/decomposition stays local (qwen3:8b) so only the
answerer variable changes. Script: scripts/locomo_jscore_frontier_2026-07-18.py,
new file, reuses scripts/locomo_jscore_holdout.py's slice/sampling and
scripts/locomo_jscore_graph_2026-07-18.py's graph_decompose system-building
unchanged. Same n=80 LOCOMO holdout slice (conversations 4-10, seed 123) as
both prior J-score runs. Three conditions, priority order, each run to
completion and committed before the next: graph_decompose (memsub's best
config), naive_topk (control), no_memory (floor). Results:
results/locomo-jscore-frontier-2026-07-18.jsonl, streamed, resumable.

### Sanity check: first 3 answers (graph_decompose), by eye before the full run

1. Q: "What kind of beer does McGee's bar serve?" GOLD: "Stout, lager".
   ANSWER: "McGee's bar serves stout, lager, and light beers." Judge:
   correct=True. Reasonable -- gold facts are a subset of the answer, judge
   correctly credits the paraphrase/superset.
2. Q: "How many Prius has Evan owned?" GOLD: "two". ANSWER: "Evan has owned
   one Prius, the new one that broke down after the mountain drive." Judge:
   correct=False. Reasonable -- Sonnet is answering fluently and on-topic
   from the retrieved context, but the retrieved context didn't surface
   enough to get the count right; judge correctly marks it wrong rather than
   crediting topic relevance.
3. Q: "What places has Joanna submitted her work to?" GOLD: "film contest,
   film festival." ANSWER: "I don't know -- the context doesn't specify
   which places or publications Joanna submitted her work to." Judge:
   correct=False. Reasonable -- Sonnet is correctly declining to guess when
   the retrieved context doesn't contain the fact (ANSWER_SYSTEM's
   instruction working as intended), judge correctly marks a non-answer
   wrong rather than crediting the honesty.

All three: answer format matches the local-answerer protocol exactly (one
short sentence, CONTEXT-grounded), judge verdicts track gold vs. answer
correctly in both directions (credit a superset paraphrase, penalize a wrong
number and a non-answer). Pipeline confirmed sane; proceeding to the full
n=80 run per condition. ~44s/question observed for graph_decompose (answer +
judge, two Sonnet CLI calls plus one local decompose call) -- slower than
the local-answerer runs (qwen3:8b answer calls were near-instant vs.
Sonnet's CLI round-trip), budgeted accordingly with a 3h per-condition wall
cap and exponential-backoff retry on CLI failure.

### Full run result (all 3 conditions complete, 2026-07-18)

All three conditions run to completion, each committed independently before
the next started, zero retries needed (no CLI rate-limit hits). Full suite
re-checked green (328 passed, unchanged) after all three runs.

| condition | n_ok | accuracy | wall time |
|---|---|---|---|
| graph_decompose | 80/80 | 43.75% | 3108s (~52 min) |
| naive_topk | 80/80 | 27.50% | 2858s (~48 min) |
| no_memory | 77/80 (3 malformed) | 3.90% | 2975s (~50 min) |

**Headline, stated plainly**: memsub + Sonnet does not beat mem0's
2025-paper reproduced tier (~61.7%) or Zep's revised number (75.14%).
**The more informative result**: memsub + Sonnet (graph_decompose, 43.75%)
scored *lower* than memsub + qwen3:8b on the identical condition and slice
measured the day before (51.25%, docs/graph-layer-2026-07-18.md) -- a 7.5pp
drop in the direction opposite what swapping in a frontier answerer was
expected to do. Category-level, the drop concentrates in temporal (-10pp)
and single-hop (-10pp), not multi-hop (-5pp) -- categories where a
confident guess is cheap to get right by chance, consistent with a
working hypothesis (stated as a hypothesis, not proven) that Sonnet follows
ANSWER_SYSTEM's "say you don't know" instruction more faithfully than
qwen3:8b does, and an LLM judge gives no credit for a calibrated refusal
over a lucky guess. memsub's own relative contribution (graph_decompose vs.
naive_topk, answerer held fixed) held up and even grew slightly with the
frontier answerer (+16.25pp vs +13.75pp locally) -- the answerer-side effect
hits all three conditions, not memsub's retrieval specifically. Per-category
against mem0's reproduced number (the closest apples-to-apples row):
memsub+Sonnet edges mem0 on single-hop (70.0% vs 67.1%) and is within
single-run noise on multi-hop (45.0% vs 51.2%); temporal (-30.5pp) and
open-domain (-37.9pp) carry the real gap. Full analysis, tables, and the
honest deviations list: docs/locomo-jscore-frontier-2026-07-18.md.

Commits (all local, never pushed): script + sanity-check (d47ab4c);
graph_decompose results (421492f); naive_topk results (feeebe9); no_memory
results (1d671a7); this WORKLOG entry + final report doc (next commit).

## 2026-07-18: Beat-the-score round 2 -- refusal diagnosis + best-effort prompt + temporal surfacing

Continuation of "i need to beat current top locomo score also". Assumptions
and success criteria stated before coding, per Karpathy rule 1:

- **Assumption**: refusal-style answers ("I don't know" etc.) are scored
  incorrect by the blind judge with ~0% credit, same as a wrong guess, so
  they can only ever hurt accuracy relative to a guess with nonzero hit
  probability -- this is checkable directly from the existing JSONL
  (`correct` field conditioned on refusal-pattern match), no new LLM calls
  needed.
- **Success criteria**: (1) refusal counts per condition/category reported
  as a table, verdict stated as "confirmed / partially confirmed / not
  confirmed" with the quantitative split between refusal-count effect vs.
  non-refusal answer-quality effect: (2) 10 temporal failures read by eye,
  each labeled retrieval-miss or surfacing-gap or reasoning-gap; (3) if
  surfacing-gap is real, a small opt-in flag change (not a library default
  change) plus a temporal-only re-run with n=20 in both flag states; (4)
  graph_decompose Sonnet re-run with a best-effort prompt on the full n=80
  slice, same judge, resumable streaming to a v2 file.

### Task 1: refusal-pattern count (no new runs)

Regex over 13 refusal phrasings ("not mention", "don't know"/"do not know",
"cannot/can't determine", "no information", "not specified/provided/
stated/given/clear/available", "unable to determine/find/answer", "context
does not contain/specify/mention/provide", "context is empty/doesn't",
"unclear", "unknown", "i'm not sure"), case-insensitive, applied to the
`answer` field of every `judge_status: "ok"` record in both
`results/locomo-jscore-frontier-2026-07-18.jsonl` (Sonnet) and
`results/locomo-jscore-2026-07-17.jsonl` + `results/locomo-jscore-2026-07-18.jsonl`
(qwen3:8b, all conditions across both files).

**Refusal rate by condition:**

| condition | answerer | n | refusals | refusal rate |
|---|---|---|---|---|
| no_memory | Sonnet | 77 | 76 | 98.7% |
| no_memory | qwen3:8b (07-17) | 79 | 69 | 87.3% |
| naive_topk | Sonnet | 80 | 38 | 47.5% |
| naive_topk | qwen3:8b (07-17) | 80 | 31 | 38.8% |
| graph_decompose | Sonnet | 80 | 28 | 35.0% |
| graph_decompose | qwen3:8b (07-18) | 80 | 24 | 30.0% |

Direction holds in all three conditions: Sonnet refuses more often than
qwen3:8b on identical retrieval, every time. Refusal-hypothesis direction
**confirmed**.

**Does refusal count fully explain the accuracy gap? Quantitative check on
graph_decompose (the condition that matters for the headline number):**

Across every refusal in graph_decompose for both models, `refusal_correct`
summed to 0 (0/28 Sonnet, 0/24 qwen3:8b) -- refusals are never credited by
the judge, confirming the zero-credit assumption exactly. So:

- Sonnet: 80 - 28 = 52 non-refusal answers, 35 correct total, all 35 from
  the non-refusal set -> non-refusal hit rate 35/52 = **67.3%**.
- qwen3:8b: 80 - 24 = 56 non-refusal answers, 41 correct total, all 41 from
  the non-refusal set -> non-refusal hit rate 41/56 = **73.2%**.

Total gap: 41 - 35 = 6 questions (51.25% - 43.75% = 7.5pp). Refusal-count
delta is only 4 (28 vs 24 = 5pp of n=80). Counterfactual: if Sonnet's 4
extra refusals had instead been answered at Sonnet's own non-refusal hit
rate (67.3%), expected +2.7 correct -> ~37.7/80 = 47.1%, narrowing the gap
from 7.5pp to ~4.2pp. **Refusal hypothesis is PARTIALLY confirmed: it
explains roughly 40-45% of the graph_decompose accuracy gap (about 3.3pp of
7.5pp). The remaining ~55-60% (about 4.2pp) is a genuine non-refusal
answer-quality gap** -- when Sonnet does commit to an answer, it is right
less often than qwen3:8b is when qwen commits (67.3% vs 73.2%), not
explained by calibration/guessing behavior at all. Do not oversell the
refusal story as the whole explanation; it is a real but partial driver.

### Temporal eye-read: 10 graph_decompose Sonnet temporal failures

Reconstructed the exact retrieved context deterministically (same retrieval
+ decompose code path as the real run, `_build_system`/`_context_for` from
`scripts/locomo_jscore_frontier_2026-07-18.py`, no new answer/judge LLM
calls) for the first 10 of 15 temporal `graph_decompose` questions Sonnet
got wrong. Read each context by eye against question/gold/answer:

1. "When was Deborah in Bali?" gold "in 2022". Context: "Bali **last year**
   was one of my favs." -- relative date present, no session date to
   resolve it. Answer: "I don't know." **Surfacing gap.**
2. "When did Jolene lose a lot of progress in her work?" gold "last week of
   July 2023". Context contains "progress" but not the specific loss event
   or any date phrase in the 3922-char packed context. **Retrieval miss.**
3. "When did Audrey's ... training course ... take place?" gold "June,
   2023". Context contains "training" (positive reinforcement techniques)
   but not the specific course-date turn. **Retrieval miss.**
4. "When did Nate take his turtles to the beach?" gold "10 November, 2022".
   Context: "I took my turtles to the beach in Tampa **yesterday**!" --
   relative date present and Sonnet actually used it ("the day before this
   conversation snippet") but had no absolute date to convert it to; judged
   incorrect against the absolute gold. **Surfacing gap**, and the clearest
   example that Sonnet reasons over what's given when it does answer.
5. "When did Nate win his fourth video game tournament?" gold "The Friday
   before 10 July, 2022". Context: "I won my fourth video game tournament
   **on Friday**!" -- relative date present, but Sonnet answered "I don't
   know" without even surfacing "Friday". **Surfacing gap + a reasoning
   miss on top** (didn't use the partial info it had).
6. "When will John start his new job?" gold "In July, 2022". Context: "I'm
   starting **next month**." Sonnet answered "John will start next month,"
   directly lifting the relative phrase but never resolving it to an
   absolute month -- judged incorrect against "July, 2022". **Surfacing
   gap** (this is arguably the single cleanest example: the answer would
   have been correct with a session-date anchor).
7. "Where was James at on July 12, 2022?" gold "Toronto, Canada". No
   "Toronto"/"Canada" anywhere in the packed context. **Retrieval miss**
   (compounded by needing session-date-aware retrieval to find the right
   session in the first place -- a harder, joint retrieval+temporal
   problem, out of scope for the Task 3 flag).
8. "When was Calvin's concert in Tokyo?" gold "last week of May 2023".
   Context mentions the Tokyo concert three times but with no date phrase
   attached to any of the three turns. **Retrieval miss** (the date-bearing
   turn, if one exists, wasn't packed).
9. "Which book did Jolene read in January 2023?" gold "Avalanche by Neal
   Stephenson". Context: "**Two weeks ago** I read 'Avalanche' by Neal
   Stephenson in one sitting!" -- the exact book and author are retrieved
   correctly, relative date present, but Sonnet answered "I don't know"
   rather than commit to an unconfirmed date-scoped claim. **Surfacing
   gap**, textbook case: everything needed is present except the session
   date to confirm "January 2023."
10. "When did Evan announce his marriage to his extended family?" gold
    "January 5, 2024". Context: "we told our extended fam about our
    marriage **yesterday**." Relative date present, absolute date missing.
    **Surfacing gap.**

**Verdict: 6/10 are clear surfacing gaps** (relative date phrase retrieved
correctly, no session-date anchor available to resolve it to the gold's
absolute format) **and 4/10 are retrieval misses** (the date-relevant
evidence turn was not in the packed context at all, a separate and harder
problem out of scope for Task 3's surfacing flag). This is a strong,
reproducible signal that session-date surfacing is real and worth fixing:
proceeding with Task 3 (opt-in context-formatter flag), per the task's
instruction to build it "if Task 1 showed dates are missing from context."
The retrieval-miss cases are logged here but not addressed by Task 3 --
flagged as a distinct follow-up (better temporal-aware retrieval, not
covered by this round).

Diagnostic scripts used (not committed, /tmp scratch, reproducible from the
commands above and the repo's own `_build_system`/`_context_for`): refusal
counter and context reconstruction. No new LLM answer/judge calls were made
for Task 1 -- purely offline analysis of existing JSONL plus deterministic
local retrieval (one local qwen3:8b decompose call per reconstructed
context, not an answer or judge call).

### Task 2: best-effort prompt, graph_decompose v2 re-run

Added `ANSWER_SYSTEM_BEST_EFFORT` to `scripts/locomo_jscore_frontier_2026-07-18.py`
(opt-in via `--prompt best_effort`, local to this script; `metrics_local.
ANSWER_SYSTEM` untouched, local-answerer numbers stand as history).
Re-ran `graph_decompose`, Sonnet, n=80, same blind judge, streamed to a new
file (never mixed with v1): `results/locomo-jscore-frontier-v2-2026-07-18.jsonl`.
Wall time 3183s (~53 min), zero retries, zero malformed/error records.

**Result: 52.5% overall (was 43.75%), +8.75pp.** Per category: multi-hop
45.0% (unchanged), temporal 30.0% (+5.0pp), open-domain 50.0% (+15.0pp),
single-hop 85.0% (+15.0pp). Refusal rate on graph_decompose dropped from
35.0% to 5.0%. **memsub + Sonnet v2 now beats memsub + qwen3:8b on the
identical condition and slice (52.5% vs 51.25%)** -- round 1's
counter-intuitive local-beats-frontier result is reversed by the prompt fix
alone, no retrieval change. Full suite re-checked green (328) before and
after the code change.

### Task 3: temporal session-date surfacing flag, n=20 with vs. without

Added `--with-dates` to the same script (opt-in, eval-path only, no
`memsub` library default changed): `_context_for` now prefixes each packed
episode with `[session date]` from `ep.provenance['session_date']` when
present, via a new `_format_episode` helper. Confirmed working with a
1-question check before the full run (context showed `[11 November 2022]
Joanna: ...` prefixes as expected). Checked `memsub.bench.LocomoQA` for a
question-asked-date field per the task's "include the question date if the
protocol provides one" -- none exists (only conversation_id/question/
answer/evidence/category), so that part is not applicable, noted not
implemented.

Re-ran temporal category only (n=20, `--category 2`), graph_decompose,
Sonnet, Task 2's best-effort prompt, same blind judge, with `--with-dates`:
`results/locomo-jscore-frontier-v2-temporal-dated-2026-07-18.jsonl`. Wall
time 779s (~13 min), zero retries/errors.

**Result: 45.0% accuracy with the date flag, vs. 30.0% for the same n=20
temporal slice without it (from Task 2's v2 base run) -- a +15.0pp gain
from surfacing session dates alone.** This is a direct, measured
quantitative confirmation of Task 1's eye-read hypothesis (6/10 temporal
failures were surfacing gaps), not just a directional one. The flag was
only exercised on the temporal category in isolation this round (per task
scope) -- not a library default, not re-run across the other 3 categories.
Full suite re-checked green (328) before and after.

### Round 2 headline, stated plainly

memsub + Sonnet went from 43.75% (round 1, conservative prompt) to 52.5%
(round 2, best-effort prompt, n=80) with zero retrieval changes, closing
about half the gap to mem0's reproduced tier (-17.95pp -> -9.2pp). Adding
temporal date surfacing (measured on temporal alone, n=20) closes a further
15.0pp on that specific category. **Still short of mem0 (~61.7%) and Zep
(75.14%) overall.** Single-hop now clearly beats mem0's reproduced number
(+17.9pp, was +2.9pp); multi-hop is completely unchanged by both fixes
(45.0% in both v1 and v2, still -6.2pp behind mem0) -- the one category
neither intervention touched. Full tables and honest verdict:
`docs/locomo-frontier-v2-2026-07-18.md`.

Commits (all local, never pushed): Task 1 WORKLOG entry (914f8f4); Task 2
script + v2 results (70fde43); Task 3 temporal-dated results (d8d147d);
this final WORKLOG entry + report doc (next commit).
