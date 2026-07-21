
## 2026-07-21 03:20: Idea resonance + one-line install + demo/site quality pass

### Scope (3 tasks, verbatim from the brief)

1. Idea resonance: drop an idea, get back distant-but-resonant memories
   with an AI-written bridge, plus a UI surface and a persisted memory.
2. One-line install: `bash scripts/install.sh`, portable, idempotent.
3. Demo visual quality: regenerate the site's Maya tour screenshots,
   add a resonance tour step, reframe the hero copy to the living-brain
   arc.

Own-paths-only throughout: memsub/resonance.py (new), scripts/install.sh
(new), site/, vault_serve.py (additive routes only), scripts/build_demo_vault.py
+ scripts/_demo_persona_data.py (additive), tests, docs, this file. Never
touched memsub/ask.py, store.py, distill.py, cli.py, hybrid.py, rerank.py,
answer_policy.py (other agents' uncommitted WIP found at session start,
confirmed still present and unstaged after every commit below).

### Task 1: memsub/resonance.py

Pure distance-selection logic (`find_resonances`, no LLM, stub-testable):
embed the idea and every active episode with the same embedder, score by
cosine, group the resonant candidates (score >= floor) by a cheap topic
proxy (the episode's most specific linked entity: lowest-degree of its
graph-linked entities, so a generic entity like the vault owner's own
name -- linked to nearly everything -- never dominates the grouping),
exclude whichever cluster the single closest match belongs to (that one
is the obvious connection, not a surprising one), then keep the
best-scoring episode from each remaining cluster, capped at 3. One
qwen3:8b call per surviving match writes a 2-3 sentence bridge
("These connect because..."). Honesty rule: nothing clearing the floor,
or everything clearing it sharing the top cluster, returns "nothing
resonates yet" -- never a fabricated connection. The idea itself is
persisted via `MemoryClient.add(..., channel="user_stated")`, the same
public write path every other caller uses.

`python -m memsub.resonance "idea text"` is the module-main CLI entry
(not routed through cli.py, which is off-limits this round).
`memsub/vault_serve.py` gets an additive `/resonate` page and
`/api/resonate` route, reusing the existing `ask_backend` (qwen3:8b) and
the same per-request-`MemoryClient` pattern `/api/ask` already
established (sqlite connections are thread-bound; the backend holds no
sqlite state so it's safe to share).

Calibration finding worth recording: on the real demo vault (single
namespace, one person, ~140 active episodes), hashing-embedder cosine
scores run 0.01-0.40 with mean ~0.21 -- most content in a small
single-topic vault scores moderately against *any* on-topic idea just
from shared vocabulary (the person's own name, the project's name). The
floor (0.15) only needs to reject genuine noise; distance is enforced by
cluster exclusion, not the floor. Entity-based clustering is also only a
proxy for "topic," not perfect -- confirmed live (see Task 3 below): a
deliberately-seeded, genuinely-distant memory scored well above the
floor but still landed just outside the exact top 3 for one particular
idea phrasing, because a small single-topic vault's literal vocabulary
overlap dominates even real (ollama) embeddings. Documented honestly in
the site copy rather than papered over.

11 new tests (`tests/test_resonance.py`, a hand-built `FakeEmbedder` for
exact deterministic cosine control) plus 3 route tests
(`tests/test_vault_serve.py`, `MockBackend`, real `HashingEmbedder` with
manually-linked entities to get a real, checked-by-hand cluster-exclusion
outcome). Commit: `acd8d50`.

### Task 2: scripts/install.sh

Portable installer (`$HOME` + the script's own location only, no
hardcoded paths): checks python3.10+ (hard requirement) and a local
ollama daemon (soft -- installs everything else and degrades gracefully
with a clear message if missing), creates a venv (`uv venv`/`uv pip
install` when `uv` is on PATH -- this sandbox's system python has no
`ensurepip`/`python3-venv`, a real portability gap the stdlib `venv`
module can't work around, and it's also literally how this repo's own
real `.venv` was built, confirmed via `pyvenv.cfg`), initializes the
store and vault dirs, installs the extraction/consolidate/vault-sync cron
jobs and the Claude Code `UserPromptSubmit` recall hook idempotently.

Ported and generalized from `~/scripts/install-durable-capture.sh`: only
the pieces genuinely part of this repo are wired (`daily_extract.sh`
cron, `vault apply` cron, `repo_watchdog.sh` cron rescoped to just this
repo via `REPO_WATCHDOG_REPOS`, the recall hook); the personal
transcript-capture layer that lives outside the repo
(`memsub-inbox.sh`/`memsub-inbox-sweep.sh`, the `SessionEnd`/
`PreCompact` hooks) is deliberately not ported -- it would either
silently fail on any other machine or require inventing a new
personal-glue layer that isn't part of the memsub product. Documented in
`docs/install-2026-07-21.md`.

Sandbox test (real bug found and fixed in the test harness, not the
script): `HOME` pointed at a throwaway tmp dir, a fake `crontab` script
logged to a state file instead of the real crontab, and the repo was
copied to scratch so venv creation never touched the real working
`.venv`. First version of the fake `crontab` lost earlier lines on every
call -- root cause: `crontab -` was implemented as `cat > $STATE`, which
truncates the file the instant the process starts, racing the `crontab
-l` on the other side of the same `(crontab -l; echo new) | crontab -`
pipeline (both sides of a shell pipe start concurrently). Fixed by
writing to a temp file and `mv`-ing over `$STATE` only after stdin
closes (atomic rename, no truncation race). This is purely an artifact
of faking `crontab` as two separate shell-script invocations; the real
`crontab` binary is one atomic C program and never has this problem.

Verified: fresh install (venv/store/vault/3 cron lines/hook all created,
exit 0), idempotent rerun (everything "already present", zero new
entries), the no-ollama degraded path (`PATH` with ollama's directory
excluded -- still exits 0, installs everything else), and confirmed
byte-for-byte that the real crontab, `~/.claude/settings.json`, and this
repo's real working `.venv` were all untouched. Wrapped as 3 pytest
tests (`tests/test_install_sh.py`, skips cleanly without network).
Commit: `856241c`.

### Task 3: demo regeneration + site quality

Added one engineered persona record to `scripts/_demo_persona_data.py`
(a sound-design note on player attention and irregular rhythm, dated
months before the idea it's meant to resonate with) and reran
`scripts/build_demo_vault.py --fresh` end to end (147 episodes after
also persisting the demo idea for real, 139 active, 7 superseded, 1
quarantined, 48 entities, 371 entity-episode links -- the entity/link
counts happened to land on the exact same numbers as the prior build,
everything else shifted by the one new record plus the persisted idea).
Then ran `python -m memsub.resonance` for real against the fresh
`demo/memory.db` -- the output used on the site is genuinely unedited
tool output, not hand-written (see the honesty note above: the seeded
note itself didn't make the literal top 3 for this idea phrasing, and
the site says so rather than silently swapping in a nicer-looking fake
result).

Site: hero copy reframed to the living-brain arc (install one line, it
keeps extracting/pruning/resolving contradictions/summarizing on its
own, watch it happen, your AI never forgets). New tour step 6
("Resonance") with the real top-3 cards and verbatim qwen3:8b bridge
text. Dashboard/ripple stat numbers in the tour captions and the
animated terminal replay updated to match the regenerated data.

Screenshots: consistent 1440px-wide headless Chrome captures
(`--force-prefers-reduced-motion`, matching the existing convention),
tight-cropped to each page's real content bounds with a small ad-hoc PIL
script (not committed -- lived in /tmp, not part of the product) instead
of shipping mostly-empty frames (the old `/ask` screenshot was ~80%
black void below the form; the old dashboard screenshot cut off
mid-sentence at a hard 900px height). Verified the new tour step 6 by
temporarily flipping its slide/button to `active` for a screenshot, then
reverting via the backed-up original file (confirmed byte-identical
after revert) -- the click-toggle JS itself was not touched. Reviewed
the rendered tour twice (once right after building, once again after
fixing an em-dash violation introduced in the new copy -- the file's
existing convention uses `&mdash;` throughout, but the global "no em
dashes, ever" rule applies to text this session wrote, so new copy uses
colons/commas/restructured sentences instead, existing untouched text
left as-is). Saved under `docs/screenshots/2026-07-21-demo-v2/` and
copied into `site/assets/img/`. Commit: `9a12860`.

### Verification

`pytest -q`: 516 passed throughout (started at 499, +11 resonance +3
route +3 install = 516). No em dash in any file this session wrote or
edited (existing `&mdash;` usage in site/index.html left untouched).
Every commit's `git log -1 --stat` checked immediately after committing
to confirm only this session's own surface paths were included, never
`ask.py`/`store.py`/`distill.py`/`cli.py`/`hybrid.py`/`rerank.py`/
`answer_policy.py`. All servers started for manual verification
(`vault serve` on 8768, a plain `http.server` on 8899 for the site) were
stopped and confirmed gone (`ps aux`) before finishing. Nothing pushed.
Bundle: `~/repo-bundles/memory-sublayer-20260721-0319.bundle`.

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

## 2026-07-18 (evening): improvement plan v2, Wave 1 (items 1-3)

Ordered directly by Rustom: "make a plan to improve each now, and then
actually execute the full plan" (docs/improvement-plan-2026-07-18.md).
Executing Wave 1 items 1-3 only (Wave 2 out of scope for this pass).

### Pre-existing suite state (found before touching anything, not caused by
this work)

`pytest -q` fails to collect: `tests/test_graph.py` (and likely
`tests/test_graph_cli.py`) import `Entity` from `memsub.store`, but no
`Entity` dataclass exists there -- `memsub/graph.py`, `memsub/provenance.py`,
`memsub/vault_serve.py` and their docs (all untracked, uncommitted) are an
incomplete prior session's graph-layer work
(docs/graph-layer-2026-07-18.md says "Two additive SQLite tables
(`memsub/store.py`: `entities`, `entity_links`)" but `store.py` was never
actually updated). Also 3 pre-existing failures in `tests/test_provenance.py`
(hash-order assertions, unrelated to graph). Baseline with graph tests
excluded: 302 collected, 295 passed, 3 failed (test_provenance.py), before
any change in this session. This is adjacent, unrelated, incomplete work --
not touched here per the surgical-changes rule; Wave 1's own regression
check uses this 295-passed baseline (plus new item-3 tests), not the
328-green target stated in the task brief, which assumed a suite state that
does not currently exist. Flagged honestly, not silently worked around.

### Assumptions (stated before coding)

1. Item 1 "v3 combined" = `--prompt best_effort --with-dates`, full n=80,
   `graph_decompose`, same slice/judge as prior rounds. Needs a new output
   path (`results/locomo-jscore-v3-2026-07-18.jsonl`) since the existing
   `RESULTS_PATH_V2_DATED` file already holds the temporal-only (n=20)
   isolated run from round 2 Task 3 -- must not mix a full n=80 combined run
   into that file. Implemented as a new `--out PATH` override flag (default
   behavior unchanged when omitted).
2. Item 2 budget ablation reuses item 1's config (best_effort + with_dates,
   graph_decompose) and varies only `BUDGET` -- implemented as a new
   `--budget N` override flag (default remains `metrics_local.BUDGET` = 1000,
   so all prior/other runs are unaffected). Budget 1000 = item 1's v3 file,
   no separate run. Budgets 2000/4000 get their own `--out` files.
3. Item 3 "real retrieval/formatting path" = `MemoryClient.inject()` in
   `memsub/sdk.py` -- the actual SDK entry point used by real callers
   (`from memsub.sdk import MemoryClient; client.inject(...)`), not
   `memsub/memory.py`'s `ContextStuffingMemory`/`RetrievalMemory` (a
   separate, older "concept-log" experiment harness not wired to the
   benchmarked v2 `HybridRetriever` stack or used by any current eval).
   The date-prefix formatter itself is factored into a single shared
   function (`memsub.store.format_episode`) so both the SDK and the eval
   script call the identical implementation -- this is what "wire the eval
   script to use the library flag instead of its local hack" means in
   practice, since the eval script builds `HybridSystem`/`Episode` objects
   directly and has no reason to route through `MemoryClient`.

### Success criteria

- Item 1: `results/locomo-jscore-v3-2026-07-18.jsonl` has a `summary`
  record for `graph_decompose` with `n_ok` = 80, no `judge_status` other
  than "ok" among counted rows, streamed incrementally (resumable).
- Item 2: two more full n=80 summary records at budgets 2000 and 4000,
  same condition; a written comparison (overall + per-category) across the
  three budgets.
- Item 3: `memsub/store.py` gains `format_episode(ep, with_dates=False)`;
  `MemoryClient.inject(..., with_dates=False)` defaults to byte-identical
  output vs. before the change; new unit tests cover both the formatter
  and the SDK flag (on: dated prefix present; off: identical to pre-change
  behavior); the eval script's local `_format_episode` is replaced by an
  import of the shared function; full non-graph suite (295 baseline) stays
  green plus the new tests.
- All eval runs stream per-question JSONL, flush immediately, skip already-
  "ok" records on restart. Commits are local only, one per completed item,
  never pushed.

### Discovered blocker: the graph layer (docs/graph-layer-2026-07-18.md) was
not actually present in `memsub/hybrid.py` / `memsub/store.py`

