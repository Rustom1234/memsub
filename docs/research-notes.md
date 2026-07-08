# Research notes: improving long-conversation memory recall

Written 2026-07-08 for the LOCOMO evidence-recall push. Sources were surveyed
via web research (citations inline); every claim is labeled peer-reviewed,
preprint, or vendor/marketing. Our own failure data comes from
`docs/error-analysis-locomo.md` (dev split only).

## Our failure modes (measured, dev split, budget 1000)

- 220 of 223 evidence-all failures are retrieval ranking misses (`ranked_low`):
  the evidence turn exists in the store but ranks median 174 (p75 300) when
  the budget packs roughly the top 50 turns.
- Budget cuts (2) and ingestion gaps (1) are negligible. Oracle bound with
  perfect ranking is 99.7%: retrieval quality is the entire game at this
  budget.
- Per category fail rate: multi-hop 94.6%, open-domain 94.7%, single-hop
  51.5%, temporal 35.6%.
- Mean cosine(query, evidence) is 0.18-0.21 on failures vs 0.32-0.39 on
  successes: the hashed bag-of-features embedder has weak semantic signal;
  failures are cases with little lexical overlap between question wording and
  evidence wording, or where only one hop of a multi-hop question matches.

Implication: techniques must attack ranking (better matching signal, query
decomposition, complementary retrievers), not budget packing or storage.

## What memory systems report on LOCOMO (and how much to trust it)

- **LoCoMo paper itself** (Maharana et al., ACL 2024, arXiv 2402.17753,
  peer-reviewed, benchmark authors, most trustworthy source here): temporal
  reasoning is the starkest human-model gap (best model ~25 F1 vs 92.6 human);
  adversarial questions collapse long-context models (2.1 F1 at 16k context);
  and their retrieval-granularity ablation found LLM-extracted "observations"
  beat raw dialog turns (41.4 vs 31.7 F1 top-5) which beat session summaries
  (lossy). Retrieval precision, not recall, hurt open-domain answers.
- **mem0** (arXiv 2504.19413, ECAI 2025, vendor-authored): extraction +
  ADD/UPDATE/DELETE/NOOP pipeline; J-scores 67.1 single-hop / 51.2 multi-hop /
  72.9 open-domain / 55.5 temporal on their own harness. The "92.5% LOCOMO"
  figure circulating is from a mem0 blog (marketing), not the paper.
- **Zep/Graphiti** (arXiv 2501.13956, KGC 2025, vendor-authored): bi-temporal
  knowledge graph with edge invalidation. Their 84% LOCOMO claim is blog-only
  and publicly disputed; mem0's re-run scored Zep at 58.4, Zep's rebuttal
  scored itself at 75.1 and accused mem0's harness of bugs. No neutral rerun
  exists. Their paper's own numbers are on DMR (near-saturated) and
  LongMemEval, not LOCOMO.
- **A-MEM** (arXiv 2502.12110, NeurIPS 2025): Zettelkasten-style linked notes;
  its LOCOMO numbers here come only from mem0's competitor harness (weakest
  row there), so low confidence either way.
- **MemGPT/Letta** (arXiv 2310.08560, COLM 2024): memory hierarchy with
  self-editing; headline numbers are on DMR not LOCOMO; Letta's "74.0%
  LOCOMO" is a product blog claim.

Trust summary: every vendor LOCOMO number was produced on that vendor's own
harness with its own judge, and the one public cross-check (mem0 vs Zep)
ended in mutual accusations of rigged harnesses. The transferable,
author-endorsed findings are: (1) temporal and multi-hop are the hard
categories, (2) observation-level granularity beats raw turns beats
summaries, (3) precision under noise matters, not just recall. Note their
metric is answer quality via LLM judge; ours is evidence recall under a
token budget, so absolute numbers do not transfer at all.

## Techniques from the retrieval literature (evidence, transfer, cost)

| technique | evidence | expected gain on OUR failure modes | cost |
|---|---|---|---|
| BM25 + dense, RRF fusion | RRF: Cormack et al. SIGIR 2009 (peer-reviewed); BM25 strong zero-shot baseline: BEIR, NeurIPS D&B 2021 | directly attacks ranked_low; our own probe: BM25 alone +16.9pp evidence-all on dev | ~50 lines, zero deps |
| temporal metadata + date-aware boost | TempRALM (arXiv 2401.13222, preprint); LoCoMo authors: temporal is the biggest gap | temporal bucket (32 dev failures); session dates currently discarded by our loader | tiny; regex date parse, no deps |
| multi-hop query decomposition (self-ask EMNLP 2022; IRCoT ACL 2023, +21 retrieval pts on multi-hop QA) | peer-reviewed, strongest multi-hop evidence in survey | multi-hop bucket (69 dev failures, 94.6% fail rate); single query cannot cover two topics | LLM call(s) per question, or heuristic sub-queries free |
| multi-granularity (RAPTOR, ICLR 2024, +20 pts on QuALITY; LoCoMo observation ablation) | peer-reviewed | cross-session gist questions; can be approximated dep-free by adding a session-level score to turn scores | offline; LLM summaries optional |
| HyDE / query2doc expansion (ACL 2023 / EMNLP 2023) | peer-reviewed, designed for weak unsupervised encoders like ours | ranked_low where wording differs | 1 LLM call per query |
| HippoRAG 1/2 PPR over entity graph (NeurIPS 2024 / arXiv 2502.14802) | peer-reviewed, big multi-hop recall gains (68->89 R@5 on 2Wiki) | multi-hop ceiling, but needs triple extraction; heuristic-NER + numpy PPR is a possible lite version | high |
| cross-encoder / LLM rerank (monoT5, RankGPT) | peer-reviewed | precision within budget | neural weights violate zero-dep; LLM rerank = calls per query |
| Chain-of-Note (EMNLP 2024) | peer-reviewed | none: operates after retrieval, cannot move evidence recall | n/a |

## Ranked shortlist (gain on our buckets x zero-infra cost)

1. **Hybrid BM25 + dense with RRF.** Our probe already measured +16.9pp from
   the lexical half alone; fusion is the safest, cheapest, best-evidenced win.
2. **Temporal metadata.** Parse LOCOMO session dates into episode metadata
   (currently thrown away) and boost turns whose session date matches
   dates/months/years mentioned in the question. Cheap, targets 32 dev
   failures plus temporal precision generally.
3. **Multi-hop handling via multi-query RRF.** Heuristic sub-query split
   first (free, deterministic); LLM decomposition (haiku) only if the
   heuristic is flat. Targets the 69-failure multi-hop bucket.
4. **Session-granularity signal.** Add a session-level relevance score
   (session as pseudo-document) to turn scores: a dependency-free
   approximation of RAPTOR/multi-granularity.
5. **HyDE-style expansion (haiku).** Only if 1-4 stall; adds an LLM call per
   query at answer time, which is a real product cost.

Deprioritized with reasons: full HippoRAG (engineering cost, LLM triple
extraction over every conversation); cross-encoder rerank (neural weights
break zero-infra); Chain-of-Note (cannot move evidence recall by
construction); LLM-extracted observation memory (LOCOMO authors' best
granularity, but requires extracting ~3000 turns through an LLM per run and
changes what "evidence id" means for our metric; revisit only if the cheap
stack stalls).
