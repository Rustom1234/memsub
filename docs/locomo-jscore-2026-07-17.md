# LOCOMO holdout: LLM-judge (J-score) run

Closes bead `vtr` and Part A's first fix in `docs/improvement-plan-2026-07-17.md`: memsub's
headline number (0.152 mean token-F1, qwen3:8b local, `docs/benchmark.md`) is not on the same
axis as any competitor number quoted there. mem0, Zep, Dakera, and MIRIX all report LLM-judge
accuracy (a J-score: does a judge model call the generated answer correct given the gold
answer), not token-overlap F1. This run puts a memsub number on that same axis for the first
time. Script: `scripts/locomo_jscore_holdout.py`. Raw per-question and per-condition-summary
records, streamed and flushed incrementally, resumable: `results/locomo-jscore-2026-07-17.jsonl`.

Executed 2026-07-17, one run, wall time 7331.3s (~122 minutes), not truncated.

## Methodology

Reuses the exact LOCOMO holdout slice this repo already uses for its headline number
(`scripts/holdout_answer_quality.py` / `scripts/holdout_eval.py`): `load_locomo(...)[3:]`
(LOCOMO conversations 4-10, the FINAL HOLDOUT split, never touched by any tuning decision),
`random.Random(123)` deterministic stratified sample, up to 20 questions per category
(categories 1-4: multi-hop, temporal, open-domain, single-hop; category 5 adversarial excluded
per repo convention), n=80 questions. This is the same n=80 slice behind the documented
memsub 0.152 F1 vs naive 0.117 vs no_memory 0.022 holdout result.

Four conditions, each run over the full 80-question slice (320 answer+judge pairs total):

- `no_memory` -- no retrieved context.
- `naive_topk` -- cosine top-k only, no policy.
- `memsub_hybrid_v2` -- the shipped default retriever (frozen hybrid v2 params: dense_weight
  0.5, bm25_weight 1.0, temporal + speaker + session-granularity boosts).
- `memsub_decompose` -- the same shipped retriever, but at query time the caller uses
  `HybridRetriever.rank_decomposed` (one bounded LLM decompose call, <=3 retrieval passes,
  RRF-fused) instead of plain `rank()`. This is today's opt-in decomposition feature
  (`docs/multihop-improvements-2026-07-17.md`); it is off by default in the product and is
  measured here as a separate condition, not folded into the `memsub_hybrid_v2` row.

Per question: the answer is generated locally by qwen3:8b via ollama (`OllamaBackend`,
default `think=None`, unchanged from the documented methodology), budget 1000 tokens
(chars/4 approximation), same `ANSWER_SYSTEM` prompt as every other local-model run in this
repo. The answer is then graded by a Sonnet judge (`memsub/judge.py`, `claude -p --model
sonnet` in headless print mode -- the same precedent used by `scripts/judge_capture_holdout.py`
and the LongMemEval external-benchmark run; Rustom's Max plan, no API key cost). The judge
prompt (`build_judge_prompt`) is built from exactly `(question, gold, answer)` -- the condition
name is never passed to the judge, so it is blind to condition by construction, not just by
convention. Judge verdicts are strict JSON (`{"correct": true|false}`) with a plain-text
fallback parser; a response that parses as neither is recorded `judge_status: "malformed"` and
excluded from accuracy rather than guessed (same policy as every other judge run in this repo).

### Deviations from the official LOCOMO J-score protocol

State plainly, because these numbers are being placed next to competitor numbers in the results
table below and the difference matters:

1. **Answerer is local (qwen3:8b), not frontier.** Every competitor number quoted in
   `docs/improvement-plan-2026-07-17.md` Part A (mem0, Zep, Dakera, MIRIX, ByteRover, Hindsight)
   was produced with a frontier API model as the answerer/reader (GPT-4o-mini, GPT-4.1-mini, or
   similar). This run's answerer is an 8B local model. This is by far the largest source of the
   gap between memsub's numbers and the competitor numbers below -- it is a statement about
   local-model answer quality as much as about the memory system.
2. **Judge model family differs.** This run's judge is Claude Sonnet via the local `claude` CLI.
   Published LOCOMO leaderboard numbers typically use a GPT-4-class judge (mem0's paper and most
   others use GPT-4o or GPT-4o-mini as judge). Different judge models have different strictness;
   absolute accuracy numbers from different judges are not directly comparable even holding the
   answerer fixed.
3. **Slice size is n=80, not the full LOCOMO test set.** The official LOCOMO benchmark has far
   more than 80 questions across its 10 conversations; this run uses the same 80-question
   stratified holdout sample this repo has used for every other holdout headline number, for
   direct comparability with memsub's own prior results, not the competitors' full-set numbers.
4. **memsub is a retrieval/selection layer over a fixed local answerer, not an end-to-end
   product benchmarked the way vendors benchmark their own systems.** Some competitors (e.g.
   Zep) also report contested variance in their own numbers (94.7% claimed vs 75.14% revised vs
   58.44% independently reproduced) -- see `docs/improvement-plan-2026-07-17.md` for the
   citation trail; that dispute is about their methodology, not this run's, and is noted here
   only so the "disputed" caveat travels with every cross-system comparison below.
5. **One malformed judge response** (1/320, `no_memory` condition, `conv-50`, single-hop
   category) is excluded from accuracy rather than guessed at, per `memsub/judge.py`'s
   never-guess policy. n=79 (not 80) for `no_memory`'s reported accuracy as a result; every
   other condition has clean n=80.

**What this run is:** a local-answerer LOCOMO J-score row, methodologically consistent with
itself (same judge, same slice, same budget across all four conditions), suitable for comparing
memsub's four conditions against each other. **What it is not:** an apples-to-apples comparison
with the frontier-answerer competitor rows below. Read the competitor numbers as "what a
memory system plus a frontier LLM achieves"; read memsub's numbers as "what a local-first memory
system plus a local 8B LLM achieves." Both are real numbers; they are not on the same axis, and
this doc does not claim otherwise.

## Results

### Overall judge accuracy

| condition | n | correct | judge accuracy |
|---|---|---|---|
| no_memory | 79 (1 malformed, excluded) | 2 | 2.53% |
| naive_topk | 80 | 30 | 37.50% |
| memsub_hybrid_v2 (shipped default) | 80 | 38 | 47.50% |
| memsub_decompose (opt-in) | 80 | 39 | 48.75% |

### Per-category judge accuracy (standard LOCOMO categories)

| condition | multi-hop | temporal | open-domain | single-hop |
|---|---|---|---|---|
| no_memory | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) | 2/19 (10.5%) |
| naive_topk | 11/20 (55.0%) | 6/20 (30.0%) | 3/20 (15.0%) | 10/20 (50.0%) |
| memsub_hybrid_v2 | 10/20 (50.0%) | 8/20 (40.0%) | 5/20 (25.0%) | 15/20 (75.0%) |
| memsub_decompose | 11/20 (55.0%) | 7/20 (35.0%) | 5/20 (25.0%) | 16/20 (80.0%) |

