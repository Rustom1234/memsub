# Extensions: what to build next, and why

Written 2026-07-09, before implementation. Ranked by expected impact on the
weakest measured cells (multi-hop and open-domain LOCOMO evidence recall,
see docs/benchmark.md and docs/error-analysis-locomo.md) times cost, with
risk noted. The frame has changed since docs/research-notes.md was written:
this machine now has a local LLM (ollama: qwen3:8b, deepseek-r1:32b) that
costs zero dollars per call. Every technique research-notes.md deprioritized
specifically for "changes the cost profile" (LLM rerank, query
decomposition, HyDE) is back on the table, because the cost is now latency
and local compute, not API spend. That reframing is the main new lever this
round.

Baseline to beat (dev, budget 1000, from docs/benchmark.md): hybrid v2
72.8% evidence-all / 85.1% evidence-any. Per-category dev: single-hop 87.0
all, temporal 93.3 all, **multi-hop 20.3 all / 77.0 any**, **open-domain
31.6 / 52.6**. Multi-hop and open-domain are the gap.

## Ranked candidates

### 1. Local LLM reranking of the hybrid shortlist (implement first)

Take the hybrid v2 top-K (K~20-30) candidates already retrieved by the
zero-cost BM25+dense+temporal+speaker+session stack, and have a local model
(qwen3:8b) score or reorder them for relevance to the query before packing
under budget. This directly attacks the dominant failure mode in
docs/error-analysis-locomo.md: 220 of 223 dev evidence-all failures are
`ranked_low` (evidence exists in the store, just ranked too low to fit the
budget) — a ranking problem, which reranking is built to fix.

- **Expected impact**: high. Cross-encoder/LLM rerank is the best-evidenced
  precision technique in the literature survey (monoT5, RankGPT) and was
  only deprioritized before because a neural cross-encoder breaks the
  zero-dependency story and an API-based LLM rerank changes the cost
  profile per query. A local model removes the second objection entirely
  and the first was never really about ollama.
