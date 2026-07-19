# External benchmark: LongMemEval_S

Status: pre-registration written 2026-07-12 before the frozen run; frozen run
executed 2026-07-12 immediately after (wall time 1009.3s). Every number in the
Results section comes from that single executed run
(`results/longmemeval-frozen.json`).

## Why LongMemEval_S

`docs/benchmark.md` and `docs/metrics-local.md` measure memsub against
synthetic ground truth and LOCOMO. LongMemEval (Wu et al., ICLR 2025,
arXiv:2410.10813) is a second, independently-authored long-term-memory QA
benchmark with a different haystack format (per-question independent session
history, not one shared conversation) and six question types (single-session
x3, multi-session, temporal-reasoning, knowledge-update). Running it is a
cross-check that the retrieval-selection story in this repo is not an
artifact of LOCOMO's specific structure. `_S` is the 500-question variant
(the paper also has a larger `_M` variant); this repo uses the "cleaned"
2025/09 re-release, downloaded to `data/longmemeval_s_cleaned.json`
(gitignored, 500 items, 470 non-abstention + 30 abstention).

Adapter: `memsub/longmemeval.py` (schema-validated haystack parser, turn-level
`Episode` ingestion, mirrors the LOCOMO loader's approach per its own
docstring). Judge: `memsub/judge.py` (Sonnet via the local `claude` CLI in
print mode, substituting for the GPT-4o judge used in the published
LongMemEval leaderboard; this is a deviation from the paper's grading model
and absolute judge-accuracy numbers here are not comparable to the
leaderboard for that reason, only the memsub/naive/no-memory ordering within
this run is).

## Dry timing run (n=5, `scripts/longmemeval_dry_run.py`, executed before pre-registration)

qwen3:8b local ollama, no judge (timing probe only, n too small for any
accuracy claim). Mean haystack size for these 5 items: 538 episodes (turns).

| condition | mean total_s | max total_s |
|---|---|---|
| no_memory | 1.939 | 3.443 |
| naive_topk | 3.637 | 4.769 |
| memsub_hybrid_v2 | 4.051 | 6.799 |

Separately, a single standalone Sonnet-CLI judge call (`memsub/judge.py`,
short compact prompt) was timed at 11.0s.

## Pre-registration (before the frozen run)

- **n = 5 non-abstention items per question type x 6 types = 30 items**,
  stratified, `random.Random(123)` deterministic shuffle per type (same
  `SEED = 123` convention as `scripts/holdout_answer_quality.py`). Abstention
  items (question_id ending `_abs`) are excluded from this run; abstention
  behavior is out of scope here and not silently folded into the accuracy
  numbers.
- **n is small.** 30 items (10 per condition-type cell) is a pilot, not a
  significance-tested benchmark, same caveat as the lying-game cells and the
  LOCOMO holdout (n=80 there). Read differences of a few points as
  directional.
