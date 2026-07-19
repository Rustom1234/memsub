# LOCOMO holdout: frontier-answerer v2 (best-effort prompt + temporal date surfacing)

Continuation of "i need to beat current top locomo score also", round 2. Prior run
(`docs/locomo-jscore-frontier-2026-07-18.md`) found memsub + Sonnet
(graph_decompose, 43.75%) scored *lower* than memsub + qwen3:8b on the identical
condition and slice (51.25%), and flagged a hypothesis: Sonnet follows the
conservative "say you don't know" instruction more faithfully than qwen3:8b,
and the judge gives no credit for calibrated refusal over a lucky guess. This
round diagnoses that hypothesis quantitatively (Task 1), fixes the answer
prompt to match the LOCOMO/mem0 best-effort norm (Task 2), and surfaces
session-date metadata into context for the temporal category specifically
(Task 3). All work in this doc happened 2026-07-18, same day as the round-1 run.

## Task 1: refusal diagnosis (no new LLM calls)

Regex-based refusal-pattern count over the `answer` field of every
`judge_status: "ok"` record in the existing JSONL files (no new runs).

### Refusal rate by condition

| condition | answerer | n | refusals | refusal rate |
|---|---|---|---|---|
| no_memory | Sonnet | 77 | 76 | 98.7% |
| no_memory | qwen3:8b (07-17) | 79 | 69 | 87.3% |
| naive_topk | Sonnet | 80 | 38 | 47.5% |
| naive_topk | qwen3:8b (07-17) | 80 | 31 | 38.8% |
| graph_decompose | Sonnet (v1 prompt) | 80 | 28 | 35.0% |
| graph_decompose | qwen3:8b (07-18) | 80 | 24 | 30.0% |
| graph_decompose | Sonnet (v2, best-effort prompt) | 80 | 4 | **5.0%** |

Direction confirmed in every condition: Sonnet refuses more than qwen3:8b on
identical retrieval, every time, under the v1 (conservative) prompt.

### Does refusal count fully explain the v1 accuracy gap? No -- roughly 40-45% of it.

On graph_decompose (the condition that matters), refusals were never credited
by the judge (`refusal_correct` summed to 0 for both models). So all correct
answers came from non-refusal answers:

- Sonnet (v1): 52 non-refusal answers, 35 correct -> non-refusal hit rate
  **67.3%**.
- qwen3:8b: 56 non-refusal answers, 41 correct -> non-refusal hit rate
  **73.2%**.

