# Graph layer: bipartite entity-episode index

Ordered directly by Rustom ("Do the graph layer" / "Add the graph layer, learn from
mem0 and open source things, and yes u can use a better model if need be (dont use
api, use from my subscription)"). Design credit: a source-grounded read of
mem0/Graphiti/cognee's actual cloned source,
`memory-sublayer-research/oss-source-dive/report-2026-07-18.md` section 4
(Synthesis) -- that section is the design this build implements, not an
independent design. Full assumptions and success criteria recorded in
`WORKLOG.md` before any code was written (Karpathy rule 1).

## What shipped

Two additive SQLite tables (`memsub/store.py`: `entities`, `entity_links`), no
external graph DB, no embedding index for entities. Rule-based extraction
(reuses `memsub.sleep._extract_entities` unchanged) is the default and common
case; an LLM is spent only on the rare ambiguous entity-dedup pair, never per
episode.

- **Write path** (`memsub/graph.py`): `graph_store()` -- one implementation,
  reused by both `memsub graph backfill` (new CLI command) and
  `consolidate()`'s new opt-in `enable_graph=False` flag. Idempotent via a
  `"graphed"` event per episode, mirroring `memsub.sleep.sleep_store`'s
  `"slept"` pattern. A staged dedup funnel (`EntityResolver`): exact
  normalized match reuses an existing entity at zero cost; a `difflib`
  near-miss (ratio >= 0.82) against an existing entity in the same namespace
  is the one genuinely ambiguous case, resolved by exactly one LLM call,
  cached per resolver run so a repeat pair never asks twice; no near-miss, or
  no backend given for an ambiguous pair, means a new entity is created
  rather than guessed.
- **Query path** (`memsub/hybrid.py`): `HybridRetriever.rank_graph` /
  `search_graph`, opt-in, mirroring the existing `rank_decomposed` pattern
  exactly. Extracts rule-based entities from the query (no LLM), looks them
  up in the store's entity index (exact then fuzzy, no LLM), and folds the
  resulting "episodes sharing an entity" ranked list into the existing RRF
  fusion as one more signal -- never a separate query path. Bounded
  traversal: 1-hop by default (one entity-episode join), 2-hop behind an
  explicit `hops=2` (exactly one more entity-episode hop pair, never
  open-ended BFS).
- **Composition** (step 4): `rank_decomposed_graph` / `search_decomposed_graph`
  compose the graph signal with query-time decomposition (decompose first,
  then graph-augment each sub-query's retrieval), since decomposition was
  already the best measured lever going into this work
  (`docs/multihop-improvements-2026-07-17.md`).
- **Default paths unchanged**: `enable_graph` defaults `False`;
  `rank()`/`search()`/`consolidate()` called without the new opt-ins are
  byte-identical to before this work (locked in by
  `tests/test_graph.py::test_default_rank_and_search_are_unaffected_by_graph_population`).

One deliberate deviation from the report's literal schema sketch: entity ids
are namespace-scoped (`f"{namespace}::{normalized_text}"`), not bare
normalized text, because a bare-text id would collide two different
namespaces' identically-named entity into one row -- correctness, not
scope creep. See `WORKLOG.md` assumption 1 for the full reasoning, and
assumptions 2-8 for the other scoped decisions (entity typing, near-miss
threshold, cache lifetime, traversal ranking, backfill safety, eval file
naming).

## Backfill: real store, measured

Run against a **temp copy** of `~/.memsub/memory.db` (`shutil`/`cp`, never the
live file; live file's checksum verified unchanged before and after):

| metric | value |
|---|---|
| active episodes processed | 4,218 |
| entities created | 1,877 |
| entity-episode links created | 6,824 |
| wall time | 43.5s |
| LLM calls | 0 (`--backend none`, rule-based only) |
| rerun (idempotency check) | 0 episodes processed, 0.18s, same totals |

`memsub graph backfill --db DB` is namespace-aware by construction: each
episode resolves entities against its own `namespace`, so a single pass over
`--namespace` omitted (all namespaces) never crosses entity ids between
namespaces.

## Eval 1: answer quality, three conditions (n=74 multi-hop dev slice)

`scripts/graph_layer_eval.py`, following `scripts/multihop_answer_quality_eval.py`'s
exact precedent unchanged: `ANSWER_SYSTEM`, `BUDGET`, `FROZEN`, `token_f1` all
imported from `scripts/metrics_local.py`, qwen3:8b as both memory backend and
answerer, budget 1000, LOCOMO dev conversations 1-3 (never holdout),
category-1 (multi-hop) questions with a non-empty gold answer, n=74 (the full
multi-hop dev slice). Streamed incrementally to
`results/graph-layer-eval-2026-07-18.jsonl`, one record per
(condition, conversation, question), resumable.

| condition | n | mean token-F1 | exact-substring rate | mean answer latency (s) |
|---|---|---|---|---|
| baseline (shipped hybrid v2) | 74 | 0.1964 | 0.0676 | 0.737 |
| graph (rank_graph, 1-hop) | 74 | 0.1828 | 0.0676 | 0.743 |
| graph_decompose (rank_decomposed_graph) | 74 | 0.1901 | 0.0811 | 0.718 |

**Reading it honestly**: neither graph condition beats the shipped baseline's
mean token-F1 on this slice. Plain `graph` is the weaker of the two
(0.1828, an 6.9% relative drop from baseline) -- the 1-hop co-mention signal
alone pulls in some episodes that dilute the greedy-packed budget without
buying back enough relevance to compensate, the same budget-competition
dynamic already on record for write-time fusion
(`docs/multihop-improvements-2026-07-17.md`). `graph_decompose` recovers most
of that gap (0.1901, still 3.2% below baseline on F1) and is the only
condition to beat baseline's exact-substring rate (0.0811 vs 0.0676, a 20%
relative improvement) -- consistent with the graph signal occasionally
surfacing the exact right co-mentioned episode even when it doesn't move the
aggregate F1 number. `graph_decompose` is the best of the two graph configs
measured here, carried forward to Eval 2 as "the best new config" per the
task's instruction -- not because it beat the baseline on this metric, but
because among the graph variants it is the one worth spending the J-score
budget on.

## Eval 2: J-score, best config vs shipped baseline (n=80 LOCOMO holdout)

`scripts/locomo_jscore_graph_2026-07-18.py`, following
`scripts/locomo_jscore_holdout.py`'s exact protocol: LOCOMO holdout slice
(`scripts/holdout_answer_quality.py`'s `holdout_convs()`/`sample()`,
conversations 4-10, `random.Random(123)`, up to 20 questions per category,
n=80), qwen3:8b local answerer, blind Sonnet-CLI judge
(`memsub/judge.py`, `claude -p --model sonnet`, sees only
question/gold/answer, never the condition name). One run, wall time 4006.5s
(~67 min), not truncated. Appended to
`results/locomo-jscore-2026-07-18.jsonl`.

| condition | n_ok | accuracy | multi-hop | temporal | open-domain | single-hop |
|---|---|---|---|---|---|---|
| memsub_hybrid_v2 (shipped baseline) | 79/80 | 48.10% | 47.37% | 35.00% | 35.00% | 75.00% |
| graph_decompose (best new config) | 80/80 | 51.25% | 50.00% | 35.00% | 40.00% | 80.00% |

(memsub_hybrid_v2's n_ok is 79, not 80: one judge response was malformed and
excluded per `memsub/judge.py`'s never-guess policy, same convention as
`docs/locomo-jscore-2026-07-17.md`.)

### Verdict

**graph_decompose wins on J-score**: +3.15pp absolute accuracy (48.10% ->
51.25%, +6.5% relative), with the multi-hop row -- the category this whole
build targets -- up +2.6pp (47.37% -> 50.00%) and open-domain up +5.0pp
(35.00% -> 40.00%) and single-hop up +5.0pp (75.00% -> 80.00%). Temporal is
flat (35.00% both). This is the opposite sign from Eval 1's token-F1 number,
which is itself informative, not a contradiction to explain away: this repo
has already documented the same disagreement pattern for write-time fusion
(`docs/multihop-improvements-2026-07-17.md`'s evidence-recall-vs-answer-quality
split), and the report that designed this build flagged the caveat directly
(section 4's "eval caveat carried from
docs/multihop-improvements-2026-07-17.md"). A blind LLM judge grades semantic
correctness (does the answer capture the gold fact, in any phrasing); token-F1
grades lexical overlap against one reference string. The graph signal appears
to be shifting *which* facts get surfaced into context in a way that more
often lands the meaning right even when the exact wording diverges from the
single annotated LOCOMO reference answer -- plausible given `graph_decompose`
also won Eval 1's exact-substring rate, a coarser but still lexical signal
that moved the same direction as the J-score result.

**Recommendation: ship as opt-in, do not flip the default.** Two honest
reasons to stop short of changing `rank()`/`search()`'s default behavior on
this evidence:
1. n=74 (Eval 1) and n=80 (Eval 2) are both small single runs with no
   repeated-trial variance estimate; a 3.15pp swing on n=80 (about 2-3
   questions) is a real signal worth pursuing further, not yet a settled
   result the way the 309-test-suite regression guarantees are.
2. The two metrics disagree on sign. A metric disagreement is not
   automatically resolved by picking whichever one is more flattering --
   it means "measure again, on more data, before trusting the direction,"
   which is exactly what opt-in shipping buys: `rank_graph` /
   `rank_decomposed_graph` are available today for anyone who wants them
   (`memsub graph backfill` to populate the index, then call the graph-aware
   methods explicitly), while the shipped default stays exactly what
   309+19 tests already lock in.

## What is shipped regardless of this result

`enable_graph`, `rank_graph`, `search_graph`, `rank_decomposed_graph`, and
`search_decomposed_graph` are all opt-in and off by default, exactly as
scoped in `WORKLOG.md`'s assumptions. `memsub graph backfill` never runs
unless invoked. `memsub/vault_serve.py` was not touched. Nothing was pushed
to a remote.