- **Conditions**: `no_memory`, `naive_topk`, `memsub_hybrid_v2`, same three
  conditions and same `FROZEN` hybrid-v2 params as `docs/metrics-local.md`
  (no re-tuning on this dataset; the params were selected on LOCOMO dev and
  are held fixed here, which is itself a test of whether they generalize).
  `memsub_plus_sleep` is out of scope for this round (matches
  `scripts/judge_capture_holdout.py`'s stated scope).
- **Budget**: 1000 tokens (chars/4 approximation), same as all other local
  runs in this repo.
- **Grading**: token-F1 (deterministic, `scripts/metrics_local.token_f1`) and
  a Sonnet-CLI judge (`memsub/judge.py`, semantic correct/incorrect).
- **Timeouts, pre-registered from the dry-run numbers above**: 60s per answer
  call (dry-run max was 6.8s, ~9x headroom) and 60s per judge call (measured
  11.0s, ~5x headroom). A run that hits either timeout is reported as a
  timeout, not silently dropped or retried into a different number.
- **Backend**: qwen3:8b via local ollama for ingestion/retrieval/answering
  (same as `docs/metrics-local.md`); Sonnet via local `claude` CLI
  (subscription-billed, no API key) for judging (same as the lying-game
  experiment's backend).

Frozen run script: `scripts/longmemeval_frozen.py`. Output:
`results/longmemeval-frozen.json`.

## Results (frozen run, executed 2026-07-12, wall time 1009.3s)

n = 30 items per condition (5 per question type), qwen3:8b answerer,
Sonnet-CLI judge. No answer or judge call hit its pre-registered 60s timeout
(max observed: 14.9s answer, 36.4s judge). 0 of 90 judge responses were
malformed.

| condition | n | mean token-F1 | judge accuracy |
|---|---|---|---|
| no_memory | 30 | 0.0788 | 0.0333 (1/30) |
| naive_topk | 30 | 0.2063 | 0.5000 (15/30) |
| memsub_hybrid_v2 | 30 | 0.2377 | 0.4667 (14/30) |

**Honest verdict.** Both memory conditions crush no-memory, as expected (the
haystack averages 487 turn-episodes over the 30 sampled items; without
retrieval the model has nothing
to work with). Between the two memory systems the result is a split at this
n: memsub_hybrid_v2 leads naive_topk on token-F1 (0.238 vs 0.206, +15%
relative), but the semantic judge scores them a statistical tie, 14/30 vs
15/30, a one-item difference in naive's favor. At n=30 per condition neither
gap supports a superiority claim in either direction. The correct summary is:
the LOCOMO-selected frozen parameters transferred to an unseen benchmark
without collapsing (memsub did not underperform the naive baseline), and no
more than that. This is weaker than the LOCOMO holdout result (memsub 0.152
vs naive 0.117 token-F1, n=80), and both are reported.

### Per question type (n = 5 per cell; directional at most, single items move these numbers)

| question type | no_memory F1 / judge | naive_topk F1 / judge | memsub F1 / judge |
|---|---|---|---|
| knowledge-update | 0.000 / 0-5 | 0.248 / 4-5 | 0.096 / 1-5 |
| multi-session | 0.092 / 0-5 | 0.113 / 1-5 | 0.131 / 2-5 |
| single-session-assistant | 0.106 / 0-5 | 0.183 / 3-5 | 0.422 / 5-5 |
| single-session-preference | 0.050 / 0-5 | 0.121 / 1-5 | 0.190 / 1-5 |
| single-session-user | 0.017 / 0-5 | 0.347 / 4-5 | 0.359 / 4-5 |
| temporal-reasoning | 0.208 / 1-5 | 0.225 / 2-5 | 0.228 / 1-5 |

Two cells stand out, with the strong caveat that each cell is 5 items:

- **single-session-assistant** is memsub's best cell (5/5 judged correct,
  F1 0.42 vs naive's 0.18 and 3/5). These questions ask about what the
  assistant said; memsub's session-aware scoring plausibly helps keep whole
  relevant sessions together under the budget.
- **knowledge-update** is memsub's worst cell (1/5 vs naive's 4/5, F1 0.10 vs
  0.25). These questions require surfacing the *latest* version of a changed
  fact. A plausible mechanism is that memsub's recency weighting operates on
  ingestion-order timestamps (`ts` is a synthetic counter in the adapter, see
  `memsub/longmemeval.py`), while its session weighting can pull in the
  earlier, superseded session; naive cosine top-k has no such preference. At
  n=5 this is a hypothesis, not a finding; it is the natural first probe if
  this benchmark is extended (see the extensions queue: consolidation's
  supersede pass exists in memsub but is not exercised by this read-only
  benchmark harness).

### Deviations from the published LongMemEval protocol (why absolute numbers are not leaderboard-comparable)

- Answerer is qwen3:8b (local), not GPT-4o or another frontier model; the
  paper's reader models are far stronger, so absolute accuracy here is much
  lower by construction.
- Judge is Sonnet via local CLI, not the paper's GPT-4o judge prompt.
- n = 30 of 500 questions (stratified, seeded), not the full set.
- Abstention items excluded (30 of 500); abstention behavior unmeasured.
- Turn-level ingestion with no LLM extraction pass, matching this repo's
  LOCOMO methodology (`memsub.bench`), not the paper's per-system
  configurations.

What survives these deviations: the within-run ordering of no_memory vs
naive_topk vs memsub_hybrid_v2 under identical answerer, judge, budget, and
sample, which is exactly the comparison this repo's other benchmarks make.

## LOCOMO judge-capture note (partial, superseded scope)

Before LongMemEval_S was selected as the external benchmark round's
deliverable, a re-capture of the LOCOMO holdout (with answer text persisted
for judge scoring, `scripts/judge_capture_holdout.py`) was run for two of
three conditions (`results/holdout-judge-capture-no_memory.json`,
`results/holdout-judge-capture-naive_topk.json`, n=80 each). At the time this
section was written, the memsub_hybrid_v2 capture and the judge pass over
those files had not been run.

**Update 2026-07-17**: the judge pass is now complete. See
`docs/locomo-jscore-2026-07-17.md` for the full LOCOMO J-score (LLM-judge
accuracy) run: no_memory 2.53%, naive_topk 37.50%, memsub_hybrid_v2 47.50%,
memsub_decompose (opt-in decomposition) 48.75%, n=80 per condition, Sonnet-CLI
judge, qwen3:8b local answerer. That doc also lines memsub's numbers up
against the frontier-answerer competitor numbers quoted in
`docs/improvement-plan-2026-07-17.md` Part A (mem0, Zep, Dakera, MIRIX) with
every deviation stated explicitly -- read it before citing any of these
numbers externally, the local-vs-frontier-answerer gap is large and easy to
misrepresent. Bead `memory-sublayer-vtr` is closed.