Total v1 gap: 6 questions (7.5pp). Refusal-count delta: only 4 (28 vs 24).
Counterfactual (Sonnet's 4 extra refusals answered at its own 67.3% hit
rate): expected +2.7 correct, narrowing the gap from 7.5pp to ~4.2pp.
**Verdict: the refusal hypothesis is confirmed in direction but only
partially in magnitude** -- refusal count explains ~40-45% of the v1 gap;
the rest was a genuine non-refusal answer-quality gap (Sonnet's actual
committed answers were right less often than qwen's).

### Temporal eye-read: 10 graph_decompose Sonnet failures, reconstructed context

Reconstructed the exact retrieved context deterministically (same
retrieval + local decompose call as the real run, no new answer/judge LLM
calls) for 10 of 15 temporal failures. **6/10 were clear surfacing gaps**:
a relative date phrase was correctly retrieved into context ("Bali last
year", "beach in Tampa yesterday", "won ... on Friday", "starting next
month", "Two weeks ago I read 'Avalanche'...", "told our extended fam ...
yesterday") but the absolute LOCOMO session date needed to resolve the
relative phrase into the gold's absolute format was never surfaced --
`memsub.bench.load_locomo` stores it in `provenance['session_date']` and
explicitly excludes it from packed content by design ("the packed content
does not include it, the budget is not charged for metadata"). **4/10 were
retrieval misses** (the date-relevant evidence turn was not in the packed
context at all -- a separate, harder problem, out of scope for Task 3).
Full per-example breakdown logged in `WORKLOG.md`.

This justified proceeding with Task 3's opt-in surfacing flag.

## Task 2: best-effort answer prompt, graph_decompose v2 re-run

Added `ANSWER_SYSTEM_BEST_EFFORT` as a new constant local to
`scripts/locomo_jscore_frontier_2026-07-18.py` (opt-in via `--prompt
best_effort`): "give your best guess based on what CONTEXT implies rather
than refusing -- but never invent a fact that has no support in CONTEXT."
`metrics_local.ANSWER_SYSTEM` (used by every local-answerer run in this
repo) is unchanged -- those numbers stand as history, per instruction.

Re-ran `graph_decompose`, Sonnet answerer, same n=80 slice, same blind
Sonnet judge, resumable streaming to
`results/locomo-jscore-frontier-v2-2026-07-18.jsonl` (kept fully separate
from the v1 file).

### v1 vs v2 (Sonnet, graph_decompose), and vs qwen3:8b local

| metric | v1 (conservative prompt) | v2 (best-effort prompt) | delta | qwen3:8b (local, 07-18) |
|---|---|---|---|---|
| overall | 43.75% | **52.5%** | **+8.75pp** | 51.25% |
| multi-hop | 45.0% | 45.0% | +0.0pp | 50.0% |
| temporal | 25.0% | 30.0% | +5.0pp | 35.0% |
| open-domain | 35.0% | 50.0% | +15.0pp | 40.0% |
| single-hop | 70.0% | 85.0% | +15.0pp | 80.0% |

**memsub + Sonnet v2 (52.5%) now beats memsub + qwen3:8b on the identical
condition and slice (51.25%)** -- the counter-intuitive local-beats-frontier
result from round 1 is reversed by fixing the prompt alone, no retrieval
change. Open-domain and single-hop drove almost the entire gain
(+15.0pp each); multi-hop did not move at all (still 45.0%, still behind
qwen's 50.0%) -- the prompt fix did nothing for multi-hop specifically.

## Task 3: temporal session-date surfacing (n=20, with vs. without flag)

Added `--with-dates` (eval-path-only opt-in, no `memsub` library default
change): prefixes each packed episode with `[session date]` from
`provenance['session_date']` when present, inside `_context_for` in the same
script. LOCOMO provides no question-asked-date field (checked
`memsub.bench.LocomoQA`: only `conversation_id`/`question`/`answer`/
`evidence`/`category`), so "include the question date if the protocol
provides one" does not apply here -- noted, not implemented.

Re-ran temporal category only (n=20), graph_decompose, Sonnet answerer,
Task 2's best-effort prompt, same blind judge, with and without
`--with-dates`:

| temporal (n=20) | accuracy |
|---|---|
| without flag (v2 base run) | 30.0% |
| **with flag** | **45.0%** |
| delta | **+15.0pp** |

A +15.0pp jump from surfacing session dates alone, on the same n=20
slice, same prompt, same everything else. This quantitatively confirms the
surfacing hypothesis from Task 1's eye-read, not just directionally.
Results: `results/locomo-jscore-frontier-v2-temporal-dated-2026-07-18.jsonl`.

**Caveat, stated plainly**: this flag was only exercised on the temporal
category in isolation. It was not re-run across multi-hop/open-domain/
single-hop in this round (out of scope per the task), and it is not a
memsub library default -- it lives behind `--with-dates` in this one eval
script. A hypothetical combined number (v2 base run's multi-hop/open-domain/
single-hop unchanged, temporal swapped to the dated 45.0%) would be
(45+45+50+85)/4 = **56.25% overall**, but this is an arithmetic extrapolation,
not a number actually measured in a single end-to-end run -- flagged
explicitly as such, not reported as a headline result.

## Updated standing vs. competitor numbers

| system | overall | multi-hop | temporal | open-domain | single-hop | notes |
|---|---|---|---|---|---|---|
| memsub + Sonnet v1 (conservative prompt) | 43.75% | 45.0% | 25.0% | 35.0% | 70.0% | round 1, docs/locomo-jscore-frontier-2026-07-18.md |
| **memsub + Sonnet v2 (best-effort prompt)** | **52.5%** | **45.0%** | 30.0% (45.0% w/ date flag) | **50.0%** | **85.0%** | this run, n=80 (temporal date flag only re-run at n=20) |
| memsub + qwen3:8b (local, 07-18) | 51.25% | 50.0% | 35.0% | 40.0% | 80.0% | for reference, unchanged |
| mem0 (2025 paper, GPT-4o-mini, reproduced) | ~61.7% | 51.2% | 55.5% | 72.9% | 67.1% | arXiv:2504.19413 |
| Zep/Graphiti (revised, disputed) | 75.14% ± 0.17 | -- | -- | -- | -- | mem0 claims repro as low as 58.44% |
| Dakera | 88.2% | 85.4% | -- | -- | -- | dakera.ai/benchmark |
| MIRIX (gpt-4.1-mini) | 85.38% | 83.70% | -- | -- | -- | arXiv:2507.07957 |
| ByteRover 2.0 | 92.2% | 85.1% | -- | -- | -- | byterover.dev |
| Hindsight | 89.6% | 70.8% | -- | -- | -- | 2025-2026 calibration point |

**memsub + Sonnet v2 (52.5%) still does not beat mem0's reproduced tier
(~61.7%) or Zep's revised number (75.14%), let alone Dakera/MIRIX/
ByteRover/Hindsight.** The gap to mem0 narrowed substantially though:
-17.95pp (v1) -> -9.2pp (v2), roughly halved, from a prompt fix plus a
scoped temporal-only surfacing fix, with zero retrieval-algorithm changes.

Per category against mem0's reproduced tier (the closest apples-to-apples
row, full breakdown available):

| category | memsub + Sonnet v2 | mem0 (reproduced) | delta |
|---|---|---|---|
| single-hop | **85.0%** | 67.1% | **+17.9pp, memsub ahead** (was +2.9pp in v1) |
| multi-hop | 45.0% | 51.2% | -6.2pp (unchanged from v1 -- prompt fix did not move this category) |
| open-domain | 50.0% | 72.9% | -22.9pp (was -37.9pp in v1, narrowed by the prompt fix alone) |
| temporal | 30.0% (45.0% w/ date flag) | 55.5% | -25.5pp (-10.5pp w/ date flag) |

## Honest verdict on what remains

**Single-hop is now a clear, growing memsub win** over mem0's reproduced
tier (+17.9pp, up from +2.9pp). **Open-domain and temporal both improved a
lot but remain the two real gaps** -- open-domain closed by more than half
(-37.9pp -> -22.9pp) from the prompt fix alone with no retrieval change,
and temporal closes further still with the date flag (-25.5pp -> -10.5pp),
but neither closes the gap to mem0 fully. **Multi-hop did not move at all**
(45.0% in both v1 and v2, still -6.2pp behind mem0) -- neither the prompt
fix nor the temporal date flag touched it, since it wasn't the target of
either change; this is the one category where round 2's interventions had
zero effect, and it is the category the original 07-17 improvement plan
predicted would be the "real gap" before round 1's evidence shifted that
framing to temporal/open-domain. memsub + Sonnet v2 still does not beat any
of the higher-tier competitor numbers (Zep/Dakera/MIRIX/ByteRover/Hindsight)
-- those gaps are large (20-40pp) and not plausibly closed by prompt or
context-formatting changes alone.

## Honest claim language for future reference

Say: "memsub, evaluated with a frontier answerer (Sonnet via the `claude`
CLI, subscription, no API key) and a best-effort answer prompt matching the
LOCOMO/mem0 protocol norm, scores 52.5% overall J-score accuracy on the same
n=80 LOCOMO holdout slice (graph_decompose config) -- up from 43.75% with
the original conservative prompt, and now above memsub's own local-answerer
result on the identical condition (51.25%). Roughly 40-45% of the original
prompt-driven gap was refusal-count-driven (a calibrated-refusal-vs-guessing
effect the judge doesn't credit); the rest was a genuine non-refusal
answer-quality gap the prompt fix also happened to close. Surfacing LOCOMO
session dates into context closes a further 15.0pp on the temporal category
specifically (n=20, isolated re-run). memsub + Sonnet v2 still trails mem0's
reproduced tier (~61.7%) by 9.2pp overall and Zep's revised number (75.14%)
by more; single-hop now clearly beats mem0's reproduced number (+17.9pp),
multi-hop is unchanged and still behind (-6.2pp), open-domain and temporal
narrowed substantially but remain the largest gaps."

Do not say: "memsub beats mem0" (it does not, overall or on 3 of 4
categories) or "the temporal fix is shipped" (it is an eval-only flag behind
`--with-dates`, not a library default, and was only measured on the
temporal category in isolation).

## Paths

- Script (v1 prompt/path unchanged, v2 prompt/flags added):
  `scripts/locomo_jscore_frontier_2026-07-18.py`
- v1 raw records (unchanged, prior commit): `results/locomo-jscore-frontier-2026-07-18.jsonl`
- v2 raw records (best-effort prompt, n=80, graph_decompose):
  `results/locomo-jscore-frontier-v2-2026-07-18.jsonl`
- v2 temporal-with-dates raw records (n=20, graph_decompose):
  `results/locomo-jscore-frontier-v2-temporal-dated-2026-07-18.jsonl`
- This report: `docs/locomo-frontier-v2-2026-07-18.md`
- Round-1 report: `docs/locomo-jscore-frontier-2026-07-18.md`
- Refusal-count and temporal eye-read detail: `WORKLOG.md` (2026-07-18 entry)