- **Cost**: one local LLM call per query (or per query with K candidates
  batched into one prompt, which is what we'll do) — latency, not $, but
  latency is real: qwen3:8b measured ~1-1.5s per call earlier in this
  session. That is a genuine product cost for interactive use, which is why
  it should stay an opt-in retrieval mode, not silently replace the
  zero-cost default.
- **Risk**: low-medium. Adds a runtime dependency on ollama being up for
  this mode specifically (falls back to unranked hybrid order on failure).
  Batching K candidates into one prompt keeps it to one call per query
  rather than K.

### 2. Multi-hop query decomposition via local LLM

Multi-hop is the single worst cell (20.3% evidence-all dev) and barely
moves under BM25 alone (research-notes.md: max 14.9% from the lexical probe)
because a single query embedding/BM25 query cannot cover two separate
topics referenced by a multi-hop question. Have qwen3:8b split the question
into 2-3 sub-queries, run the existing hybrid retriever per sub-query, and
RRF-fuse the result lists (reusing the same RRF fusion memsub.hybrid already
does for BM25+dense).

- **Expected impact**: high specifically on multi-hop (its one target
  bucket), near zero elsewhere by construction — this is a category-specific
  fix, not a general lift. IRCoT (ACL 2023) reports +21 retrieval points
  from decomposition on multi-hop QA, the strongest single evidence point in
  research-notes.md's survey.
- **Cost**: one local LLM call per query for decomposition, plus one hybrid
  search per sub-query (2-3x retrieval cost, all local/free). No LLM cost
  beyond the one decomposition call.
- **Risk**: medium. Decomposition quality from an 8B model on a 2-3 sentence
  question is unproven here; a heuristic fallback (e.g. split on "and" /
  conjunctions, or just fall back to the original query) should trigger when
  the LLM call fails or returns garbage.

### 3. Session-level LLM summaries as an additional retrievable granularity

The existing session-granularity RRF list (session as pseudo-document,
weight 3.0) already measured a real lift (66.3/77.8 -> 72.8/85.1 dev, the
single biggest jump in the v2 log) using raw session text concatenated as
the pseudo-doc. LOCOMO's own ablation (Maharana et al. 2024) found
LLM-extracted "observations" beat raw turns beat session summaries for
top-k retrieval, but that was without our RRF fusion underneath it — a real
LLM-written session summary (2-3 sentences, "what happened / who / when")
as one more fused signal, rather than a concatenation proxy, is a natural
next step and now costs nothing to generate locally.

- **Expected impact**: medium. The mechanism already works (proven by the
  raw-concatenation version); a real summary is a quality upgrade to an
  existing win, not a new capability, so expected marginal gain is smaller
  than candidates 1-2.
- **Cost**: one local LLM call per session at write/index time (not
  query time — cacheable), cheap and off the interactive critical path.
- **Risk**: low. Purely additive to an already-frozen, already-working
  signal; worst case it doesn't move the number and gets reverted like PRF
  and dialog-locality were in the v1 log.

### 4. Entity/co-occurrence graph index for multi-hop chaining

Build a dependency-free co-occurrence graph (entity mentions per turn,
edges weighted by co-occurrence count, no embeddings/no LLM) and use it to
expand multi-hop retrieval: find turns connected to both halves of a
two-entity question via a shared intermediate node (a heuristic,
numpy/dict-only approximation of HippoRAG's personalized PageRank).

- **Expected impact**: medium-high in principle (HippoRAG reports 68->89
  R@5 on 2Wiki multi-hop, the biggest number in the whole survey) but our
  version is a lite heuristic, not the real algorithm, and entity extraction
  without an LLM (regex/capitalized-word heuristics) is weak on
  conversational text. Real entity extraction needs an LLM pass per turn at
  write time.
- **Cost**: high relative to 1-3. Needs entity extraction (LLM, one call per
  turn or per session — meaningfully more write-time cost than candidate 3's
  one-per-session), graph construction, and a new traversal/scoring path.
- **Risk**: medium-high. Most implementation surface of any candidate here;
  the LOCOMO win in the literature is for the full algorithm (PPR over a
  clean extracted KG), not this lite version, so the gain is the least
  certain of the top four.

### 5. Reflection / self-questioning at write time (A-MEM style)

Generate auxiliary questions or notes per memory at write time so future
queries phrased differently still match ("this memory answers: what is the
user's timezone").

- **Expected impact**: low-medium, low confidence. research-notes.md already
  flagged A-MEM's own LOCOMO numbers as coming only from a competitor's
  harness (mem0's), the weakest-sourced number in the whole survey — there
  is no trustworthy evidence this specific technique moves evidence recall
  on this dataset.
- **Cost**: medium. One extra LLM call per extracted memory at write time
  (multiplies write-time cost, candidate for part (c) of docs/metrics-local.md
  to quantify before committing to it).
- **Risk**: medium-high on cost/benefit given the uncertain evidence base;
  ranked below the graph index despite lower implementation complexity
  because the expected-impact evidence is weaker.

### 6. Forgetting/salience curve tuned on recall benchmarks

The decay mechanism already exists (memsub/consolidate.py: importance x
half-life discount, boosted by retrieval count) and its parameters are
already part of the existing dev-tuned grid search in
scripts/run_benchmark.py. There is no new mechanism to build here, only a
re-tune, and decay targets stale-fact hygiene (Part A of benchmark.md, where
it already gets 98% clean recall), not LOCOMO evidence recall, which is
today's weak spot. Deprioritized: highest cost/lowest marginal-impact ratio
of the six, since the mechanism it would improve isn't the one that's
underperforming.

### 7. Contradiction / temporal-validity surfaced at query time

Currently contradiction resolution is offline-only (consolidation supersedes
the older memory; the superseded copy is silent at query time, present only
in the audit log). Surfacing "this may be stale, a newer contradicting
memory exists" at injection time would help correctness for
not-yet-consolidated stores, but doesn't move the current bottleneck
(retrieval ranking) and is hard to score against the existing evidence-recall
metric (it changes what gets *labeled* in context, not what's retrieved).
Deferred: real but orthogonal to the measured weak cells, and no clean way
to benchmark the gain with the harness this repo already has.

## What gets implemented this round

Two reprioritizations from orchestrator review, in order:

**First pass (2026-07-09, review 1)**: candidate 3 (session/topic
summaries) promoted ahead of candidates 1-2, reasoning that the weak cells
(multi-hop, open-domain) are aggregation-shaped questions that fact/turn-level
retrieval structurally misses, and a stored summary is the direct fix.

**Second pass (2026-07-09, review 2, "innovation review")**: superseded the
plain-summaries plan with its more innovative superset, **sleep
consolidation** (`memsub/sleep.py`, `memsub sleep`-style offline pass): a
local model (qwen3:8b) replays recent memories and produces three
first-class memory types, each with provenance linking back to sources:

  - (a) **summaries** - the multi-granularity summaries above, now one output
    of sleep (`memsub.summarize.summarize_sessions`, unchanged)
  - (b) **inferences** - higher-order conclusions the raw facts imply
    (`kind="inference"`, `provenance["inferred"]=True`, carries a
    confidence). `memsub.consolidate` was changed so an inference can never
    silently outrank an observed memory on contradiction, regardless of
    which is newer (`_is_inferred`, tested in `tests/test_sleep.py`).
  - (c) **links** - a dependency-free entity co-occurrence graph
    (`memsub.sleep.generate_links`), built once at sleep time rather than
    per query, folding candidate 4 (entity/knowledge-graph index) into
    sleep's third output as directed.

Rationale given: this is the one candidate that composes with memsub's
actual differentiator (the audit trail and budget-aware policy framing,
not retrieval tricks that mem0/Zep/RAPTOR already ship) — every inference
is traceable to the exact memories that produced it via `memsub audit`.
Sleep precomputes aggregation at consolidation time instead of hoping
retrieval assembles it at query time, which is a more direct fix for
multi-hop/open-domain than reranking or decomposing turn-level retrieval.
Candidates 1-2 (local LLM rerank, local LLM multi-hop decomposition) move to
the back of the queue, picked up only if sleep plateaus.

Implementation note: a summary/inference/link episode does not carry the
evidence dia_id LOCOMO annotates, so the existing evidence-recall metric
(which matches packed episode ids against annotated turn ids) will not
credit one of these that answers a question correctly — it would look like
evidence recall got worse even when the model's actual answer got better.
The real metric for this extension is the end-to-end local-model
answer-quality harness (docs/metrics-local.md), not evidence-recall; both
are reported for transparency, but keep/revert is decided on answer
quality. Separately, the observed-beats-inferred consolidation rule is
measured directly with a deterministic unit test (an inference newer than
the observed fact it contradicts must still be the one superseded) rather
than folded into the Part A synthetic clean-recall sweep, which has no
notion of inferred memories in its generator; extending that generator to
simulate sleep output was judged lower value than a precise, deterministic
test of the exact rule that matters.

## Outcome (2026-07-09, after measurement)

Sleep consolidation was implemented (`memsub/sleep.py`, `memsub sleep`),
measured on dev, safety-checked, and given the one holdout run:

- Dev answer quality (n=171): +0.002 F1 overall, multi-hop 0.204 to 0.213.
- Holdout answer quality (n=80, one run): 0.143 vs 0.152 for plain hybrid
  v2; the dev multi-hop edge reversed. Full tables in docs/metrics-local.md,
  verdict logged in docs/benchmark.md.
- Safety: observed-beats-inferred (enforced in both the dedup phase and the
  contradiction pass of `memsub/consolidate.py`) preserves 98.0% clean
  recall under adversarial stale-value inferences; disabling the rule
  collapses clean recall to 0.0% (docs/benchmark.md safety check).

Decision per the keep/revert protocol: `memsub sleep` ships as an OPT-IN
command, no answer-quality claim, no change to the default retrieval stack.
Its shipping merits are auditability (every inference carries provenance to
its source sessions) and the measured safety rule. Candidates 1-2 (local
LLM rerank, LLM multi-hop decomposition) remain the next queue if this
direction is revisited; the local-model repositioning headline that DID
survive holdout is memsub hybrid v2 at 0.152 F1 vs naive 0.117 vs no-memory
0.022, with 2.8ms p95 retrieval latency.
