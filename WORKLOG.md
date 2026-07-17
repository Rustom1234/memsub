
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
  - (this checkpoint) eval script, results file, docs writeup, WORKLOG

---
