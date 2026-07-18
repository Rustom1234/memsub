
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
   needed" call. 0.82 is a starting point (catches "Melanie"/"Melany",
   "Lisbon trip"/"the Lisbon trip"), tested directly in step 5, not tuned
   against a labeled set (none exists) -- flagged as a knob, not a claim.
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

---