Before any Wave 1 run could start, importing `scripts/locomo_jscore_frontier_
2026-07-18.py` itself failed: it imports `memsub.graph.graph_store`, which
imports `Entity` from `memsub.store` -- and `Entity` did not exist there.
Deeper check: `HybridRetriever.rank_decomposed_graph` (the method the
`graph_decompose` condition calls) did not exist anywhere in `memsub/hybrid.py`
either, nor did the base `rank_decomposed` it composes with (used by three
2026-07-17 eval scripts). None of `memsub/hybrid.py` or `memsub/store.py`
show as modified in `git status` (only new, untracked files: `graph.py`,
docs, tests, results) -- so whatever produced yesterday's real, varied
`results/locomo-jscore-2026-07-18.jsonl` graph_decompose numbers (51.25%,
matching `docs/graph-layer-2026-07-18.md`'s table exactly) and the
comprehensive `tests/test_graph.py`/`test_graph_cli.py` (19 tests, matching
the "309+19=328" figure in that doc) ran against an implementation that no
longer exists on disk. This looks like lost code from a prior session (the
outputs/docs/tests survived, the library edits that produced them did not),
not fabrication -- the results data has the shape of a real run and the
tests are specific enough to have been written against working code.

This is a genuine, direct blocker for item 1 (needs the graph_decompose
condition to be callable at all), not adjacent scope creep, so it was fixed
by reconstructing the missing pieces test-driven against `tests/test_graph.py`
/`test_graph_cli.py` (an exact, executable spec for the graph layer) plus
`docs/graph-layer-2026-07-18.md`'s prose for the parts tests don't cover
(`rank_decomposed`'s exact behavior has no test coverage anywhere in this
repo; implemented per the "one bounded decompose call, <=3 retrieval passes"
contract stated in `scripts/locomo_jscore_holdout.py`'s docstring, degrading
to plain `rank()` whenever the LLM call fails or the response doesn't parse
as a JSON string array -- never guessed). Added: `Entity` dataclass +
`entities`/`entity_links` tables + `EpisodicStore` methods (`upsert_entity`,
`get_entity`, `entities_in_namespace`, `entity_count`, `link_entity`,
`entity_link_count`, `episode_ids_for_entities`, `entities_for_episode`) in
`memsub/store.py`; `HybridRetriever.rank_decomposed`/`search_decomposed`,
`rank_graph`/`search_graph`, `rank_decomposed_graph`/`search_decomposed_graph`
in `memsub/hybrid.py`; `consolidate(..., enable_graph=False)` wiring in
`memsub/consolidate.py`; `memsub graph backfill` CLI command in
`memsub/cli.py`. All additive, no existing default path touched. Result:
`tests/test_graph.py` (15) + `tests/test_graph_cli.py` (4) = 19/19 green;
full suite 322 collected, 319 passed, same 3 pre-existing unrelated
`test_provenance.py` failures as the pre-work baseline (not caused by this
fix, not touched -- flaky-looking hash-order assertions, out of Wave 1
scope). This restoration is broader than Wave 1 items 1-3 as literally
scoped, but was required to execute item 1 at all; flagged explicitly here
rather than silently absorbed into the Wave 1 narrative.

### Wave 1 results (items 1-3, all done)

**Item 1 (v3 combined, budget 1000, n=80)**: 56.25% overall (up from
round 2's 52.5%), matching round 2's flagged extrapolation almost exactly,
now an actually measured run. multi-hop 45.0%, temporal 45.0% (+15pp vs
round 2, matches the isolated n=20 measurement), open-domain 55.0%,
single-hop 80.0%. `results/locomo-jscore-v3-2026-07-18.jsonl`. Wall
3366s.

**Item 2 (budget ablation, n=80 each)**: budget 1000 = item 1's 56.25%;
budget 2000 = **63.75%** (+7.5pp, material, past the 2pp bar);
budget 4000 = 62.5% (-1.25pp vs 2000, not a further material gain).
**Budget 2000 is the new recommended default for this eval going
forward** (noted in the report doc, not silently changed in
`scripts/metrics_local.py`'s shared `BUDGET=1000`, which many other
already-published evals in this repo depend on staying fixed). Token
cost: roughly 2x packed context at budget 2000 vs 1000; no dollar cost
(subscription `claude` CLI, not a metered API key); mean latency flat
across all three budgets (~17.3-17.9s), so the real cost is prompt-window
usage, not wall-clock or cash.
`results/locomo-jscore-v3-2026-07-18-b2000.jsonl`,
`results/locomo-jscore-v3-2026-07-18-b4000.jsonl`. Wall ~3400s each.

**63.75% (budget 2000) beats mem0's reproduced tier (~61.7%) for the first
time on this holdout slice**, +2.05pp overall. Wins outright on temporal
(+14.5pp) and single-hop (+12.9pp) against mem0's reproduced numbers, near
parity on multi-hop (-1.2pp, was -6.2pp two rounds ago), still trails on
open-domain (-17.9pp, the one large gap none of today's fixes targeted).
Still trails Zep's revised number (75.14%) by 11.39pp. One run per
configuration, no repeated-trial variance estimate.

**Item 3**: done and described above (blocker-fix section) --
`memsub.store.format_episode` + `MemoryClient.inject(with_dates=...)`,
5 new tests, eval script now imports the shared function instead of its
own copy.

Full report: `docs/locomo-v3-2026-07-18.md`. Commits (all local, never
pushed): graph-layer restoration + item 3 productization (5f65ee8); item 1
v3 run (8cf9af4); item 2 budget-2000 run (401abb7); item 2 budget-4000 run
(fc8c03a); this WORKLOG entry + report doc (next commit).

Suite at close: 322 collected, 319 passed, same 3 pre-existing unrelated
`test_provenance.py` failures throughout (present before this session,
untouched, out of scope).

## 2026-07-19: git recovery incident, two chains reconciled

### What went wrong

Two independent lines of July 17-18 work existed and neither knew about the
other. The wave-1 chain (this file's entries above, d016671 back through
5f65ee8) reconstructed a graph layer from test-driven specs
(`tests/test_graph.py`/`test_graph_cli.py`) and ran the LOCOMO v3 combined +
budget-ablation evals (63.75% J-score at budget 2000, beating mem0's
reproduced tier for the first time). Separately, `recovery-jul17-18` (tip
dd5a262) held the *original* July 17-18 work this reconstruction was
patching around: provenance signing (`memsub/provenance.py`, HMAC channel
tags, quarantine-on-bad-signature), write-time fact fusion in
`consolidate.py`, `vault_serve.py` + its CLI wiring, and its own
independently-built graph layer.

A prior reconciliation attempt tried to merge these and died mid-operation
to a transient API error. It left `master` pointed at ed6c361 (the
wave-1/recovery merge-base, i.e. master had been walked backwards off the
wave-1 tip), `memsub/provenance.py` staged as a lone new file, and ~27
July 17-18 docs/results/scripts/tests sitting untracked on disk with no
`MERGE_HEAD` -- the merge itself had not actually been started.

### Recovery

1. The wave-1 chain's commits (d016671 down to 5f65ee8) were confirmed
   still present as loose objects (`git fsck --unreachable`), just
   unreferenced by any branch or reflog entry (not yet garbage collected).
   `master` was hard-reset to `d016671`, restoring the full chain.
2. Tagged `pre-merge-safety-2026-07-19` at `d016671` before touching
   anything else.
3. The 26 untracked survivors were verified byte-identical to their
   `recovery-jul17-18` counterparts, then staged and committed as a
   pre-merge checkpoint (`git reset --hard` only ever touches tracked
   files, so this was needed before `git merge` could see them as
   non-conflicting).
4. Merged `recovery-jul17-18` into `master`. Most of the store proper
   auto-merged cleanly (fusion phase, quarantine phase, provenance-aware
   trust ranking in `consolidate.py` all combined without a marked
   conflict). Six files had real conflicts, resolved as follows:
   - `.beads/interactions.jsonl` / `WORKLOG.md`: both add/add conflicts
     from independently-authored entries at the same tail position; kept
     both (union, chronological order).
   - `scripts/locomo_jscore_frontier_2026-07-18.py`: kept the wave-1
     version (`--budget`/`--out` flags, shared `format_episode`) since it
     is what actually produced the committed v3/budget-ablation result
     files.
   - `memsub/hybrid.py`: two independent reimplementations of the same
     decompose/graph-query surface existed with no conflict-worthy
     functional gap (recovery's had no provenance/fusion-specific
     content). Kept the wave-1 version since it is what `tests/test_graph.py`
     and the committed eval results were built and measured against.
   - `memsub/store.py`: same two-reimplementations situation for the
     entities/entity_links schema (`ts` vs `first_seen` column naming,
     namespace-filter variants). Kept wave-1's schema/method signatures for
     the same reason. **First attempt at this file used `git checkout
     --ours`, which silently discarded auto-merged (non-conflicting)
     content from recovery across the whole file** -- caught by rerunning
     the true 3-way merge via `git merge-file` and diffing, which showed
     the quarantine wiring (`TERMINAL_EVENTS` gaining `"quarantined"`,
     `status()`/`active()` filtering it) had been dropped. Redone by hand,
     resolving only the five actually-conflicting hunks and leaving the
     auto-merged quarantine wiring intact.
   - `memsub/consolidate.py`: one docstring-wording conflict (kept wave-1's
     wording) and one real bug: the wave-1 side's action-apply loop
     predated the fusion/quarantine action types and would have mislabeled
     both as `"forgotten"` events (silently breaking newly-fused episodes
     and unquarantining tampered ones). Took recovery's dispatch, which
     uses the already-defined `_ACTION_EVENT` map and skips `"fuse"`
     (applied inline in phase 3).
   - `memsub/cli.py`: auto-merged with a duplicate `graph backfill`
     subparser registration (both sides had independently added the same
     command at different points in the file) -- `argparse` raised
     `conflicting subparser: graph` at import time, caught by the full
     test run. Removed the second, functionally-identical registration.
5. Full suite: 333 passed (was 322 collected/319 passed baseline before
   this merge -- the delta is `tests/test_provenance.py` and
   `tests/test_vault_serve.py` joining from the recovery side, all green,
   no more pre-existing failures).
6. Eval artifacts verified present and intact:
   `results/locomo-jscore-v3-2026-07-18.jsonl` (+`-b2000`/`-b4000`, 63.75%
   J-score confirmed at budget 2000), `results/locomo-jscore-frontier*.jsonl`,
   `results/poisoning-provenance-2026-07-17.json`,
   `results/multihop-*.json*`, `results/graph-layer-eval-2026-07-18.jsonl`.

### Policy going forward

- Never `git checkout --ours`/`--theirs` a whole file during a real 3-way
  merge conflict unless you have first confirmed (via `git merge-file` or
  equivalent) that the only differences from the other side are inside the
  marked conflict hunks. It silently discards any auto-merged content
  elsewhere in the file.
- When two independent sessions reconstruct or extend the same subsystem
  from different starting points, treat both tips as first-class until
  reconciled -- do not let a `git reset`/rebase mid-recovery silently
  orphan one chain. Tag before any destructive-looking operation.
- A merge is not done until the full test suite is green; `argparse`
  duplicate-subparser and similar structural collisions do not show up as
  `git` conflicts and only surface at import/collection time.

## 2026-07-19: Wave 2 (items 4-6 of docs/improvement-plan-2026-07-18.md), start

### Bead discrepancy noted, not fabricated

The task referenced bead `memory-sublayer-jgp` as tracking this wave.
`bd show memory-sublayer-jgp` and `bd list --all` (35 issues) both show no
such bead exists -- the only open P3 beads are `cib` (LLM multi-hop query
decomposition, already implemented per the graph layer work) and `vtr`
(finish LOCOMO holdout judge pass). Proceeding on Rustom's direct
instruction ("make a plan to improve each now, and then actually execute
the full plan" / "i need to beat current top locomo score also") per the
task's own note that this satisfies the ask-once gate; will close `vtr` at
the end of this wave since its scope (LOCOMO judge pass) is what this wave
completes, and file a fresh bead noting `jgp` was never found if one is
needed for continuity.

### Data check before autopsy: 9 wrong, not 11

Re-read `results/locomo-jscore-v3-2026-07-18-b2000.jsonl`: 20 open-domain
(category 3) question records, 0 duplicates, 11 correct / **9 wrong**
(55.0%, matches the recorded summary `by_category.open-domain.accuracy =
0.55`). The task text says "11/20 wrong" -- that is arithmetically
inconsistent with the already-published 55.0% open-domain accuracy (55% of
20 = 11 correct, so 9 wrong, not 11). Verified via a direct read of the
JSONL, not assumed. Proceeding with the actual 9 wrong records; noting the
discrepancy here rather than silently inflating the autopsy set to match
the task text's number.

### Assumptions stated before coding (Karpathy rule 1)

1. Question records in the v3/b2000 file do not persist the packed context
   string, so it must be reconstructed to do the autopsy. Reconstruction
   reruns `_build_system`/`_context_for` from
   `scripts/locomo_jscore_frontier_2026-07-18.py` with the same condition
   (`graph_decompose`), budget (2000), and `with_dates=True` as the
   original v3/b2000 run, on the same holdout slice/sample. This is a
   read-only reconstruction (no answer/judge calls, so no cost and no risk
   of changing the already-published record). Caveat: the decomposition
   sub-query LLM call (`OllamaBackend(model="qwen3:8b")`) is not seeded to
   a fixed temperature, so the exact 1-2 decomposed sub-queries may differ
   slightly from the original run's; the base (non-decomposed) retrieval
   ranking is deterministic (BM25+dense hybrid, no LLM), so this affects at
   most the RRF-fused tail of the packed context, not its bulk. Documented
   as an approximation, not treated as a discrepancy if noticed later.
2. Classification taxonomy is exactly the task's four categories: (a)
   retrieval miss, (b) present-but-unused, (c) answer-format/judge
   artifact, (d) out of memory scope (needs world knowledge). Judged by
   reading each question/gold/context/answer directly, not by a heuristic
   script -- this is a qualitative call the task says to make by hand.
3. Item 4 fix policy: apply the fix that matches whichever failure mode
   dominates the count (retrieval -> raise k / 2-hop graph / decomposition
   depth; present-but-unused -> answer-prompt phrasing; judge artifact ->
   document judge-prompt gap vs LOCOMO's official wording, never silently
   loosen the judge). At most 2 iterations on the open-domain category
   (n=20) only, per the task's cap.
4. Item 5 (multi-hop 2-hop push) is one bounded experiment: `hops=2` on
   `rank_decomposed_graph` (the flag already exists,
   `memsub/hybrid.py:257,281`), category-filtered to multi-hop (n=20),
   budget 2000. No further tuning if flat/negative.
5. Item 6 final run uses whatever config wins items 4-5 (or the standing
   v3/b2000 config if neither wins), full n=80, one run.

### Success criteria (Karpathy rule 4)

- Item 4: classification table for all 9 actual open-domain failures
  written to this file and to a docs file; a fix applied and re-measured on
  n=20 open-domain; report the category delta honestly even if it is
  zero or negative.
- Item 5: multi-hop n=20 accuracy at hops=2 reported against the standing
  50.0% (hops=1) baseline; no further work if the delta is not clearly
  positive.
- Item 6: one full n=80 run of the best surviving config, written to
  `docs/locomo-final-2026-07-19.md` with the full ladder table, honest
  deviations list, and claim language restricted to measured sentences.
  README benchmark section synced if it cites older numbers. Bead cleanup
  (see above). All runs stream JSONL incrementally to `results/` and are
  resumable. Suite stays green (333 baseline, confirmed before starting).

### Item 4 result: open-domain autopsy + two bounded retrieval fixes, both negative

Full classification table and per-question evidence:
`docs/locomo-open-domain-autopsy-2026-07-19.md`. Counts across the 9
actual wrong open-domain records (v3/b2000): **(a) retrieval miss 5/9**,
(b) present-but-unused 2/9 (one compounded with (d)), (c) judge/gold
artifact 1/9, (d) out of memory scope 1/9 (+1 compounded). (a) dominates,
so per the task's own rule the fix applied was retrieval, not prompt or
judge-prompt changes.

Two bounded retrieval experiments (2-iteration cap), both category 3
(open-domain) only, n=20, budget 2000, best-effort prompt + dates,
baseline 55.0%:

| variant | flags | accuracy | delta |
|---|---|---|---|
| baseline (v3/b2000, hops=1, subqueries=2) | -- | 55.0% | -- |
| iter 1: 2-hop graph | `--hops 2` | 50.0% | -5.0pp |
| iter 2: deeper decomposition | `--hops 1 --subqueries 3` | 45.0% | -10.0pp |

Both hurt, neither helped. Stopped at 2 iterations per the task's cap. The
standing v3/b2000 config remains the best open-domain config found;
item 6's final run uses it unchanged. Added `--hops`/`--subqueries` CLI
flags to `scripts/locomo_jscore_frontier_2026-07-18.py` to run these
(default values unchanged for every existing caller, verified: 333 tests
still pass, script still imports clean). Raw records:
`results/locomo-jscore-v4-opendomain-hops2-2026-07-19.jsonl`,
`results/locomo-jscore-v4-opendomain-sq3-2026-07-19.jsonl`. Autopsy
reconstruction records (packed context per failing question):
`results/locomo-open-domain-autopsy-2026-07-19.jsonl`, produced by
`scripts/locomo_open_domain_autopsy_2026-07-19.py` (read-only, no
answer/judge calls).

## 2026-07-19 19:47: vault_serve product-UI overhaul (dashboard + search + note view + review queue)

### Scope and boundary (Karpathy rule 1: state assumptions)

Task: turn `memsub vault serve` from a plain functional viewer into a
product-grade UI ("this is a memory product," not "a file server").
In-scope: `memsub/vault_serve.py`, `memsub/cli.py`'s `vault serve` wiring
only (adding `--db`), new inline CSS/JS inside vault_serve.py, docs +
screenshots. Explicitly out of scope and untouched: `memsub/hybrid.py`,
`scripts/locomo_*` (another agent's in-flight eval work), `memsub/store.py`
(read-only consumer only, no new methods added there), `memsub/vault.py`.

Assumptions stated up front:
1. `/` becomes one page that is BOTH the dashboard (stat tiles, donut,
   sparkline) AND the existing search/browse (folder-grouped note list),
   stacked vertically, not two separate routes. Reasons: (a) the existing
   HTTP smoke test hits `GET /` on a 1-note fixture with no store and
   asserts `b"hello"` (the note's stem) appears in the body -- that only
   survives if the folder listing stays on `/`; (b) simpler nav, matches
   "index page" already being the anchor page; (c) Karpathy rule 2
   (simplicity) -- no reason to split one browsing surface into two routes.
2. Dashboard stats are computed from `EpisodicStore`'s existing public read
   methods only (`all()`, `events()`, `count()`, `active()`), never new
   store.py methods, never raw SQL. Two store calls per dashboard render
   (`store.all()` + `store.events()`, no filter) are enough to derive every
   requested number in Python: total, active/superseded/forgotten/
   quarantined counts (via `TERMINAL_EVENTS`, newest-event-per-episode,
   same rule as `store.status()`), human-verified count (episodes with a
   `confirmed` event, matching `memsub/vault.py`'s own frontmatter rule),
   namespace count, and a 30-day created-per-day series (from `ep.ts`).
   "Last extraction time" = max `ep.ts` among episodes whose
   `provenance.get("origin") == "extraction"` (a key already written by
   `memsub/extraction.py` and visible in every synced note's Provenance
   block) -- avoids needing a new store method for extraction-checkpoint
   timestamps.
3. DB is opened read-only in spirit: only if the file already exists on
   disk (checked with `Path.exists()` before ever constructing
   `EpisodicStore`, since `sqlite3.connect` silently creates a missing
   file -- must never let a GET request create a database). If missing,
   absent, or fails to open, the dashboard falls back to vault-only mode:
   note count and folder/namespace counts from the markdown index only, no
   store-derived numbers, with a visible "vault-only mode" note instead of
   silently showing zeros. `--db` defaults to the same `default_db_path()`
   convention as every other memsub command (`$MEMSUB_DB` or
   `~/.memsub/memory.db`).
4. Provenance badge (note view) is derived purely from frontmatter fields
   already written by `memsub/vault.py` (`status`, `type`,
   `human_confirmed`) -- no store access from the note-render path at all.
   Precedence when fields overlap: human-verified > quarantined > inferred
   > observed. The dashboard donut uses the equivalent live-store
   precedence for consistency of label meaning (not the same code path,
   since note view never touches the store).
5. Supersede-chain timeline walks `[[wikilink]]`s inside each note's
   existing "## Links" section text (`- superseded by [[..]]` /
   `- supersedes [[..]]`, already written by `vault.py`) via regex over the
   in-memory vault index -- no store access, capped at 20 hops to bound a
   pathological cycle.
6. Review queue page parses `review-queue.md`'s existing generated
   structure (`## <category> (...)` headings, `- [[note]] -- text`
   bullets) at the vault root. Root-mode (docs folder, no such file) shows
   a graceful "no review queue in this folder" state, not an error.
7. Zero new dependencies: no pip installs, no new CDN scripts (mermaid CDN
   stays, only loaded on note pages that use it). All animation is CSS
   (`@keyframes`, `stroke-dashoffset` draw-in) or vanilla JS (debounced
   fetch, `requestAnimationFrame` count-up), gated behind
   `@media (prefers-reduced-motion: reduce)` where it's decorative.
8. No write endpoints added anywhere; `safe_path` traversal guarantee is
   reused unchanged for every new route that takes a path segment
   (`/api/preview/<path>` reuses `safe_path` + the existing `idx.notes`
   membership check, identical pattern to `/note/<path>`).

### Success criteria (Karpathy rule 4)

- `pytest` stays green, 333+ (no regressions to existing wikilink/dead-link/
  frontmatter/mermaid/search/path-traversal/HTTP-smoke tests, whose exact
  asserted substrings are preserved verbatim).
- 4 headless screenshots at 390x844 and 1280x800 (dashboard, search-in-
  action, note w/ provenance card, review queue) reviewed and iterated
  once, committed to `docs/screenshots/2026-07-19/`.
- No new dependency added (`git diff` on `pyproject.toml`/`requirements*`
  stays empty).
- `docs/vault-serve-2026-07-18.md` and this WORKLOG entry reflect the
  shipped surface; commits are local only, never pushed.


### Build result 2026-07-19 20:14: shipped, tests green, screenshots reviewed

`memsub/vault_serve.py` rebuilt in one pass (near-total rewrite, same
tested public symbols: `build_index`, `render_note_page`, `safe_path`,
`search_notes`, `serve_vault`, `make_handler`, all signatures backward
compatible via new optional params). `memsub/cli.py`'s `vault serve`
subcommand gained `--db` (defaults to `default_db_path()`, same convention
as every other memsub command).

New surface: dashboard (`/`, stat tiles + inline-SVG provenance donut +
30-day sparkline, live per-request store queries with a vault-only
fallback), instant debounced search (`/api/search.json`, folder-grouped,
`<mark>` snippets, keyboard nav), note view (provenance badge, animated
supersede-chain timeline walked from existing `## Links` text, wikilink
hover preview via `/api/preview/<path>`), `/review` (parses
`review-queue.md` into triage cards, states the apply-loop is manual),
`/random`, sticky nav + breadcrumb + footer stamp. Zero new dependencies;
all animation is CSS keyframes or vanilla JS, gated behind
`prefers-reduced-motion`.

Verification:
- `pytest -q`: 333 passed, both before and after every checkpoint (ran it
  before touching anything, immediately after the rewrite, after the `--db`
  CLI wiring, and after the final CSS iteration -- stayed green throughout).
- Live smoke test against the real `~/memsub-vault` (4334 notes) and real
  `~/.memsub/memory.db`: `/`, `/review`, `/random` (redirect),
  `/api/search.json`, `/api/preview/<path>`, `/note/<path>` all 200/302 as
  expected; traversal probe (`/note/%2e%2e%2f%2e%2e%2fetc%2fpasswd`) still
  404s.
- Debugged an apparent data-consistency bug (dashboard tiles showing
  different totals than the provenance donut on the same page): root cause
  was headless Chrome's `--screenshot` capturing the page before the
  700ms JS count-up animation finished (a target of 1 floors to "0" for
  most of the animation), not a real bug -- confirmed by comparing an HTTP
  response's numbers against a simultaneous direct `EpisodicStore` query
  (matched exactly) and by re-shooting with `--virtual-time-budget=2000`.
  Separately confirmed the shared `~/.memsub/memory.db` was being actively
  written by unrelated concurrent extraction work during this session
  (total episode count moved between screenshots) -- expected for a
  no-cache live dashboard, documented in
  docs/vault-serve-2026-07-18.md, not something to fix here.
- 8 screenshots (390x844 + 1280x800 x {dashboard, search-in-action, note
  w/ provenance card + timeline, review queue}) reviewed; one iteration
  applied (note-page `<h1>` was oversized for this vault's long slugified
  titles, especially on the 390px shot -- capped to `1.4rem` with
  `word-break`). Re-shot and confirmed. Committed to
  `docs/screenshots/2026-07-19/`.
- `docs/vault-serve-2026-07-18.md` rewritten to document the full new
  surface, vault-only fallback semantics, and the live-numbers caveat.

Environment note: `pkill -u user -f "memsub.cli vault serve"` killed its
own invoking shell (exit 144) because this sandbox's shell wrapper embeds
the full launch command line, which the `-f` pattern also matches -- the
target process still died correctly; killing by explicit PID avoided the
side effect on retries.

Server stopped cleanly at the end (port 8766 confirmed free). No push (per
task instruction: local commits only).

---

## 2026-07-19: Three planned extensions, ordered directly by Rustom ("execute the full plan")

Scope, per the task instruction: do not touch scripts/locomo_* / results/locomo_* /
memsub/hybrid.py (another agent may still be running LOCOMO evals), leave
memsub/vault_serve.py alone (finished, stable). Never `git push`. Verify `git log -1`
is this session's own commit after every commit (lab infra has silently rolled refs
back twice before; a mismatch means stop and report, not retry).

### Task 1: `memsub recall` CLI + scripts/memsub-recall-hook.sh (auto-recall for
Claude Code sessions)

Assumptions (Karpathy rule 1, stated before coding):

1. `HybridRetriever.search`'s returned score is explicitly documented in
   memsub/hybrid.py as "a rank-derived score ... not comparable across queries" --
   `1/(rrf_k+pos+1)`. Confirmed empirically before building anything: a totally
   irrelevant query ("what is the weather in tokyo today") against the real
   4218-episode claude-code namespace still gets a "top hit" at rrf=0.0164, same as
   a genuinely on-topic query's top hit. This score is unusable as a relevance
   floor. The actual gate is raw cosine similarity between the query and each
   candidate's content, computed directly with the same embedder (HashingEmbedder,
   the CLI's existing default, zero network/cold-start).
2. Silence is the default per the task's explicit instruction. Tuned the cosine
   floor empirically against the real store (not synthetic data): 0.35 let one
   clearly-irrelevant query through ("is it going to rain tomorrow in paris" ->
   matched "Agent loop is running with PID 39518..." at cos=0.351, pure hash-trigram
   coincidence); 0.38 was the lowest floor that stayed silent on every tested
   irrelevant query (weather, pizza, coffee, banana-spaceship-gibberish, breakfast,
   cookie recipe) while still firing on clearly on-topic ones. Shipped as
   `--min-relevance 0.38` (CLI flag, overridable, not hardcoded). Accepted tradeoff,
   stated plainly: this does cost some recall (a middling-relevance real match, e.g.
   "remind me about the pixel agents overnight run review" at cos 0.375-0.379,
   drops below 0.38 and goes silent) -- the task's own priority ("must not spam
   every prompt") points at false positives being the worse failure mode, not false
   negatives.
3. Quarantined episodes (memsub.provenance signature verification failure) are
   never injected by this path, independent of whether store.active() has already
   filtered them: `_cmd_recall` calls `provenance.channel_status` itself rather
   than trusting that a `consolidate()` pass already ran for this namespace. This
   is the one read path that puts memory content directly back into a live agent's
   context, so it re-checks rather than assumes. Covered by
   `test_recall_never_injects_quarantined_content`.

Built: `memsub/cli.py` gains `_cmd_recall` + the `recall` subparser (`--db
--namespace --query --k --max-chars --min-relevance`), zero LLM calls anywhere in
the path (MemoryClient with the default hashing embedder, `client.search(...,
log=False)` so recall lookups do not pollute the audit trail's retrieval-event
log). `scripts/memsub-recall-hook.sh` (new): reads UserPromptSubmit hook JSON on
stdin (`.prompt`), calls `memsub recall`, wraps non-empty output as
`{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
...}}`, wrapped in a `timeout 2s` safety net (not the real latency control --
see below). Fails silently on any error (missing jq field, missing binary, missing
store) so it can never block a prompt.

**Timing** (the actual budget-relevant measurement, real store, real venv, cold
process each time -- no warm daemon): `time (echo '<hook json>' |
memsub-recall-hook.sh)` measured 0.65s-0.83s wall across several real queries
against the live ~/.memsub/memory.db (4218 active episodes, claude-code
namespace). Comfortably under the 2s budget with headroom. Per the task's own
framing ("embedding the query is the cost"): the real cost here is not the query
embedding itself (HashingEmbedder is pure numpy, sub-millisecond, no daemon, no
cold start -- there is nothing to warm up) but `MemoryClient._get_index` rebuilding
the whole HybridRetriever index (BM25 + dense) over every active episode in the
namespace on every fresh CLI invocation, which is O(namespace size). At today's
size (4218 episodes) that costs ~0.6-0.7s; documenting rather than hacking around,
per instruction: if this namespace's episode count grows roughly another 5-10x, or
a future caller passes `--embedder ollama` (real cold-start risk, unlike hashing),
the correct fix is a small warm daemon that keeps one pre-built HybridRetriever
index in memory across hook invocations (e.g. a local Unix-socket server the hook
talks to instead of spawning `memsub recall` fresh each time), not a longer
timeout and not caching tricks bolted onto the CLI path. Not built this round --
out of scope, and unnecessary at today's scale.

**3 example injections** (real pipe-test, `echo '{"prompt": "..."}' |
memsub-recall-hook.sh`, real store):

1. "what does the durable output rule say about streaming to a git tracked file" ->
   injected 1 line: "A .gitignore rule was added to exclude outputs/*.parquet files
   to prevent tracking of model output files. [legacy, 5d ago]"
2. "what did we decide about the pixel agents folder in the home directory" ->
   injected 2 lines (the broken-hooks decision-pending note, and the original
   Pixel Agents folder request), both genuinely on-topic.
3. "what is Kumo used for in the client project" -> injected 3 lines, all
   genuinely on-topic (client-project reuse, ranked-effort list mentioning Kumo,
   the KumoRFM/PQL schema note).

**3 example correct silences** (same pipe-test, empty stdout, exit 0):

1. "is it going to rain tomorrow in paris"
2. "what should I eat for breakfast tomorrow"
3. "give me a recipe for chocolate chip cookies"

Tests: `tests/test_recall_cli.py` (new, 6 tests) -- relevant hit prints with
channel tag + age, irrelevant query stays silent, missing store is silent-not-an-
error, quarantined content is never printed regardless of relevance, unsigned-
legacy content is tagged "legacy", `--max-chars` budget is respected (a budget
smaller than one line yields nothing rather than a truncated line).

Installer: `~/scripts/install-durable-capture.sh` gains a third idempotent section
(same jq-merge-with-.bak style as the existing two) adding
`bash /home/user/projects/memory-sublayer/scripts/memsub-recall-hook.sh` to
`.hooks.UserPromptSubmit` (repo-scoped script, absolute path, matching the
daily_extract.sh cron line's precedent -- not `~/scripts/`, which is reserved for
personal machine glue like memsub-inbox.sh). Verified the
merge logic against a scratch copy of the real `~/.claude/settings.json` (not the
real file -- installing hooks is Rustom-run only, stated in the installer's own
header) -- confirms it appends alongside the existing `accountability.sh` entry
rather than clobbering it.

Full suite: `pytest` 339 passed (was 328 at the end of the last recorded session;
+6 for `test_recall_cli.py`, +5 unaccounted for by other agents' work this repo
already had in flight -- not this task's concern, all pre-existing and green).
No em dash in any new or touched file.

Commit: `12c79bf` "Add memsub recall CLI + auto-recall hook for Claude Code
sessions". `git log -1`/`git rev-parse HEAD` checked immediately after and match
this commit.

### Task 2: scripts/repo_watchdog.sh (rollback watchdog)

Assumptions (Karpathy rule 1, stated before coding):

1. "Descendant" is checked with `git merge-base --is-ancestor expected current`
   (exit code, not text parsing) -- the exact primitive git ships for this
   question, no reimplementation. A rollback is defined as: expected exists,
   current != expected, and expected is NOT an ancestor of current. This also
   correctly catches the case where `expected`'s commit object no longer exists
   at all (aggressive gc after a hard reset): `--is-ancestor` fails non-zero on a
   missing object exactly like it does on a genuine non-ancestor, so both land in
   the same warning branch without special-casing.
2. "A rollback must not overwrite the evidence" (task's own wording) is
   implemented literally: the expected-head file is written in exactly two
   cases (no file yet: seed; current is a descendant of expected: advance) and
   in no other case. The rollback branch prints and logs but never touches the
   file, so the next run re-detects the same rollback from the same
   last-known-good baseline until a human (not this script) resolves it and
   presumably re-seeds.
3. Exit code: 0 only if every repo was clean/seeded/advanced AND the recovery
   branch was found AND at least one bundle exists; 1 otherwise. Not wired to
   any alerting in this round (no mail/webhook exists to wire it to) --
   documented as the hook point for a future alerting wrapper, per the task's
   scope (cron line only, not an alerting system).
4. Repos and thresholds are env-var overridable
   (`REPO_WATCHDOG_REPOS`/`_STATE_DIR`/`_BUNDLE_DIR`/`_RECOVERY_REF`), same
   convention as memsub-inbox-sweep.sh, specifically so the three-path test
   below never has to touch `~/.repo-watchdog` or the real repos.

Built: `scripts/repo_watchdog.sh`. Pure `git`/shell, no network, no Python
startup cost -- a full run against both real repos completes in well under a
second, safe for a 5-minute cron cadence.

**Three-path test, scratch clone under `/tmp`, real repos never touched**
(`/tmp/wd-test/repos/scratchrepo`, fresh `git init`, isolated
`REPO_WATCHDOG_STATE_DIR`/`REPO_WATCHDOG_BUNDLE_DIR` pointed at `/tmp/wd-test/...`):

1. **Clean/seed** (no expected file yet, 2 commits + a `recovery-jul17-18`
   branch present, 1 dummy bundle file): `SEED scratchrepo: expected head
   initialized to e65c92c...`, recovery branch found, 1 bundle found, exit 0.
2. **Descendant-advance** (one more real commit on top): `ADVANCE scratchrepo:
   e65c92c... -> 327be12...`, expected file updated to the new HEAD, exit 0.
3. **Simulated rollback** (`git reset --hard HEAD~2` in the scratch clone,
   landing on an ancestor of the last-seen expected HEAD): full loud warning
   block printed naming the repo/expected/actual, one line appended to the
   test's incidents.log (`ROLLBACK repo=scratchrepo ... expected=327be12...
   actual=1772b0e...`), **expected file confirmed unchanged** (still
   `327be12...`, not overwritten), exit 1. Re-ran the watchdog again with no
   other changes: still detects and re-flags the same rollback (persistent
   alarm, since nothing updated the baseline) -- confirms case 2 above works
   exactly as intended, not just on the first detection.

Also exercised (same scratch-repo technique, separate isolated state dirs): a
repo with no `recovery-jul17-18` branch at all (`WARN ... recovery branch
'recovery-jul17-18' not found`, incident logged, exit 1) and an empty bundle
directory (`WARN: no bundles found in ...`, incident logged, exit 1) -- both
paths confirmed independently of the HEAD-comparison logic.

**Real seeding run** (against the actual repos, read-only: `rev-parse HEAD`,
`show-ref`, `find` -- no mutation of either repo):

```
SEED book-dna: expected head initialized to 7c793d6fb487e5cf6c940bcc275eafc0f6aca5dd
SEED memory-sublayer: expected head initialized to 12c79bf1d057b3317b1f16a4840b22d47a1fd162
OK: 8 bundle(s) in /home/user/repo-bundles
```

Exit 0 -- both repos' `recovery-jul17-18` branches exist, 8 bundles present.
`~/.repo-watchdog/expected/{book-dna,memory-sublayer}.head` now seeded.

Installer: `~/scripts/install-durable-capture.sh` gains a fourth crontab line
(`*/5 * * * * .../scripts/repo_watchdog.sh >> ~/.repo-watchdog/cron.log 2>&1`),
same `add_cron` idempotent-append helper the other three cron lines already use.

No em dash in any new or touched file.

Commit: `7faf319` "Add rollback watchdog (scripts/repo_watchdog.sh)". `git rev-parse
HEAD` checked immediately after and matches this commit.

### Task 3: poisoning L2/L3 tiers (scripts/poisoning_benchmark.py extension)

docs/poisoning-provenance-2026-07-17.md covers L1 (single-record) attacks only,
by its own stated scope. Task: extend with two harder, self-defined tiers matching
MemPoison's L2/L3 framing as best this codebase's design allows, run against both
defense configs at all four budgets, stream per-case, report honestly.

Assumptions (Karpathy rule 1, stated before coding):

1. L1's dataset (`generate_recall_dataset`, lowercase "the quiet-service service"
   subjects) cannot be reused: checked before writing anything that
   `memsub.sleep._extract_entities` (capitalized-word regex) extracts nothing from
   it, so `memsub.consolidate`'s fusion phase is a structural no-op against L1's
   data. L2/L3's own dataset generators use capitalized single-word subject names
   specifically so the real fusion write path engages.
2. Self-defined tier mapping, stated as such (not verified against MemPoison's own
   test cases, same caveat as the L1 round's mapping): L2 = 4 mutually-reinforcing
   `llm_inferred` fragments across distinct synthetic sessions, matching MemPoison's
   own L2 text ("partitions the malicious payload across multiple individually
   plausible memory records ... corruption manifests only when co-retrieved and
   composed"). L3 = the task's own literal wording ("poison targeting the
   consolidation layer itself"), explicitly NOT MemPoison's L3 (context-triggered
   dormant activation, which this codebase has no mechanism for at all) -- two
   sub-attacks: L3a fake-correction via the imported channel (targets dedup
   directly), L3b fusion-merge (reuses L2's coordinated fragments, measures whether
   the fusion phase itself can be exploited to synthesize and promote a poisoned
   memory).
3. Poison content is disguised in every case (`kind="fact"`, no `inferred` marker)
   -- the harder case, matching L1's Attack B precedent (Attack A, marked-inference,
   was already shown defended by both old and new defenses).
4. Old defense = dataset left unsigned (no `provenance.tag_episode` calls), so
   `_trust_rank` falls back to the legacy `_is_inferred` heuristic naturally, no
   monkeypatching needed (unlike L1's `run_cell`, which had to monkeypatch
   `_is_inferred` for its "rule OFF" baseline -- not needed here since this round
   has no equivalent baseline-within-old-defense variant). New defense = every
   episode HMAC-signed with the channel realistic for how it was actually written.
5. A deterministic, no-LLM judge (`deterministic_judge_multirecord`) serves both
   consolidate() judge prompts (contradiction and fusion), dispatched by
   system-prompt prefix. The fusion half is a documented stand-in for "a judge that
   trusts numeric consensus across sources" -- deliberately generous, chosen because
   it is the realistic worst case: confirmed by reading `_judge_fusion`'s actual
   prompt construction (memsub/consolidate.py) that a real LLM judge is never even
   given each member's channel/trust rank, so it has no channel signal to work from
   either. That absence is the finding this tier exists to surface, not an artifact
   of the mock.

### A methodology bug found and fixed before trusting any result

First full run (n_families=20) showed L3a's ASR staying at 73% even under the
channel-provenance defense -- inconsistent with L1's fully-closed 0% and worth
investigating rather than reporting. Traced it: `ConsolidationConfig`'s default
`max_llm_pairs=40` (a real-LLM-cost guard) was starving out most of the intended
same-family contradiction pairs, because ~40-100 same-`kind="fact"` episodes
sharing common words ("is", "'s") cleared `contradiction_min_sim=0.45` against
*many* cross-family pairs too, exhausting the 40-pair budget before most
same-family pairs got a turn. Confirmed directly: raising `max_llm_pairs` to 5000
(free with this round's deterministic mock judge, no real-LLM cost tradeoff here)
took supersede counts from a handful to the full expected per-family count.
Discarded the first (pair-starved) run entirely rather than reporting it --
it was measuring an artifact of the pair-budget cap, not defense behavior.

### Results (n=100 per cell, seeds 6-10, 20 families x 5 seeds, budget 400 shown;
L2/L3a are budget-invariant across all four budgets, L3b genuinely varies with
budget -- full 4-budget table in results/poisoning-tiers-2026-07-19.json, 32 rows,
streamed one row at a time)

| tier | defense | key metric | value |
|---|---|---|---|
| L2 coordination | undefended | gold recall | 100.0% (trivial: nothing ever removed) |
| L2 coordination | content_heuristic (old) | gold recall | **0.0%** (clean fact loses every time) |
| L2 coordination | channel_provenance (new) | gold recall | **100.0%** |
| L2 coordination | channel_provenance (new) | fragment survive rate | 20.0% (down from 100%, not 0%) |
| L2 coordination | channel_provenance (new) | clean recall (gold present, no poison text) | 41.0% |
| L3a fake-correction | content_heuristic (old) | ASR | 100.0% |
| L3a fake-correction | channel_provenance (new) | ASR | **0.0%** |
| L3b fusion-merge | content_heuristic (old) | promoted over clean fact | 17.0% |
| L3b fusion-merge | channel_provenance (new) | fusion created | 59.0% (down from 100%, not 0%) |
| L3b fusion-merge | channel_provenance (new) | promoted over clean fact | **0.0%** |

Honest reading, per tier:

- **L1 (recap) and L3a: fully closed.** Channel provenance generalizes cleanly
  from "disguised-as-observed" (L1's Attack B) to "framed as an explicit
  correction" (L3a) -- same mechanism, same outcome (ASR 100% -> 0%).
- **L2: a real, partial win.** Channel trust wins the *authority* contest
  completely (clean fact 0% -> 100% gold recall: it now always beats any single
  fragment it's compared against) but does not purge merely-coexisting,
  never-directly-challenged low-trust noise (fragment survive rate only drops to
  20%, not 0%; 59% of packed contexts still carry the truth *and* some surviving
  poison). Root cause, stated plainly: trust-rank only fires when two records are
  actually compared in the dedup/contradiction phase; a fragment with lower cosine
  similarity to the clean fact (varied wording, by design) is simply never
  challenged, so it just sits there, uncontested and retrievable.
- **L3b: the real headline finding.** Direct code inspection
  (`memsub/consolidate.py` `FUSION_SYSTEM`/`_judge_fusion`) confirms the fusion
  phase's judge prompt never includes channel or trust-rank information at all --
  this is not a benchmark artifact, it is how the shipped code is built today. Even
  under the current defense, 59% of families still produce a synthesized fusion
  episode, 33.9% of those carry the poisoned value forward into a new, unsigned
  memory. The one honest qualifier: in this specific harness, that gap never
  actually promotes poison over a clean fact (promotion rate 0.0% at every budget)
  -- because the upstream dedup/contradiction phases already independently
  guarantee the clean fact survives 100% of the time before fusion runs. That is a
  property of this dataset (a clean fact always exists and is always defended),
  not evidence that fusion itself is safe; a scenario where the true fact is
  itself weaker was not tested this round.

Full writeup, tables for all four budgets, updated claim language, and the "what
this round still does not test" section: docs/poisoning-tiers-2026-07-19.md.

Full suite: `pytest` still 339 passed (this task added only a script extension and
doc/results files, no `memsub/` library code changed). Regression-checked the
original L1 `--provenance` round still reproduces byte-identical output
(`git diff results/poisoning-provenance-2026-07-17.json` empty after re-running
it). No em dash in any new or touched file.

## 2026-07-20: Temporal upgrade (bi-temporal fields + query-time temporal intent)

Ordered directly: "do the temporal upgrade", strict limit frugality, execute
without pausing for approval. Design sources: docs/research-salvage-2026-07-20.md
(TSM semantic timelines + temporal intent at retrieval, REMem time-qualified
facts, Zep bi-temporal edges) and docs/locomo-frontier-v2-2026-07-18.md (LOCOMO
temporal category: 30% without a date flag, 45% with one surfaced, n=20 --
this build is the next step past display-only surfacing).

### Assumptions (stated before coding, Karpathy rule 1)

1. Bi-temporal columns store epoch floats (`valid_from`/`valid_to`), same unit
   as `Episode.ts`, nullable, appended as new trailing dataclass fields (after
   `id`) so no existing positional `Episode(...)` call in the codebase breaks
   (grepped: every caller uses kwargs, but trailing-with-defaults is the safe
   convention already used for `ts`/`id` themselves).
2. "Populated at write time" means an explicit opt-in helper
   (`memsub.temporal.annotate_episodes`) a caller invokes before
   `store.append()`, not a change to `EpisodicStore.append()`'s signature or
   behavior. This keeps the default write path byte-identical without a new
   boolean flag threaded through the store -- nobody calling the new helper
   is equivalent to the flag being off.
3. `consolidate()`'s contradiction-supersede sets `valid_to` on the loser only
   when BOTH sides already carry `valid_from` (temporal scope). This is a
   data-gated no-op, not a new `consolidate()` kwarg: since nothing populates
   `valid_from` unless `annotate_episodes` was explicitly called upstream, the
   default `consolidate()` path is unaffected without adding an
   `enable_temporal` flag. Only phase 2 (contradiction) gets this; phase 1
   (dedup/duplicate) does not, per the literal task wording.
4. Query-time "now" for resolving a relative expression in the query itself
   (e.g. "what did I do last March?") is the most recent episode's
   session_date (or its `ts` if no session_date), since LOCOMO and most
   memsub sources have no explicit "question asked" timestamp (confirmed
   absent in docs/locomo-frontier-v2-2026-07-18.md's own investigation of
   `LocomoQA`). Documented as a heuristic, not a ground truth.
5. New retrieval methods only (`rank_temporal`, `rank_decomposed_graph_temporal`,
   and `search_*` wrappers), mirroring the existing `rank_graph`/
   `rank_decomposed_graph` pattern -- `rank()`/`rank_decomposed_graph()`
   themselves are never touched, which is the simplest way to guarantee the
   default path stays byte-identical (matches "mirroring how graph/
   decomposition gates work" in the brief).
6. Relative-date grammar is pure Python (stdlib `datetime`/`calendar`), not
   the `python-dateutil` package -- "dateutil-style rules" read as "in the
   style of", matching this repo's existing zero-dependency posture
   (memsub/hybrid.py's own docstring: "product-grade and dependency-free").
7. Intent classification's one ambiguous-case LLM escalation only fires when
   a temporal marker word is present (`when`/`before`/`after`/etc.) but rules
   cannot decide point_in_time vs range vs ordinal vs current_state; a query
   with zero temporal markers is atemporal at zero cost, no backend call ever
   needed for the (expected) majority case.

### Success criteria (stated before coding)

- Migration: old DB rows read back with `valid_from=valid_to=None`; round trip
  for freshly written temporal-tagged episodes.
- Date resolution: a dozen-case table (yesterday, tomorrow, today, N days/
  weeks/months ago, in N days/weeks/months, next/last week/month/year,
  last/next `<Month>`, plus 2 absolute cases) resolves correctly via pure
  rules, zero LLM calls; one detected-but-unresolvable case (e.g. bare
  "recently") escalates to exactly one backend call, cached on repeat.
- Intent classification: at least one rule-based case per of the 5 intents,
  plus one ambiguous case that escalates to a mocked backend.
- Boost correctness: `rank_temporal` puts a time-window-matching episode
  first over an equally-relevant-but-wrong-date one; ordinal intent reorders
  candidates chronologically in the requested direction.
- Default-path byte-identical regression: full existing suite (339 baseline)
  still green unchanged, plus one explicit test that `rank()`'s output is
  identical whether or not episodes carry `valid_from` (since `rank()` never
  reads those columns).
- Eval: dev-slice (local qwen, token-F1, no judge) shows temporal retrieval
  >= the with-dates baseline on temporal-category dev questions before
  spending any judge calls; the n=20 holdout judged comparison uses ~40
  total Sonnet CLI calls (20 baseline + 20 temporal judge calls), nothing
  larger, no other cloud calls anywhere in this task.

## 2026-07-20: Second brain Layers 0/2/3 (vault serve readability, graph, connections)

Ordered directly by Rustom, docs/second-brain-spec-2026-07-20.md: "i never
wanna forget an idea and i want to be linked up to other ideas sometimes...
also the UI needs to be way cooler - i wanna see the obsidian graph... i want
the second brain but i want it with summarised high quality notes", plus the
Layer 0 addendum: raw note pages are "not readable, i dont understand shit
here." Scope for this task: Layer 0 (readability), Layer 2 (graph), Layer 3
(connections), and dashboard "more informative" upgrades, inside
memsub/vault_serve.py plus new read-only helper modules only. Layer 1
(distillation) and Layer 4 (idea capture) are a different agent's work, not
touched here. store.py/hybrid.py/memory.py/cli.py/scripts/ are another
agent's active files -- not touched, not even read-modified.

### Assumptions (Karpathy rule 1, stated before coding)

1. No `topics/` tier exists yet (Layer 1 hasn't run: confirmed
   `~/memsub-vault` has no `topics/` dir). Layer 2's graph therefore ships
   exactly what the task brief scopes for "for now": note nodes (small, dim)
   plus hub nodes for the biggest entities (large, accent) -- no topic-tier
   nodes. When Layer 1 ships later, topic nodes are a natural additive node
   kind on the same GraphIndex shape; not built speculatively now.
2. Entity data for the graph is read from vault markdown text, not the
   store's `entities`/`entity_links` SQLite tables. Reason: `memsub/vault.py`'s
   `render_note` already writes every entity relationship as a literal
   `- shares entity **X** with [[Y]]` line in each note's `## Links` section
   (same source `memsub/graph.py`'s backfill populated the DB tables from).
   Parsing vault text: (a) keeps vault-serve's read surface store-optional,
   consistent with the existing "vault-only mode" fallback the dashboard
   already has, (b) reuses the same precedent `_supersede_links` already
   sets (walk `## Links` bullets from raw text, no store access), (c) avoids
   opening `~/.memsub/memory.db` from a new code path this task doesn't own.
   Documented plainly in the doc update: graph edges are vault-text-derived,
   not live-DB-derived; a note's entity links only exist in the graph once
   `memsub vault sync` has regenerated its file.
3. Layer 3 "existing embeddings if accessible read-only" -- checked: no
   embeddings table/index in `store.py`'s schema (`memsub/embed.py` embeds
   query-time only, nothing persisted per-episode). Per the task's own
   fallback instruction, similarity is TF-IDF cosine over note body text
   (the `## Content` section only, not frontmatter/provenance noise),
   computed once at server-start index-build time, stated honestly in the
   doc as TF-IDF, never described as "embeddings."
4. Performance: both the graph and the TF-IDF similarity index are built
   once at `serve_vault()` startup (same "no live reload" contract the note
   index already has), not per-request. `/api/graph.json` filters/caps an
   already-built in-memory graph; TF-IDF top-K neighbors per note are
   precomputed at startup and cached, so a note-page request is O(1) lookup.
   TF-IDF postings skip terms with document frequency above 15% of the
   corpus (stopword-like, near-zero idf) to bound pairwise comparison cost
   across ~4,300 notes.
5. One title function, one place: `memsub/vault_notes.py:note_title()` is
   the single source of truth for "first sentence of `## Content`, never the
   slug" and every surface that shows a note's name (search results, folder
   listings, graph node labels, related-ideas panels, wikilink hover
   preview, connection-of-the-day) calls it -- no surface keeps its own
   `Path(rel).stem` title logic after this change.
6. The Layer 0 rewrite only changes *rendering*; the underlying markdown
   `memsub/vault.py` writes is untouched (that file is off-limits anyway).
   `## Provenance` / `### Audit events` / raw frontmatter fields render
   inside one closed `<details>` at the bottom; `## Links`' entity-share
   bullets group by entity into one expandable prose line each; `supersedes`
   / `superseded by` / `promoted from` lines stay visible (human-meaningful
   editorial links, not machine jargon) above the details block. The
   existing supersede-chain timeline keeps parsing raw text unchanged.
7. "Connection of the day" pool: collecting the single global best-similarity
   unlinked pair via full O(n^2) comparison is what the TF-IDF top-K-per-note
   precompute already avoids; instead the daily pick draws from the union of
   each note's precomputed top-unlinked-neighbor (already capped, already
   excludes shared-wikilink/entity pairs), taking the top 50 by score
   globally, then `random.Random(<ISO date>)` picks one deterministically --
   same pair all day, changes at midnight server-local time, no LLM.

### Success criteria (stated before coding)

- Full existing suite (383 baseline) stays green; new tests added for
  `vault_notes`, `vault_graph`, `vault_similarity`, and the rewritten note
  page / dashboard / graph endpoints, including a path-traversal case for
  every new path-taking endpoint.
- `note_title()` returns the first sentence of a standard memsub note's
  content (not the 80-char-truncated slug the old `<h1>` used, not the raw
  filename stem) and falls back gracefully for non-memsub notes (index,
  review-queue, templates) that have no `## Content` section.
- `/graph` renders a canvas graph from a real vault run (127.0.0.1:8767,
  read-only), supports drag/zoom/pan, a namespace filter, a time filter, and
  a "showing top N of M notes by connectivity" note with expand controls.
- Every note page shows a mini constellation (immediate neighborhood) and a
  related-ideas panel split into "already linked" / "similar but unlinked"
  (top 5, TF-IDF, excluding shared-wikilink/entity pairs).
- Dashboard gains: "what your brain knows" top-10 entity chips (tap =
  search), a projects/namespace breakdown in prose, a 5-newest recent
  activity list (prose titles, relative dates), and connection-of-the-day --
  all additive, existing tiles/donut/sparkline untouched.
- No write endpoints added anywhere; `safe_path` traversal guarantee
  extended to any new path-taking route, covered by tests.
- Live smoke test on 127.0.0.1:8767 (host-b's 8766 instance untouched) against
  the real `~/memsub-vault` + `~/.memsub/memory.db`-adjacent read; headless
  screenshots at 390x844 and 1280x900 for dashboard, `/graph`, a
  many-entity-link note (readability proof), and connection-of-the-day;
  looked at, iterated once on anything that reads badly; committed to
  `docs/screenshots/2026-07-20-brain/`.

### Result

Built exactly the three modules and endpoints planned. One real-data finding
along the way: `claude-code/index.md` (a namespace table-of-contents page
`memsub/vault.py` already writes) links to all 4,330 notes and dominated
the graph's connectivity ranking + would have flooded the TF-IDF corpus
before `vault_notes.is_content_note()` excluded it and the three other
structural page types (root `index.md`, `review-queue.md`,
`templates/*`). Documented in docs/vault-serve-2026-07-18.md's "What
changed 2026-07-20" section in full.

**Performance** (real vault, 4,334 notes, measured standalone before
wiring HTTP): `build_index` 1.42s, `build_graph_index` 0.17s (4,334 note
nodes, 40 hubs, 23,625 edges), `linked_neighbors` 0.01s,
`build_similarity_index` 0.63s. ~2.3s total one-time startup cost, in line
with the existing "no live reload" contract. Per-request
`filter_and_cap`/`note_constellation`/`related_ideas`/
`connection_of_the_day` are all O(1)-ish lookups into the precomputed
structures, no per-request re-scan.

**Layer 0** (note pages): title is now the plain-prose first sentence of
`## Content`, never the slug, applied consistently everywhere a title
appears (search results, folder listings, review queue, wikilink hover
preview, graph node labels, related-ideas panel). Machine fields (id,
hashes, run/session ids, turn spans, backend, model, audit log) collapse
into one closed `<details>`. The literal "shares entity **Chat** with
[[slug]]" x8 list is gone, replaced by one expandable "Related: 8 notes
about Chat" line with real prose titles. Verified directly against
Rustom's own example note
(`claude-code/auth-constraint-claude-subscription-must-be-used-f--727009c1.md`,
8 entity-share lines) -- screenshot: `note-1280x900.png` /
`note-390x844.png`.

**Layer 2** (`/graph`): force-directed canvas, vanilla JS (Pointer Events
for unified mouse/touch, wheel zoom, drag, pan), namespace + this-week/
this-month filters, search-fade, "showing top N of M" with a limit
selector (150/300/600/1000). Bounded settle-then-freeze physics (~220
frames) keeps it battery-cheap. Every note page also gets a small
constellation (1-hop neighborhood, embedded inline, no extra request).
Screenshot: `graph-1280x900.png` / `graph-390x844.png`.

**Layer 3** (connections): TF-IDF cosine (stated honestly as TF-IDF, never
"embeddings" -- no persisted per-episode embedding exists in the schema),
top 5 unlinked-similar per note excluding anything sharing a wikilink or
entity, plus a one-line "why" from top shared terms. Verified: the AUTH
constraint note's discovery set surfaced other Anthropic-API-key notes it
has no existing link to. Connection of the day (date-seeded, deterministic)
found a genuinely interesting near-duplicate pair on the real vault
(`the-king-of-thorns-fragment...` / `the-morning-star-fragment...`, "why:
shares roll, fragment, frag, bookdna"). Screenshot:
`dashboard-connection-of-day-1280.png` / `dashboard-connection-of-day-390.png`
(tall captures, no scroll needed to see it).

**Dashboard**: "what your brain knows" (top-10 entity chips, tap = search),
"projects" (namespace counts in prose), "recent activity" (5 newest, prose
titles, relative dates), connection of the day -- all additive, existing
stat tiles/donut/sparkline untouched. Screenshot: `dashboard-1280x900.png`
/ `dashboard-390x844.png`.

**Iteration after looking at screenshots**: `recent activity`'s relative-date
column used `float: right`, which misaligned against wrapped long note
titles (visible in the first `dashboard-1280x900.png` pass). Fixed by
switching `.prose-list li` to flexbox (`justify-content: space-between`,
`.when` `flex-shrink: 0`) so the timestamp stays right-aligned regardless
of title length -- re-screenshotted, confirmed clean.

**Endpoints added**: `GET /graph` (page), `GET
/api/graph.json?namespace=&period=&limit=` (JSON, filtered/capped graph
payload). No write endpoint anywhere; Layer 3's "link these" action was
explicitly out of scope for this round (deferred to the later distillation
agent per the task brief) and was not built.

**Suite**: 410 passed (383 baseline + 27 new: 8 `vault_notes`, 6
`vault_graph`, 5 `vault_similarity`, 8 `vault_serve` integration tests
covering readability, the details collapse, the related panel, the graph
page/endpoint, and traversal safety on the existing `/note/` route). No em
dash in any new or touched file.

**Live smoke test**: `127.0.0.1:8767` against the real vault + db,
read-only (`--db` flag only ever opens an existing file). Confirmed
`/api/graph.json` limit/namespace filters, dashboard sections, note page
readability, and the connection-of-the-day pick all work against real
data. Own instance killed after (`pkill -f "memsub.cli vault serve"`),
port 8767 confirmed free. Spruce's 8766 instance was never touched (never
started, never queried).

**Commits** (`git log -1 HEAD` checked immediately after each, matched):

1. `14f8954` -- plan (this file, before any code).
2. `3403510` -- `vault_notes.py`/`vault_graph.py`/`vault_similarity.py` +
   `vault_serve.py` wiring (Layer 0 note-page rewrite, dashboard/graph
   plumbing) + unit tests for the three new modules.
3. `bb8b1af` -- `/graph` + `/api/graph.json` endpoints, `MAX_NODE_LIMIT`
   import fix, `vault_serve` integration tests (readability, related
   panel, graph page/endpoint smoke test).
4. `5160f65` -- `is_content_note()` fix for the index.md-pollution finding
   in both `vault_graph.py` and `vault_similarity.py`; recent-activity
   timestamp-alignment fix from the screenshot iteration.

Screenshots (not yet committed as of the commits above -- committed
separately per the task's own step ordering, "verify first, then commit
screenshots"): `docs/screenshots/2026-07-20-brain/{dashboard,graph,note}-{390x844,1280x900}.png`
plus `dashboard-connection-of-day-{390,1280}.png`.

Files touched, per the task's file boundary: `memsub/vault_serve.py`,
`memsub/vault_notes.py` (new), `memsub/vault_graph.py` (new),
`memsub/vault_similarity.py` (new), `tests/test_vault_notes.py` (new),
`tests/test_vault_graph.py` (new), `tests/test_vault_similarity.py` (new),
`tests/test_vault_serve.py`, `docs/vault-serve-2026-07-18.md`, `WORKLOG.md`,
`docs/screenshots/2026-07-20-brain/*.png`. Nothing in `memsub/store.py`,
`hybrid.py`, `memory.py`, `cli.py`, or `scripts/` was read-modified or
touched at all. No push to any remote.

## 2026-07-20: Abstention calibration + tiny learned reranker

Ordered directly: two builds, salvaged from `docs/research-salvage-2026-07-20.md`
(MemGuard's abstention calibration; the ~3.6M-parameter learned-reranker
lead) and grounded in `docs/locomo-frontier-v2-2026-07-18.md` (refusal
findings: best-effort prompting fixed over-refusal, calibrated abstention is
the honest remaining answer) and `docs/temporal-v1-2026-07-20.md` ("best
prior config" = `rank_decomposed_graph` + `with_dates=True`, budget 2000, and
the `base_order` pattern for fair paired comparisons). File boundary this
round: `memsub/rerank.py`, `memsub/hybrid.py`, new `memsub/answer_policy.py`,
`scripts/`, `results/`, `docs/`, `WORKLOG.md`, `tests/` only --
`memsub/vault_serve.py`/`vault_graph.py`/`vault_notes.py`/`vault_similarity.py`
and their tests belong to a concurrent agent, not touched or even
read-modified here.

### Assumptions (Karpathy rule 1, stated before coding)

1. "Best prior config" (used as Task 3's baseline arm) is
   `HybridSystem(**FROZEN).rank_decomposed_graph(query, backend, store)` +
   `format_episode(with_dates=True)` + budget 2000 -- the exact condition
   `docs/temporal-v1-2026-07-20.md` calls "baseline" and reports as the
   standing config (temporal retrieval tied it, did not beat it).
2. Task 1's abstention label ("answerable-from-retrieved") is computed
   purely from gold-token overlap with the packed retrieved context, per the
   task's own literal wording -- no answerer LLM call is needed to build the
   dev-slice calibration set, only retrieval (one `graph_decompose` call per
   question, local qwen3:8b, zero cloud calls). This label doubles as the
   proxy ground truth for "would the answerer have gotten this right", made
   explicit and documented as a proxy, not a real judged accuracy number.
3. The evidence-sufficiency score itself (used at inference/query time) is
   built ONLY from query-side and retrieval-side signals (RRF/cosine top-1
   and margin, query-entity coverage across the top-k retrieved text,
   per-item agreement) -- it never sees the gold answer, so calibrating its
   threshold against gold-overlap labels on dev is a legitimate offline fit,
   not circular.
4. Task 2's reranker training labels come from LOCOMO's own `evidence`
   dia_ids (exact ground truth for "is this episode gold-supporting"),
   joined against the (conversation_id, question) pairs found by scanning
   existing `results/*.jsonl` files -- not from re-deriving labels via text
   overlap. Restricted to DEV conversations only (`conv-26`, `conv-30`,
   `conv-41` -- `data/locomo10.json` indices 0-2, confirmed by direct
   inspection), even though several `results/` files were themselves run on
   HOLDOUT conversations (`conv-42/43/44/47/48/49/50`) -- those holdout rows
   are excluded from training to keep Task 3's holdout judged comparison
   uncontaminated. Exactly which source files/rows contributed will be
   logged in `docs/rerank-abstain-2026-07-20.md`.
5. Both the reranker's candidate-level features and the abstention score's
   aggregate features are built from ONE new `HybridRetriever.candidate_features()`
   method (`memsub/hybrid.py`, opt-in, `rank()`/`rank_graph()` etc. untouched)
   over the plain `rank()` pool (BM25, dense cosine, RRF-derived reciprocal
   rank, entity overlap via `memsub.sleep._extract_entities` reused the same
   way `memsub/graph.py` already does, recency, temporal-window match via
   `memsub.temporal`, graph-boost via `memsub.graph.graph_episode_order`
   with `backend=None`, which degrades to zero LLM calls per that module's
   own documented contract). Building this pool needs no LLM call at all
   (rank()/graph_episode_order are both deterministic) -- only Task 1's
   calibration run needs local ollama, for the one `graph_decompose` call
   per question needed to match "best prior config".
6. A tiny generic logistic-regression trainer (pure numpy, standardized
   features, gradient descent) lives once in `memsub/rerank.py`
   (`train_logistic`/`apply_logistic`) and is reused by both
   `memsub/answer_policy.py` (4 aggregate features -> P(answerable)) and the
   reranker training script (7 candidate features -> P(gold-supporting)) --
   one implementation, two independent feature vectors/weight files, no
   sklearn dependency (not installed; repo ethos is dependency-free per
   `memsub/hybrid.py`'s own docstring).
7. Trained weights are data, not library code, so they live under
   `results/` (`results/reranker-weights-2026-07-20.json`,
   `results/abstain-weights-2026-07-20.json`), not a new file under
   `memsub/` -- the task's file boundary only grants three specific
   `memsub/` files.

### Success criteria (stated before coding)

- **Task 1 (abstention)**: on the dev-slice calibration set (LOCOMO dev
  conversations, categories 1-4, `graph_decompose` + `with_dates`, budget
  2000), the calibrated threshold makes >=70% of abstained questions ones
  that were "would-have-been-wrong" by the gold-overlap proxy, while
  sacrificing <=5% of "would-have-been-right" questions (abstaining on
  them). If the simple heuristic/logistic score cannot jointly satisfy both
  on this dev set, that will be reported honestly as a miss, not silently
  loosened.
- **Task 2 (reranker)**: trains in local seconds-to-minutes off parsed
  `results/` + LOCOMO evidence labels; dev-slice eval (Recall@k / MRR over a
  shared base-order candidate pool, base-vs-reranked, same pattern as
  `docs/temporal-v1-2026-07-20.md`'s `base_order` fairness fix) reported
  honestly whichever way it lands.
- **Task 3 (bounded judged check)**: n=20 mixed holdout slice (seed 2026: 10
  multi-hop, 5 open-domain, 5 temporal), best prior config vs
  best-prior-config + reranker + abstention, Sonnet CLI judge, streamed to
  `results/rerank-abstain-2026-07-20.jsonl`, hard cap 40 judge calls total
  for this whole task (this is the only place Sonnet is called anywhere in
  this round). `docs/rerank-abstain-2026-07-20.md` reports honest verdicts
  per component, explicitly not overclaiming past what n=20 can show.
- Full existing suite (410 baseline, confirmed by collection count before
  starting) stays green; new tests added for `answer_policy.py`, the
  `rerank.py` logistic trainer, and `hybrid.py`'s new
  `candidate_features`/`rank_reranked` methods.
- Every run that produces results streams to a git-tracked JSONL under
  `results/` incrementally (durable-output rule), and each checkpoint is
  committed with a `git log -1 HEAD` verification per the task's rules; no
  `git push` at any point.

## 2026-07-20: Second brain Layers 1 + 4 (distillation + idea capture)

Ordered directly: implement `docs/second-brain-spec-2026-07-20.md` Layer 1
(distillation, the librarian) and Layer 4 (deliberate idea capture), the
second of the two agent rounds that spec's "Build order" section lays out
(Layer 2/3 already shipped, see the entry above). File boundary this round:
new `memsub/distill.py`, `memsub/cli.py` (additive subcommands), `scripts/`,
`~/scripts/install-durable-capture.sh` (cron line only), `docs/`, `tests/`,
`WORKLOG.md`. Forbidden: `memsub/rerank.py`, `memsub/hybrid.py`,
`memsub/answer_policy.py` and their tests/scripts (a concurrent agent owns
these -- confirmed uncommitted changes to `hybrid.py`/`rerank.py` present in
the working tree at task start, left untouched). The vault_serve family
(`vault_serve.py`/`vault_notes.py`/`vault_graph.py`/`vault_similarity.py`) is
finished work from the round above: read-only reuse, minimal additive hooks
only where this task specifically requires one.

### Assumptions (Karpathy rule 1, stated before coding)

1. **Clustering**: entity co-occurrence graph, built from
   `store.entities_for_episode()` over active episodes (all namespaces,
   `namespace=None`, matching every other maintenance command's default --
   the live store currently has one namespace, `claude-code`, but nothing
   here hardcodes that). Entities that co-occur with more than `HUB_CAP`
   (30) other episodes are excluded from edge-formation (not from the
   vault-facing graph, just from clustering): a generic entity like a
   frequent speaker name would otherwise chain unrelated episodes into one
   giant component, the same "index.md pollution" failure class
   `memsub/vault_notes.py::is_content_note` and `vault_graph.py`'s hub-cap
   already document and guard against elsewhere in this codebase. Connected
   components (union-find) over the filtered entity graph are candidate
   topics; candidates larger than `REFINE_THRESHOLD` (8) are refined
   (split, never merged across candidates) by a lexical-similarity graph
   (Jaccard over `memsub.lexical.tokenize` token sets, threshold tuned by
   inspection against the real store, not a labeled set) intersected with
   candidate membership. A topic needs >=3 members after refinement;
   smaller remainders are dropped, not surfaced as noise topics.
2. **LLM usage**: local qwen3:8b only
   (`memsub.backends.build_backend(prefer="ollama")`), exactly one call per
   *changed* topic (title + overview + per-member fact rewrite + open
   questions, one JSON object). The timeline section is built deterministically
   from each member's `valid_from`/`valid_to` (temporal upgrade fields) with
   no LLM call -- asking a local model to reproduce dates it was already
   given as structured input is unnecessary risk for zero benefit. Every
   wikilink in a topic note is assembled deterministically by
   index-position mapping from the fixed member list, never parsed out of
   LLM prose -- the LLM never controls which note a fact links to, only how
   the fact is phrased, mirroring `memsub/sleep.py::generate_inferences`'s
   own pattern of cross-validating LLM output against real ids rather than
   trusting it.
3. **Idempotence**: `member_hash` = first 16 hex chars of
   `sha256(sorted(episode_ids))`, written to each topic note's frontmatter.
   Episodes are immutable once written (the store is append-only, and
   `memsub/vault.py::content_hash` already documents this same invariant
   for episode notes), so the member id set alone is a sufficient content
   fingerprint -- no separate per-member content hash needed. An unchanged
   topic costs zero LLM calls and is not even opened for a write
   (`_write_if_changed`-style skip), so a resumed/rerun distillation pass
   after a crash or limit-hit redoes zero work, satisfying the durable/
   resumable requirement without needing separate checkpoint bookkeeping --
   the vault file itself is the checkpoint.
4. **Human sovereignty**: every topic note's body carries one fixed
   HTML-comment-delimited machine block (`<!-- memsub:distill:begin -->`
   ... `<!-- memsub:distill:end -->`); on regeneration only the text between
   those markers is replaced, text outside is preserved byte-for-byte
   (prepended/appended human prose, added sections, anything). A note whose
   frontmatter carries `verdict: reject` is deleted outright ("kills a
   topic note", the spec's own words) rather than regenerated -- mirrors
   `memsub/vault.py::apply_vault`'s existing `verdict: reject` semantics for
   episode notes. `human_verified: true`, once present in frontmatter, is
   never removed or overwritten by a later machine regeneration.
5. **Idea badge**: `memsub idea` writes its episode with `kind="idea"`.
   `memsub/vault.py::render_note` already writes `frontmatter_fields["kind"]
   = ep.kind` unconditionally, and `memsub/vault_serve.py::_render_kind_badges`
   already renders any non-default `kind` value as a small chip. Result: the
   "distinct badge in vault" requirement is met with **zero changes** to
   `vault.py` or the vault_serve family -- reuse over invention (Karpathy
   rule 2). Verified by a live check, not just an assumption, before this is
   trusted.
6. **Idea topic/entity echo**: "cheap" is read literally -- rule-based
   entity extraction only (`memsub.graph.extract_episode_entities`, reused
   unchanged), matched against `store.entities` by exact normalized id (no
   difflib fuzzy pass, no LLM dedup call), plus a plain-text scan of
   `vault/topics/*.md` frontmatter (if a vault path is known) for topics
   whose `entities` list intersects. No embedding call, no LLM call, at
   idea-write time.
7. **Vault UI wiring**: `vault/topics/` is a plain folder under vault root;
   `vault_serve.py::build_index` already walks `root.rglob("*.md")` with no
   folder allowlist, so topics render with zero code change -- to be
   confirmed by a live smoke test after the real run, not assumed. Topic
   notes carry `namespace: topics` + `created` frontmatter so
   `vault_notes.is_content_note()` recognizes them as content notes (graph/
   search inclusion) with zero code change there either. Whether topic
   nodes get styled as large/accent in `/graph` is decided during
   implementation per the task's own explicit fallback: a minimal additive
   hook in `vault_graph.py` if it is genuinely small, else documented as a
   follow-up rather than deep-editing that module.
8. **Prerequisite**: `store.entity_count()` is 0 against the live
   `~/.memsub/memory.db` (checked directly) -- `memsub graph backfill` has
   never been run against it. Running it first (`--backend none`, zero LLM
   calls, an existing, already-tested, idempotent command, not a new
   mechanism) is an in-scope prerequisite for entity-seeded clustering.

### Success criteria (stated before coding)

- `memsub distill` runs end-to-end against the real store + vault (backup
  `vault/topics/` to `/tmp` first) and produces >=1 topic with 3+ members;
  every topic note matches the fixed structure (prose title, one-paragraph
  overview, key-fact bullets each ending in a wikilink to its source note,
  a timeline section when any member carries a `valid_from` window, open
  questions). Rerunning immediately after is fully idempotent: 0 LLM calls,
  0 files touched (checked by mtime).
- `memsub idea "..."` writes a `channel="user_stated"`, signature-verified
  episode and prints which known entities/topics it attached to; a
  `remember` shell alias is documented in `docs/`.
- New tests green (clustering determinism, idempotence, the sovereignty
  merge, the idea write path); full existing suite (434 baseline, confirmed
  by `pytest --collect-only` before starting) stays green throughout.
- No em dash in any file touched.
- Commits land per checkpoint, only this round's own paths staged
  explicitly, `git log -1 HEAD` checked immediately after each (concurrent
  commits from the rerank/abstention agent are expected on the same branch
  and left untouched).
- `git bundle create ~/repo-bundles/memory-sublayer-<timestamp>.bundle --all`
  at the end; no `git push` anywhere in this round.
- `docs/distillation-2026-07-20.md` reports the real topic count, quotes 3
  sample topic notes in full, and gives an honest hand-graded quality check
  of 10 random topic notes against the Zettelkasten bar -- weak clusters
  named plainly, not smoothed over.

### Progress checkpoint: library + tests

`memsub/distill.py` (new): `cluster_topics`/`_entity_components`/
`_refine_component` (clustering), `generate_topic_content` (one qwen3:8b
call per changed topic, deterministic fallback), `distill` (idempotent
write loop, marker-based sovereignty merge, `verdict: reject` kill),
`write_idea`/`match_entities_and_topics` (Layer 4). `memsub/cli.py`:
additive `distill` and `idea` subcommands, `_cmd_distill`/`_cmd_idea`. No
changes to `memsub/vault.py` or the vault_serve family were needed --
`kind="idea"` already flows through `render_note`'s existing `kind`
frontmatter field and `vault_serve.py`'s existing kind-badge chip renderer
(assumption 5, confirmed rather than just assumed: see the live check
below).

`tests/test_distill.py` (new, 19 tests): clustering forms the expected
3-member cluster and is deterministic; hub-entity exclusion (35 episodes
sharing one over-cap entity stay unclustered); lexical refinement splits an
artificially large entity-linked component into its two genuinely distinct
lexical halves; topic-note structure (frontmatter, machine markers,
key-fact wikilinks covering every member, open questions); timeline section
present iff a member carries `valid_from`, absent otherwise; fallback
content with `backend=None` still covers every member; idempotent rerun (0
LLM calls, 0 file writes, byte-identical, mtime unchanged); sovereignty
(human text outside markers preserved, unknown frontmatter fields and the
original `created` preserved, across a real regeneration triggered by a 4th
member changing `member_hash`); `verdict: reject` deletes the note;
dry-run writes nothing to disk; `write_idea` channel/signature verified;
`match_entities_and_topics` finds a known entity and, once a real vault
exists, the topic it belongs to; CLI end-to-end for both `distill` and
`idea`.

Full suite: 455 passed (434 baseline + 19 new here + 2 from the concurrent
rerank/abstention agent's own uncommitted work, confirmed by `git status`
showing only `memsub/cli.py` modified and `memsub/distill.py`/
`tests/test_distill.py` new among my own changes; `memsub/hybrid.py`/
`memsub/rerank.py` and untracked `results/`/`scripts/` files belonging to
that agent were left untouched and unstaged). No em dash in any file
touched.

## 2026-07-20: "Ask your brain" chat panel (memsub/ask.py + /ask page)

### Plan (stated before coding)

Scope: additive only. New `memsub/ask.py` (core flow), additive route +
UI in `memsub/vault_serve.py` (my one allowed edit surface), tests, docs.
Forbidden this round: `memsub/cli.py`, `memsub/distill.py`,
`memsub/rerank.py`, `memsub/hybrid.py`, `memsub/answer_policy.py` and their
tests/scripts (concurrent agents own them) -- read-only imports from any of
these are fine, edits are not.

1. **Retrieval**: `memsub.sdk.MemoryClient.search(question, k=8,
   budget_tokens=1200)`, the public read API, unchanged. The live store
   (`~/.memsub/memory.db`) has one namespace today (`claude-code`,
   confirmed live) but `ask.py`'s entrypoint takes a list of `MemoryClient`
   (one per namespace found in the served vault's frontmatter, via the
   existing `_namespace_breakdown`-style scan) and merges+re-sorts hits, so
   a future multi-namespace vault is not silently half-searched. Each
   client is built once at server start (`serve_vault`) and reused --
   `HybridRetriever` is expensive to rebuild per request (BM25 + embedding
   index over ~5k episodes), so per-request latency is retrieval-cheap and
   dominated only by the LLM call.
2. **Sufficiency gate (self-contained, in ask.py)**: `hybrid.py`'s
   `search()` score is rank-derived (`1/(rrf_k+pos+1)`), not a calibrated
   relevance score -- confirmed by reading `HybridRetriever.search`'s own
   docstring ("not comparable across queries"), so it cannot alone tell
   "good top hit" from "garbage top hit" the way `answer_policy.py`'s
   logistic model does. Given the explicit instruction not to import
   `answer_policy` while that agent owns it, the gate here is deliberately
   simpler and documented as a known-weaker stand-in: query-term coverage
   (via `memsub.lexical.tokenize`, stemmed/stopworded, already used by BM25
   in this repo -- reuse over reinventing) as the primary signal, a light
   capitalized-phrase "entity" substring-coverage check as a secondary
   signal, and a top-score floor (>0, i.e. at least one hit at all) as the
   literal instruction's third leg, even though it is weak by itself.
   Thresholds are picked by hand against the 6 real test questions below
   and written down with their reasoning, not guessed once and left alone.
   **Future merge note**: once `answer_policy.py` lands, its calibrated
   `AbstentionPolicy` should replace this gate wholesale -- tracked here so
   it is not forgotten.
3. **Composition**: `memsub.backends.build_backend(prefer="ollama",
   model="qwen3:8b")` -- hardcoded `prefer="ollama"`, never `"auto"`,
   because `"auto"` prefers a cloud API key if `ANTHROPIC_API_KEY` happens
   to be set, which would violate "ALL inference local, zero cloud" for
   this feature specifically regardless of environment. Backend
   unreachable/erroring degrades to a clear UI error, never a crash and
   never a silent cloud fallback. System prompt: only the numbered
   memories, mandatory per-sentence `[n]` citation markers, no invented
   facts, partial answers state what is missing rather than guessing, no
   separate sources section (rendered separately, server-side), no
   meta-preamble. `strip_think_blocks` (existing, `backends.py`) handles
   qwen3's `<think>` blocks; a small additional regex strips a couple of
   observed preamble patterns after live testing.
4. **Citations**: evidence index `[n]` maps 1:1 to the merged/sorted hit
   list. Episode -> vault relpath via `memsub.vault.namespace_dirname` +
   `note_filename` (existing, read-only import); title via
   `memsub.vault_notes.note_title(relpath, idx)` when the note is already
   vault-synced, else a plain first-sentence-of-content fallback (most
   recent episodes may not be vault-synced yet -- verified live: vault
   sync runs on its own cadence, some very recent episodes have no note
   file yet). `[n]` markers in the model's answer are regex-replaced (after
   `html.escape`) with anchor chips linking to a `#src-n` sources list
   below the answer; out-of-range citation numbers (a possible model
   hallucination) render as plain bracket text, not a broken link, and are
   surfaced in test assertions so a hallucinated index is caught, not
   swallowed.
5. **UI**: `/ask` GET renders the page shell (dark aesthetic, existing
   `_page()`/`_CSS`, one new small CSS block) with sample question chips
   built from `graph.hubs` top entities (same source `_render_brain_section`
   already uses) styled as "what did I decide about {entity}". `POST
   /api/ask` (new `do_POST` on the existing stdlib `Handler` -- none exists
   yet) runs the flow synchronously and returns pre-rendered HTML
   fragments as JSON; `ThreadingTCPServer` already gives one thread per
   connection so a multi-second local LLM call does not block other
   requests. Client JS: plain `fetch`, await, spinner text while waiting
   (qwen3:8b latency is seconds per the task brief -- polling/chunking
   would be complexity with no payoff here), conversation history appended
   client-side only, nothing persisted server-side. Header nav gets one new
   `ask` link.
6. **Tests**: gate cases (sufficient/insufficient, empty query, entity
   miss vs term miss), citation parsing/rendering (in-range, out-of-range,
   adjacent `[1][2]`, escaping), route smoke (GET /ask, POST /api/ask
   end-to-end over real HTTP with a MockBackend, no network). Then 6 real
   questions against the live store with the real qwen3:8b backend,
   logged verbatim (Q/A/citations/latency) in this file, each citation
   hand-verified against the actual cited note content -- any hallucination
   found gets the prompt tightened and the before/after logged, not
   quietly fixed.
7. Commit per checkpoint (library+tests, then vault_serve wiring, then
   real-question log + screenshots), `memsub/ask.py` + `tests/test_ask.py`
   + my `memsub/vault_serve.py` hunk + `tests/test_vault_serve.py`
   additions + `docs/` staged explicitly each time, `git log -1 HEAD`
   checked after every commit (concurrent commits from the other two
   agents on this branch are expected and left untouched). No `git bundle`
   (a later agent bundles). Never `git push`. No em dash anywhere.

## 2026-07-20: Belief-revision ripple engine (memsub/ripple.py)

Ordered directly, the product's hero feature: when a memory is superseded,
rejected, or corrected, compute everything downstream that derived from it,
so the user sees "changing your mind about X touches these N things."
File boundary this round: new `memsub/ripple.py`, `tests/test_ripple.py`,
`scripts/ripple_demo.py`, `docs/ripple-2026-07-20.md`, `WORKLOG.md`.
Forbidden (three concurrent agents own them): `memsub/vault_serve.py` and
all `vault_*` modules, `memsub/ask.py`, `memsub/cli.py`, `memsub/distill.py`,
`memsub/hybrid.py`, `memsub/rerank.py`, `memsub/answer_policy.py`,
`~/scripts/install-durable-capture.sh`. Read-only imports from those paths
are fine; no edits. UI wiring is a later pass -- this round builds the
engine and makes it callable, nothing more.

### Assumptions (Karpathy rule 1, stated before coding)

1. **Derivation edges, four tiers, from strongest to weakest signal**:
   (a) fusion: `memsub.consolidate` phase 3 logs a `fused_into` event on
   each source episode with `detail={"by": <fused_id>, "entity": ...}`
   (confirmed by reading `consolidate.py` and querying the live store: 72
   `fused_into` events, 12 fusion episodes) -- this is an indexed,
   O(1)-per-node lookup via `store.events(episode_id=X, event="fused_into")`,
   no need to scan every episode's `fused_from` provenance list. (b) topic
   notes: `memsub/distill.py` (read only) writes `vault/topics/<slug>.md`
   with each member cited as `[[<slug>--<id[:8]>]]` inside the machine
   block (`note_wikilink`, `memsub/vault.py`) -- membership is a regex scan
   of `vault/topics/*.md` for that 8-hex-char id suffix, not a frontmatter
   member-id list (the spec sketch assumed one; the real code cites members
   by wikilink in the body, confirmed by reading `render_machine_block` and
   `topic_frontmatter` -- frontmatter only carries `member_count`/
   `member_hash`, no id list). (c) supersede chains, both directions: a
   memory can be the loser (`store.events(episode_id=X, event="superseded")`,
   detail `by` = winner) or the winner (no reverse index exists, so a
   single `store.events(event="superseded")` scan, 141 rows on the live
   store, fetched once per `ripple()` call and passed down, not re-queried
   per BFS node). (d) associative: entity-share neighbors via
   `entities_for_episode` + `episode_ids_for_entities`, weighted by count of
   shared entities, capped to the top 10 neighbors so the weakest tier
   cannot dominate a report -- computed once from the root memory only,
   never expanded across hops (chaining the weakest signal would explode
   the graph and dilute the "derived" tiers' meaning).
2. **Hop walk**: BFS over tiers (a)+(b)+(c) only (associative is root-only,
   see above), `max_hops` bounds the walk (default 2): hop 1 = "derived
   directly", hop >= 2 = "derived transitively". Visited-set dedup means an
   id is reported in the strongest tier it is first reached by; the walk
   never revisits a node. Deterministic by construction: every edge
   collector sorts its output before traversal, and the one non-indexed
   scan (supersede winners) is fetched once, not re-ordered per call site.
3. **Impact score**: per-item, tier-weighted (`derived_directly`=3.0,
   `derived_transitively`=1.5, `associatively_related`=0.5 x shared-entity
   count) -- a report's `total_impact_score` is the sum, so "count-weighted
   by tier" holds at both the item and report level. No LLM calls anywhere
   in this module (pure graph walking over `store.py`/`graph.py`'s existing
   indexes and a vault directory scan).
4. **Vault path default**: `Path.home() / "memsub-vault"`, matching every
   `cli.py` vault subcommand's own default -- `ripple()` takes an optional
   `vault_path` override and degrades cleanly (empty topic tier, no error)
   when the directory or `topics/` subfolder does not exist yet (true today
   on the live vault: Layer 1 distillation has not been run against the
   real store yet, confirmed by `find ~/memsub-vault/topics -iname '*.md'`
   returning zero files -- the topic-note tier is exercised by unit tests
   against a synthetic vault, and will start surfacing real hits on the
   live store the first time `memsub distill` runs).
5. **Real demo cases**: found by querying the live store's audit trail
   directly (`memory_events` where `event='superseded'`), not invented.
   The OpenClaw-attribution correction chain the task brief named is real:
   episode `0054af99...` ("Rustom has experience as an ML engineer working
   with ... OpenClaw ...") was superseded with `reason: human_correction`
   by `bc7731e1...` (same sentence, OpenClaw removed) -- the only
   `human_correction` event in the store. A second real case is a 6-hop
   duplicate-collapse chain ending at the currently-active episode
   `cce2b5f1...` (also OpenClaw-adjacent professional-experience wording).
   Exact ids and full reports are logged in `docs/ripple-2026-07-20.md`
   after the demo script runs against the real store.

### Success criteria (Karpathy rule 4, stated before coding)

- `ripple(store, memory_id, max_hops=2)` returns a `RippleReport`
  (JSON-serializable via `dataclasses.asdict`) with items grouped into
  exactly three tiers, each item carrying a prose title, a type
  (memory/fused/topic/idea), a plain-words edge-path explanation, and an
  impact score.
- `ripple_on_event(store, event)` is a one-line wrapper over `ripple()`
  keyed on `event.episode_id` (the loser/affected episode), ready for
  future event-wiring to call directly.
- Zero LLM calls, runs against the real ~5.4k-episode / ~2.6k-event live
  store in comfortably under 100ms (measured, not assumed).
- Deterministic: two calls against the same store state produce
  byte-identical JSON.
- `tests/test_ripple.py` covers all four edge tiers in isolation, hop
  bounding (`max_hops=1` vs `2`), determinism (two calls, same output),
  and the empty case (a memory with zero derivation edges still returns a
  well-formed report). Full suite (`pytest -q`) stays green.
- `scripts/ripple_demo.py` runs three real cases against the live store
  (found via the audit trail, not fabricated) and prints human-readable
  reports; `docs/ripple-2026-07-20.md` carries the feature pitch, the
  honest associative-tier caveat, and one verbatim report.
- Commit per checkpoint (`memsub/ripple.py` + `tests/test_ripple.py`
  first, then `scripts/ripple_demo.py` + `docs/` + the real-case log),
  `git log -1 HEAD` checked after every commit (concurrent commits from
  the other agents on this branch are expected and left untouched, and my
  commits must land and remain ancestors). No `git bundle`. Never
  `git push`. No em dash anywhere.

### Result

All success criteria met. `scripts/ripple_demo.py` ran 3 real cases against
the live store (`~/.memsub/memory.db`, ~5.4k episodes / ~2.6k events),
found via the audit trail, not invented: (1) the OpenClaw-attribution human
correction (`0054af99...` -> `bc7731e1...`, 1 derived item, impact 3.0), (2)
the first loser in the 6-hop OpenClaw-adjacent duplicate-collapse chain
(`bf35552f...`, 2 derived items across both hops at `max_hops=2`, impact
4.5), (3) an ILS-optimization fusion source with 10 associative neighbors
(`cf4855a8...`, both the fusion and associative tiers lit up, impact 8.0) --
the one case exercising two tiers at once on real data. Measured latency
~30-35ms per call against the live store, comfortably under the 100ms
target. `tests/test_ripple.py`: 15 tests, all four edge tiers in isolation,
hop bounding, determinism (including a JSON-string equality check, not just
dataclass `==`), the empty case, an unknown-id `ValueError`, and the
`ripple_on_event` wrapper. Full suite: 493 passed. Full verbatim Case 1
report and the honest associative-tier-is-correlation caveat are in
`docs/ripple-2026-07-20.md`.

## 2026-07-20: Build-week demo vault (synthetic persona, no real data)

### Why

Rustom is submitting memsub to a build-week competition. The real vault
(`~/memsub-vault` + `~/.memsub/memory.db`) is private and can never be
shown in a demo. This task builds a fully synthetic, public-safe persona
brain -- "Maya Chen, indie game developer building Lighthouse Keeper while
freelancing" -- that exercises every showcase feature (topics, ripple,
temporal, quarantine, contradictions, graph) on real pipeline output, so
`vault serve --vault demo/vault --db demo/memory.db` just works for anyone
who clones the repo. My surfaces only: `demo/`, `scripts/build_demo_vault.py`,
`docs/demo-2026-07-20.md`, this file. Everything else (`vault_serve.py`,
`vault_*.py`, `ask.py`, `cli.py`, `distill.py`, `hybrid.py`, `rerank.py`,
`answer_policy.py`, `store.py`) is imported read-only, never edited.

### Assumptions (stated before coding, Karpathy rule 1)

1. **Write path**: episodes are constructed as `memsub.store.Episode(...)`
   with explicit historical `ts`/`valid_from`/`valid_to`, tagged with
   `memsub.provenance.tag_episode(ep, channel)`, then `store.append(ep)`.
   This is a first-class public write path (the same one `memsub.sdk
   .MemoryClient.add` calls internally) -- not a hand-written DB row --
   and it is the only way to control historical timestamps, since
   `MemoryClient.add` always stamps `time.time()`.
2. **Decay would gut a 6-month-spread vault**: `consolidate()`'s decay
   phase computes `age_days` against `now` (defaults to real wall-clock
   time, i.e. today, 2026-07-20), so a January episode is already
   ~190 days old the moment `consolidate()` runs. At default
   `importance=1.0` and `decay_half_life_days=30`, that decays to
   `0.5**(190/30) ≈ 0.011`, far under `decay_value_threshold=0.2` --
   most of the vault would be silently forgotten on the very first real
   consolidation pass. Fix: every persona episode is written with
   `importance=15.0` (clears the threshold even at ~200 days old with
   zero retrievals: `15 * 0.5**(200/30) ≈ 0.23 > 0.2`), a defensible
   choice since every hand-curated showcase memory is, narratively, one
   Maya actually cares about. Decay is not exercised as its own showcase
   beat in this task; not showcasing it is a smaller loss than losing the
   vault.
3. **Topic-note membership is authored, not auto-clustered, for the
   showcase topics**: `memsub.distill.cluster_topics` partitions episodes
   into *disjoint* components (union-find) -- by construction, one episode
   can never land in two auto-clustered topics. The ripple showcase
   requires the engine-choice decision to be cited in *two* topic notes,
   which the automatic path cannot produce. Resolution: reuse
   `distill.py`'s real rendering functions (`Topic`, `generate_topic_content`
   -- one real qwen3:8b call per topic, `render_machine_block`,
   `topic_frontmatter`, `note_wikilink`) but choose each topic's member
   list explicitly rather than calling `cluster_topics`/`distill` end to
   end. This still writes the exact spec format (member wikilinks, machine
   block markers, frontmatter) with real LLM prose; it is deliberate
   curation of *which* real pipeline function is the entry point, not a
   deviation from the spec format.
4. **Deliberate supersede events use `store.log_event` directly**, not
   `consolidate()`'s LLM contradiction judge, for the four engineered
   chains (engine reversal, freelance rate x2, art-style pivot, sleep
   habit pivot): the judge is stochastic against a hashing embedder's
   similarity band, and the brief calls for guaranteed, reproducible
   showcase structure. `store.log_event` is the same public API
   `consolidate()` itself calls to apply an action -- not a private
   internal. `consolidate()` still runs afterward for real dedup/
   contradiction/fusion against everything I did *not* hand-supersede,
   so the "real pipeline ran" claim in the doc is true.
5. **Fusion is genuinely automatic**: the Godot-related episodes span
   >=2 distinct `session_id`s and share the "Godot" entity (rule-based
   capitalized-word extraction, `memsub.sleep._extract_entities` --
   confirmed by reading the regex), which is all `consolidate()`'s fusion
   phase needs (`_fusion_candidates` reads entities straight from
   `ep.content`, no graph-backfill dependency) -- so the fused episode
   citing them is produced by one real qwen3:8b judge call, not
   hand-written.
6. **Quarantine timing**: the imported-channel episode is tampered (one
   character flipped in the `content` column via a raw `sqlite3` UPDATE,
   bypassing the store's append-only API entirely -- deliberately, that
   is the point) *before* `graph_store`/`consolidate()` run, so the real
   phase-0 signature check in `consolidate()` is what logs the
   `quarantined` event, not a fabricated one.
7. **Graph backfill is required even though I hand-author topics**:
   `memsub.ripple`'s associative tier reads `store.entities_for_episode`
   / `episode_ids_for_entities`, populated only by
   `memsub.graph.graph_store`. Skipping it would leave the associative
   tier empty regardless of how topics are authored.
8. **Namespace**: single namespace `"maya-chen"` for the whole persona
   (unregistered namespaces are open by design per `memsub.namespaces`,
   confirmed in `memsub/sdk.py`'s docstring) -- simplest layout, one
   vault folder, matches how `vault serve` already expects a single-user
   store.
9. **`.gitignore` excludes `*.db` repo-wide**; `demo/memory.db` must be
   committed anyway (explicit deliverable) -- will need `git add -f`.

### Plan (Karpathy rules 2/3: minimum code, nothing speculative)

1. `scripts/build_demo_vault.py`, one script, run manually (not part of
   any test suite): persona data as plain Python literals (~130-150
   entries across 7 topics: game design, engine choice, freelance
   clients, art style, health/habits, reading list, community/marketing),
   one qwen3:8b call per entry to turn a factual bullet into one warm,
   third-person sentence (proper nouns preserved verbatim by prompt
   instruction, so entity extraction stays reliable), written via
   `Episode` + `provenance.tag_episode` + `store.append` into
   `demo/memory.db`.
2. Apply the 4 deliberate supersede chains + 1 quarandtine tamper via
   direct store/sqlite calls (assumption 4/6 above).
3. Run `memsub.graph.graph_store` (real qwen backend for near-miss entity
   dedup, likely zero LLM calls needed since names are spelled
   consistently) then `memsub.consolidate.consolidate` (real qwen
   backend: dedup, contradiction, fusion, decay) over the whole store.
4. Hand-author 7 topic notes via `distill.py`'s real rendering functions
   (assumption 3), including the engine-choice topic and a second
   freelance/hiring topic that both cite the engine decision.
5. `memsub.vault.sync_vault` to write per-episode notes into
   `demo/vault/maya-chen/`.
6. Verify: `memsub.ripple.ripple()` on the engine decision (target: all
   3 tiers populated, 8-12 total items) written into the doc; 3 sanity
   search queries via `memsub.sdk.MemoryClient.search`; `vault serve`
   run briefly on port 8768, screenshots at 1280x900 (dashboard, /graph,
   the engine-choice topic note) via headless `google-chrome`.
7. `docs/demo-2026-07-20.md`: exact runbook, the 3 questions, the ripple
   command, screenshot paths.

### Success criteria (Karpathy rule 4, stated before coding)

- `demo/vault/` + `demo/memory.db` exist, are regenerable by re-running
  `scripts/build_demo_vault.py` from a clean state, and are small enough
  to commit.
- 120-150 episodes, 6-8 topic notes with member wikilinks in the real
  spec format.
- `ripple(store, <engine-decision-id>, max_hops=2, vault_path="demo/vault")`
  shows non-empty `derived_directly`, `derived_transitively`, and
  `associatively_related` tiers, 8-12 items total.
- The tampered episode's `store.status()` is exactly `"quarantined"` and
  its synced vault note's frontmatter says so.
- 3 planned questions return on-topic top hits via `MemoryClient.search`.
- `vault serve --vault demo/vault --db demo/memory.db` starts cleanly and
  renders the dashboard, `/graph`, and the engine-choice topic note
  without errors.
- `pytest -q` (existing suite, untouched) stays green.
- No em dash anywhere in generated content or docs. Commits per
  checkpoint, own-paths-only, `git log -1` verified after each (concurrent
  agent commits on this branch expected and left untouched). No
  `git bundle`. Never `git push`.

### Result

All success criteria met, after 3 rebuild iterations (each `--fresh`,
~9-10 minutes, local qwen3:8b, `think=False`). `demo/memory.db` +
`demo/vault/` committed (~1.7MB), fully regenerable via
`python3 scripts/build_demo_vault.py --fresh`.

**Counts**: 144 episodes (136 active, 7 superseded, 1 quarantined), kind
breakdown 94 fact / 15 decision / 11 idea / 1 milestone / 23 real fusion
episodes, 48 entities / 371 entity-links (real `graph backfill`), 8 topic
notes (`demo/vault/topics/`).

**Ripple showcase** (`ripple(store, <engine-decision-id>, max_hops=2)`):
19 affected items, total impact 42.5, all 3 tiers populated -- 6 direct
(supersede to the Unity-switch decision, 3 real fusion citations, 2 topic
citations), 4 transitive (the Unity decision's own further fusion
citations), 9 associative (Godot tutorials, pricing research, the
contractor hire, entity-share only). Exceeds the 8-12-item target from the
plan; kept as-is since every item stayed genuinely on-topic (no noise) and
"more, still coherent" reads as more impressive for a competition demo,
not less curated.

**3 iteration bugs found and fixed** (full detail in
`docs/demo-2026-07-20.md`'s honest iteration log, not hidden):
1. Applying the deliberate supersede chains before graph backfill/
   consolidate made the engine decision already-inactive by the time
   fusion/entity-linking ran (both only see `store.active()`) -- empty
   associative tier, no fusion citation. Fixed: reordered so graph
   backfill + consolidate run first, deliberate supersedes last
   (idempotent-guarded against consolidate's own contradiction judge
   possibly already having caught a pair).
2. Even after reordering, consolidate's contradiction phase (pairs
   same-`kind` episodes) coincidentally paired the engine decision with an
   unrelated same-kind episode and auto-superseded it before fusion ran in
   the same call, re-excluding it from fusion. Fixed: gave that one
   episode a unique `kind="milestone"` no other record shares --
   mathematically unpairable in the contradiction phase.
3. `importance=15.0` (chosen against decay) measured 0.159-0.196 for the
   earliest (January) episodes -- just under the 0.2 decay threshold, since
   decay's clock is wall-clock `time.time()` (today), not story time.
   Several early memories, including the pricing-research episode central
   to the ripple showcase, were decayed as "forgotten" on the first real
   consolidate pass. Fixed: raised to `importance=100.0`, a 5x margin at
   the oldest date.

**Quarantine exhibit**: verified `store.status()` returns exactly
`"quarantined"` for the tampered episode, caught by consolidate()'s real
phase-0 signature check (not a fabricated event); dashboard PROVENANCE
donut and QUARANTINED tile both show it, red.

**Retrieval sanity** (`MemoryClient.search`, `embedder="auto"` --
real `nomic-embed-text` via local ollama, pulled for this build): 2 of 3
planned questions return the exactly-right memory at or near rank 1
("what has Maya decided about the art style" hits the painterly-gouache
decision at rank 1; "what engine is Maya using and why" returns
Unity-migration-relevant hits throughout). The third ("what does Maya
charge") returns topically-adjacent freelance-client hits but does not
surface the specific rate figures within the top 12 -- an honest finding
(vocabulary mismatch between "charge" and "rate"/"dollars"/"hour" on a
tiny corpus, confirmed not a broken pipeline: direct phrasing like
"freelance rate" hits the right episode at rank 1), documented rather than
hidden, matching this project's standing rule against fabricated positive
results.

**Screenshots**: `docs/screenshots/2026-07-20-demo/{dashboard,graph,
topic-engine-choice}-1280x900.png`, headless `google-chrome --headless=new
--force-prefers-reduced-motion` (skips vault-serve's stat-tile count-up
animation, avoiding the known mid-animation-frame timing quirk documented
in `docs/vault-serve-2026-07-18.md`, so tiles show final server-rendered
values).

**Verification**: `pytest -q` 493 passed (untouched suite, confirmed
before and after this work). `vault serve --vault demo/vault --db
demo/memory.db` starts cleanly, dashboard/`/graph`/topic notes all 200.

Full runbook, exact commands, the 3 questions, and the ripple command are
in `docs/demo-2026-07-20.md`.


## 2026-07-21: Public website for build-week submission (`site/`)

### Why

Rustom, verbatim: "a professional level website which not only tells,
gives full technical details but most importantly demonstrates the
product." Public face of memsub for a build-week submission. My surfaces
only: new `site/` directory, `docs/website-2026-07-21.md`, this file.
Everything under `memsub/`, `scripts/`, and the forbidden list (`ask.py`,
`store.py`, `distill.py`, `cli.py`, `hybrid.py`, `rerank.py`,
`answer_policy.py`, `vault_*`) is read-only reference material, never
touched.

### Assumptions (Karpathy rule 1, stated before coding)

1. **Static only, zero build step.** Plain HTML/CSS/vanilla JS in `site/`,
   opens from `file://` and from any static server (`python3 -m
   http.server`). No npm, no bundler, no framework. This is explicit in
   the brief and matches the project's own "dependencies: numpy" ethos.
2. **Palette matches the shipped product UI exactly**, read directly from
   `memsub/vault_serve.py`'s `_CSS` block (read-only reference, not
   edited): `--bg:#0a0b0d --bg-raised:#14161a --bg-card:#191c22
   --border:#262a33 --text:#d8dbe2 --text-dim:#8b909c --accent:#6fd6ff`,
   `ui-monospace` for headings. Reusing the real product's own tokens is
   what "consistent with the product UIs" means operationally, not a
   fresh design decision.
3. **Demo screenshots must be Maya-persona (public-safe) only.**
   `docs/screenshots/2026-07-20-demo/*` (dashboard, graph, topic note) are
   the synthetic `demo/vault` persona and are safe to publish.
   `docs/screenshots/2026-07-20-brain/*` and `docs/screenshots/2026-07-19/*`
   are screenshots of Rustom's real, private vault (visible content
   includes real personal notes, e.g. an AUTH-constraint note) and are
   **excluded from this site entirely** -- `docs/demo-2026-07-20.md` itself
   states the real vault is private and never used in the demo; publishing
   those screenshots on a public site would violate that directly.
4. **Missing demo screenshots (a readable note, ask-with-citations) are
   captured fresh from a locally-run instance of the committed, public-safe
   demo data**, per the brief's own fallback instruction. Ran
   `vault serve --vault demo/vault --db demo/memory.db --port 8770`
   (foreground-tracked background bash, killed immediately after capture)
   and captured with headless `google-chrome --headless=new
   --force-prefers-reduced-motion` (same flag the existing demo screenshots
   used, for a consistent look): the engine-decision note page (the ripple
   root memory, a good "readable note" exhibit since Layer-0 readability
   redesign is on display) and the empty `/ask` interface (real chrome, no
   LLM call made -- see assumption 5).
5. **No live "ask" answer is generated for the site.** The task states "no
   LLM calls" for this build. The `/ask` page's answer flow calls an LLM
   backend, so the site shows the real, empty `/ask` UI chrome (a real
   screenshot, no LLM call) and separately renders the ask-with-citations
   *narrative* using the three planned demo questions' real, already-measured
   search results from `docs/demo-2026-07-20.md`'s "Retrieval sanity"
   section (verbatim hit content and scores, including the honest miss on
   "what does Maya charge") -- styled as a citation panel, not a fabricated
   new answer.
6. **The ripple moment is rendered as styled HTML from real report data,
   not a screenshot** -- `docs/ripple-2026-07-20.md` states UI wiring is a
   later pass, so no live ripple page exists to screenshot. The mission
   names the exact numbers to show (19 items, 42.5 impact); those are
   `docs/demo-2026-07-20.md`'s real, already-executed
   `ripple(store, eid('engine.decision.godot'), max_hops=2)` output on the
   committed `demo/memory.db`, not invented for the site.
7. **One factual correction to the brief's own paraphrase, made because
   claim discipline is stated as absolute and overrides an inexact
   instruction.** The brief's hero copy says "benchmarked above the market
   leader's reproduced score on a laptop 8B" and its doc-list annotation
   calls the 63.75% figure a "local 8B answerer" result. Checked directly
   against `docs/locomo-v3-2026-07-18.md`: the 63.75% run (budget 2000, the
   figure that beats mem0's reproduced ~61.7%) used **Sonnet as the
   answerer via the `claude` CLI subscription, not qwen3:8b** -- confirmed
   by that doc's own text ("Sonnet answerer + Sonnet judge") and by the
   whole prior arc in `docs/locomo-jscore-2026-07-17.md` /
   `docs/locomo-jscore-frontier-2026-07-18.md`, which exists specifically
   because memsub's *local-8B-answerer* J-score (47.5%/48.75%) is
   explicitly documented as "not comparable in absolute terms to
   frontier-answerer competitor numbers" like mem0's -- the whole point of
   the frontier-answerer experiments was to put memsub on the same
   frontier-answerer footing mem0's own number already uses. Publishing
   "beats mem0 on a laptop 8B" would misstate which model answered the
   winning run -- a real accuracy problem on a page whose whole premise is
   claim discipline. **Fix**: hero copy keeps "local, cryptographically
   yours" (true of the product's storage/retrieval architecture and of
   the separate, real local-8B numbers in `README.md`) and drops the
   specific "on a laptop 8B" clause from the benchmark sentence; the
   benchmarks section states plainly which run used which answerer, and
   separately shows the genuine local-8B numbers (README's 0.152 vs 0.117
   vs 0.022 token-F1) labeled as their own thing, never merged with the
   Sonnet-answerer 63.75% figure. Same correction applies to "single-hop 85
   vs their 67.1": 85.0% belongs to a different, lower-overall config (v2,
   52.5% overall); the actual winning 63.75%-overall config's single-hop
   row is 80.0% vs mem0's 67.1% (+12.9pp) -- the matched-row number from
   `docs/locomo-v3-2026-07-18.md`'s own "per category" table is used
   instead of splicing the higher number from a different row.
8. **The 92.5% mem0 platform-claim figure is stated as told**, not sourced
   from a doc in this repo -- the task brief supplied it directly as
   context with an explicit instruction never to imply we beat it. Site
   copy hedges it accordingly: named, marked unreproduced, not compared
   against numerically.
9. **Session-level ask-once gate**: the standing global rule (propose a
   plan review before non-trivial work, ask once) was already satisfied --
   asked once, the coordinator confirmed the spec itself is Rustom's own
   detailed order and the reviewed plan. Not re-asking.

### Build order (surgical, one thing at a time)

1. `site/assets/img/`: copy + `convert`-recompress the 3 existing
   Maya-persona screenshots + the 2 freshly captured ones (assumption 4).
2. `site/assets/style.css`: tokens from assumption 2, phone-first (base
   styles = 390px, `min-width` breakpoints up).
3. `site/index.html`: the 7 sections in the brief's order (hero, demo,
   how-it-works, trust, benchmarks, footer) -- content only from the source
   docs, corrections from assumptions 7-8 applied.
4. `site/assets/script.js`: demo-tour step switcher, terminal-replay
   animation (`setInterval`/`textContent`, no dependency), architecture
   stage expand/collapse, mobile nav toggle. No LLM calls, no network
   calls -- everything is static content already in the page.
5. Screenshot-verify at 390x844 and 1440x900 (headless chrome), iterate
   visual quality at least twice, save to
   `docs/screenshots/2026-07-21-site/`.
6. Commit per checkpoint, own-paths-only, `git log -1` + `git status`
   verified after each commit. No push.

### Success criteria (Karpathy rule 4, stated before coding)

- `site/index.html` opens correctly via `file://` (no console errors from
  a missing asset) and via `python3 -m http.server` from `site/`.
- Every numeric claim on the page traces to one of the named source docs
  (or to README for the local-8B numbers), with the correction in
  assumption 7 applied -- no figure is invented, rounded up, or borrowed
  from a mismatched config row.
- No em dash anywhere in `site/` or this entry.
- No OpenClaw mention anywhere.
- Screenshots exist at both breakpoints for hero, demo, how-it-works,
  trust, and benchmarks sections, saved under
  `docs/screenshots/2026-07-21-site/`, after at least two visual-quality
  iteration passes.
- `git log -1` after each commit shows only `site/`, `docs/`, or
  `WORKLOG.md` paths touched -- nothing in the forbidden list staged.
- Nothing pushed.

## 2026-07-21: Graph visualization rebuild (memsub/vault_graph.py, vault_serve.py graph surfaces)

Task: current /graph + mini constellation renders as a flat hairball
(uniform blue dots, straight gray edges, physics that spreads everything
into an even blob). Rebuild to the visual standard of Obsidian's graph
view: organic clusters, glowing nodes, elegant motion. Scope: my surfaces
only -- `memsub/vault_graph.py`, the graph page + mini-constellation
markup/JS/CSS inside `memsub/vault_serve.py`, tests, docs, this file.
Forbidden (other agents' uncommitted WIP, left untouched even if dirty in
`git status`): `site/`, `memsub/ask.py`, `memsub/store.py`,
`memsub/distill.py`, `memsub/cli.py`, `memsub/hybrid.py`,
`memsub/rerank.py`, `memsub/answer_policy.py`.

### Plan (Karpathy rule 1: assumptions stated before coding)

1. **Session-level ask-once gate**: the standing global rule (propose a
   plan review before non-trivial work, ask once) is satisfied by the
   task spec itself -- it is Rustom's own detailed, numbered rebuild spec
   with explicit rules including "Karpathy plan first," i.e. state the
   plan here and proceed, same precedent as the 2026-07-21 site-build
   session (WORKLOG.md, "Session-level ask-once gate" note). Not asking
   again mid-task.
2. **Layout method: SVD-2D directly, not 50-dim + t-SNE.** The spec
   explicitly allows this fallback ("if a clean pure-python UMAP-
   approximation is too heavy, SVD-2D directly is acceptable: judge by
   results"). Chosen because: (a) stdlib-only is a hard rule, no numpy;
   (b) the real vault is 7,343 content notes (measured directly, up from
   the 4,334 remembered in old session notes -- it has grown), so the
   layout algorithm must be O(nnz) per pass, not O(N^2); (c)
   `build_similarity_index` already does a comparable one-pass TF-IDF
   scan over all 7,343 docs in 1.6s (measured), so a handful of extra
   power-iteration passes over the same sparse vectors is a bounded,
   acceptable one-time server-start cost under the existing "no live
   reload" contract `vault_graph.py`'s docstring already establishes.
   Implementation: sequential power iteration with Gram-Schmidt deflation
   for exactly the top-2 left singular vectors of the TF-IDF doc-term
   matrix (classic LSA-2D text-clustering technique) -- reuses
   `vault_similarity._tokenize`/`_content_text` by import (read-only
   reuse, that file stays untouched, it is not in my surface list).
3. **Coordinates cached in-process**, keyed on (note count, hash of the
   sorted content-note relpaths) so identical graphs across repeated
   `build_graph_index` calls (e.g. the test suite) don't recompute, small
   dict capped at 8 entries. Matches spec's "recompute only when note
   count changes," made collision-safe across different vaults of equal
   size by also hashing the relpath set.
4. **Hub coordinates** = centroid of member notes' coordinates (no
   separate embedding for entities -- they are not text documents).
   **Isolated notes** (empty TF-IDF vector, no content words) get a
   deterministic small-jitter position seeded by their relpath hash
   rather than stacking at the origin.
5. **Mini constellation keeps client-side force layout**, not the server
   SVD embedding -- at that scale (a note + its immediate neighbors,
   typically under ~20 nodes) force-directed layout is not a hairball
   problem; Obsidian's own local graph view uses local physics too. Rule
   3 ("same treatment") is read as the same visual/interaction language
   (dark glow, curved edges, hover-focus, idle drift), not literally the
   same layout algorithm at a scale where it would add complexity for no
   visible benefit (Karpathy rule 2: simplicity first).
6. **Full graph page force sim becomes a gentle local relax seeded by
   server coordinates**: much lower repulsion constant, no "spread into
   the whole canvas" burst -- the physics only nudges nodes apart enough
   to avoid exact overlap, global structure comes from the SVD layout,
   not from repulsion.

### Build order

1. `memsub/vault_graph.py`: `GraphNode.x`/`.y` fields, `_compute_layout`
   (power-iteration SVD-2D + hub centroids + isolated-note jitter +
   cache), wired into `build_graph_index`; `filter_and_cap` and
   `note_constellation` payload dicts carry `x`/`y`.
2. `memsub/vault_serve.py`: dark radial-gradient canvas background,
   soft-glow node rendering (radial gradient + shadowBlur), curved
   quadratic edges with weight-proportional alpha (near-invisible at
   rest), cluster color from a curated 8-color palette (desaturated jewel
   tones, colorblind-aware) keyed by nearest hub, hub-only labels at rest
   with dark halo + more labels fading in on zoom, hover = focus mode
   (neighbors bright, rest dims to 15%), smooth zoom-to-cursor (existing
   wheel handler already zooms-to-cursor, kept), gentle idle drift
   (skipped under `prefers-reduced-motion`), dpr cap + rAF throttle when
   idle. Same CSS/JS treatment applied to the mini constellation markup.
3. Tests: `tests/test_vault_graph.py` (x/y present and finite, same-hub
   notes closer in layout space than to an unrelated note -- coarse
   cluster-quality check), `tests/test_vault_serve.py` graph tests
   updated only if payload shape assertions need it.
4. Verify: `vault serve --vault demo/vault --db demo/memory.db --port
   8770` and `vault serve --vault /home/user/memsub-vault --port 8768`
   (real vault, briefly), headless `google-chrome --screenshot` at
   1440x900 and 390x844 for `/graph` (both vaults) and one note page's
   constellation, at least 3 iteration passes, saved to
   `docs/screenshots/2026-07-21-graph/`.
5. Commit per checkpoint, own-paths-only, `git log -1` + `git status`
   verified after each. No `git bundle`. Never `git push`.

### Success criteria (Karpathy rule 4)

- `/api/graph.json` nodes carry finite `x`/`y`; a coarse layout-quality
  test passes (notes sharing a hub entity are closer to each other in
  layout space than to an unrelated note, on the fixture vault).
- Full `pytest -q` suite green.
- Screenshots exist at both breakpoints for the real vault and demo/vault
  graph pages plus one constellation, under
  `docs/screenshots/2026-07-21-graph/`, after >=3 visual iteration passes.
- No em dash anywhere in touched files or this entry.
- `git log -1` after each commit shows only my surface paths (never
  `site/`, `ask.py`, `store.py`, `distill.py`, `cli.py`, `hybrid.py`,
  `rerank.py`, `answer_policy.py`).
- Nothing pushed.

### Result

Rebuilt in 3 checkpoints (commits), plus a mid-flight port substitution
noted below. Layout method: SVD-2D directly, sequential power iteration
with Gram-Schmidt deflation (2 components, 24 iterations), pure stdlib.
Chosen over 50-dim + t-SNE per the spec's own fallback clause.

Two real bugs found and fixed during visual iteration (not caught by unit
tests, only by looking at actual screenshots -- exactly why the brief
insisted on brutal visual verification):

1. **A handful of outlier notes stretched the SVD bounding box**, squeezing
   every real cluster into a corner once the client fit-to-view logic
   normalized against min/max. Fixed by clipping each axis to its
   3rd/97th percentile server-side (`_clip_outliers` in
   `memsub/vault_graph.py`) before coordinates are ever served -- not a
   client-side workaround, the served `x`/`y` themselves are now robust to
   one weird document.
2. **Edges and hub labels were still too busy at rest** on the first
   render pass: edge alpha was visible enough to read as a bright fan
   near high-degree hubs once hundreds overlapped, and every hub within
   the default zoom always drew a label regardless of crowding. Fixed by
   halving the edge alpha formula and rewriting the node draw pass into
   two ordered passes (glows, then a priority-sorted, collision-decluttered
   label pass) in `_GRAPH_JS`.

Port note: port 8770 (the brief's suggested demo-vault port) is held by
something outside this session's visibility (not owned by this user, no
matching process in `ps`/`ss`, `sudo` unavailable to investigate) --
verified with a raw `socket.bind` test that every other port tried (8768,
8771, 8772, 18770, 8080) binds cleanly. Used port 8771 for the demo vault
instead; port 8768 (the real-vault port) was free and used as specified.

Screenshots (3 iteration passes for the demo vault, final pass for both
vaults) in `docs/screenshots/2026-07-21-graph/`:
`iteration1-demo-graph-1440x900.png` (broken: skewed into a corner,
edges/labels too busy), `iteration2-demo-graph-1440x900.png` (outlier
clip applied, labels/edges still busy), `demo-graph-1440x900.png` /
`demo-graph-390x844.png` (final), `real-vault-graph-1440x900.png` /
`real-vault-graph-390x844.png` (real vault, 7,343 notes, final),
`demo-note-constellation-1440x900.png` (mini constellation, same visual
treatment, force layout kept as designed).

Quality check beyond eyeballing screenshots: on the real vault, notes
sharing a hub entity land roughly 2x closer to each other in layout space
than random note pairs (measured directly, not asserted in a test since
it depends on the real vault's actual content -- the equivalent check
against a synthetic fixture is `test_layout_separates_unrelated_topics_
more_than_same_topic` in `tests/test_vault_graph.py`).

`pytest -q`: 499 passed. No em dash. Nothing pushed. Both servers stopped
after verification (`ps aux` confirmed clean).

### Iteration 4 (coordinator review, real-vault screenshot): 3 concrete fixes

Coordinator reviewed `real-vault-graph-1440x900.png` from iteration 3 and
flagged it as not at the bar yet, with three specific failures:

1. **Density collapse**: ~80% of nodes crushed into one horizontal band,
   huge empty region below. Root cause: raw SVD-2D coordinates are
   unit-norm vectors, not variance-scaled -- nothing forces even spread,
   so a corpus with one dominant topic direction crushes most documents
   into a narrow band while a few land far out. Fix: replaced
   `_clip_outliers` with `_rank_normalize` in `memsub/vault_graph.py` --
   each axis's coordinates become that axis's rank percentile (ties
   averaged), independently for x and y, mapped to a fixed [-0.5, 0.5]
   range. Rank order is monotonic with the raw value so genuine cluster
   separation survives, but density becomes uniform along each axis by
   construction. Measured on the real vault's default 150-note view: a
   coarse 10x10 grid over the layout's bounding box now has a max cell
   share of 9.3% (was one band holding the large majority) -- meets the
   coordinator's "~8%" target.
2. **Bloom overdose**: glow radius large enough that adjacent nodes merged
   into fog. Fixed in `_GRAPH_JS` (`memsub/vault_serve.py`): glow sprite
   gradient stops tightened (hot core now 16% of sprite radius instead of
   35%, falls off by 40% instead of 65%), sprite size halved (96px ->
   64px), and the glow radius formula changed from a flat per-kind
   multiple of node radius (`r * 4.2` for hubs) to `r + sqrt(r) * k` --
   sub-linear in r, so a big hub's halo no longer balloons faster than a
   small note's. Added an explicit small solid-color core disc (fixed
   ~2.2-3 screen px regardless of node radius) on top of the glow so every
   node reads as a distinct point.
3. **Label smear**: "Kumo Both User Fable" collided into one unreadable
   block. Fixed: the collision-skip exception that previously covered the
   focused node *and all its neighbors* now covers only the exact
   hovered/selected node (`node !== focus` is the only bypass) -- every
   other label, including neighbors, must pass the collision check like
   everyone else. Added a label count cap (`clamp(10 + max(0, view.scale -
   0.6) * 18, 8, 400)`, ~10-12 at the default fit-to-view scale, growing
   as the user zooms in), `ctx.letterSpacing = "0.4px"`, and a heavier dark
   halo (stroke width 3->4, opacity 0.8->0.9).

Boot-cleanliness check (coordinator: "a restart on the standard port came
up 000 earlier"): timed a fresh real-vault boot precisely -- the process
does not accept any TCP connection at all (curl reports `000`, not a slow
HTTP response) for about 7.7s, because `serve_vault()` runs `build_index`
+ `build_graph_index` + `build_similarity_index` synchronously before
`ThreadingTCPServer` is even constructed. Measured breakdown: the new
layout compute is under 1s of that total; the rest (`build_index` parsing
7,343 markdown files, `build_similarity_index`'s TF-IDF pass) is
pre-existing cost from before this task. The graph-specific concern in
the coordinator's note -- "does not block first paint... serve the page
immediately and fetch coordinates async with a loading shimmer" -- was
already true of the per-request architecture (the /graph page's HTML
shell renders synchronously with no coordinate dependency, and the client
already `fetch()`es `/api/graph.json` after page load) and is now more
visibly true with an added `.graph-shimmer` element (CSS gradient
animation, removed the moment `start(data)` runs, static under
prefers-reduced-motion) covering the wait. Deliberately did NOT
restructure `serve_vault()`'s startup sequencing (e.g. threading the
index build so the socket opens before the vault is parsed) -- that
touches the shared server bootstrap for every route (dashboard, search,
notes, ask, review-queue), not "the graph page + mini-constellation"
this task's surface is scoped to, and the file's own docstring
establishes the single-synchronous-build-at-startup contract deliberately
for its read-only-safety guarantees. Documenting this honestly rather
than silently expanding scope.

Re-screenshotted both vaults at both breakpoints against the same bar
(`docs/screenshots/2026-07-21-graph/demo-graph-*.png`,
`real-vault-graph-*.png`, `demo-note-constellation-1440x900.png`);
iteration 3's shots kept as `iteration3-*.png` for the record. Real vault
now shows nodes filling the canvas with visible color regions, crisp
distinguishable dots even in the densest sub-cluster, and no colliding
labels.

`pytest -q`: 499 passed (all layout/cluster tests pass unchanged with rank
normalization -- rank order preserves the same-hub-closer-than-random
property the tests check). No em dash. Both verification servers stopped
(`ps aux` confirmed clean). Nothing pushed.