**Multi-hop row, called out explicitly** (this is the category the competitor claims in Part A
center on): no_memory 0.0%, naive_topk 55.0%, memsub_hybrid_v2 50.0%, memsub_decompose 55.0%.
At n=20 per category, one question is a 5-point swing -- read every number in this table as
directional, not precise. On this particular n=20 multi-hop cell, decomposition ties naive_topk
and edges the shipped default by one question (11/20 vs 10/20); this is the same
small-and-noisy shape of result as `docs/multihop-improvements-2026-07-17.md`'s earlier n=74
answer-quality finding for decomposition (+4.3% relative there), not a new, larger effect.
memsub's clearest, least noisy win in this run is single-hop (75.0-80.0% vs naive's 50.0%),
consistent with the shipped retriever's documented single-hop strength
(`docs/benchmark.md`'s evidence-recall holdout: 92.2%/93.3% single-hop all/any vs naive's
55.5%/59.1%).

### memsub numbers vs quoted competitor numbers (NOT apples-to-apples -- see deviations above)

| system | overall | multi-hop | notes |
|---|---|---|---|
| **memsub_hybrid_v2 (this run, local answerer)** | **47.5%** | **50.0%** | qwen3:8b answerer, Sonnet-CLI judge, n=80 |
| **memsub_decompose (this run, local answerer, opt-in)** | **48.75%** | **55.0%** | same, decomposition on |
| mem0 (2025 paper, GPT-4o-mini) | ~61.7% (mean of 4 reported categories) | 51.2% | arXiv:2504.19413 |
| mem0 (2026 rewrite, unverified vendor claim) | 92.5% | 94.6% | no independent reproduction found |
| Zep/Graphiti (Zep's own revised number, disputed) | 75.14% +/- 0.17 | -- | mem0 claims repro as low as 58.44% |
| Dakera | 88.2% | 85.4% | dakera.ai/benchmark |
| MIRIX (gpt-4.1-mini) | 85.38% | 83.70% | arXiv:2507.07957 |
| ByteRover 2.0 | 92.2% | 85.1% | byterover.dev |
| Hindsight | 89.6% | 70.8% | 2025-2026 calibration point |

memsub's local-answerer numbers sit well below every frontier-answerer competitor row above.
That gap is expected and is not a fair read of the memory system alone -- section above explains
why (deviation 1, primarily). The honest comparison this table supports is directional: memsub
plus a local 8B model recovers a meaningful fraction of what a memory system plus a frontier
model achieves (memsub_hybrid_v2's 47.5% vs no-memory's 2.5% is the load-bearing comparison
within this run), not a claim that memsub "beats" or "loses to" any competitor number in the
table. No claim of parity or superiority versus any competitor row is made anywhere in this
document.

## Honest claim language for future reference

Say: "memsub, evaluated through the same LLM-judge protocol competitors use (local qwen3:8b
answerer, Sonnet judge, n=80 LOCOMO holdout), scores 47.5% overall judge accuracy
(memsub_hybrid_v2) vs 37.5% naive top-k vs 2.5% no-memory; the opt-in decomposition condition
scores 48.75%. These are local-answerer numbers, not comparable in absolute terms to
frontier-answerer competitor numbers (mem0, Zep, Dakera, MIRIX all use GPT-4o/4.1-class
answerers); comparable only in that they now share a judge-accuracy metric family instead of
token-F1."

Do not say: "memsub scores 47.5% vs mem0's 61.7%" or any framing that puts these two numbers on
the same line without the local-vs-frontier-answerer caveat attached every time.

## Bead

Closes `memory-sublayer-vtr` ("Finish LOCOMO holdout judge pass"): the bead's original scope
(capture memsub_hybrid_v2, run the judge over all three prior conditions) is done, extended to
a fourth condition (memsub_decompose) to also cover today's decomposition opt-in per this
session's broader task.

## Paths

- Script: `scripts/locomo_jscore_holdout.py`
- Raw records (JSONL, streamed, resumable): `results/locomo-jscore-2026-07-17.jsonl`
- This report: `docs/locomo-jscore-2026-07-17.md`
- Prior partial capture (superseded by this run): `results/holdout-judge-capture-no_memory.json`,
  `results/holdout-judge-capture-naive_topk.json` (`scripts/judge_capture_holdout.py`, n=80
  each, no judge pass was ever run over them; this document is the completed judge pass)
