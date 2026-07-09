# memsub: Owner's Guide

This guide is written for someone who has not read the code. It explains what
memory-sublayer (`memsub`) does, how it works end to end, the specific
algorithms involved, the measured results, what was tried and rejected, how
to run it, and what is planned next. Every claim below is grounded in the
docs (`README.md`, `docs/positioning.md`, `docs/memory-design.md`,
`docs/metrics-local.md`, `docs/benchmark.md`, `docs/extensions.md`) and in a
targeted read of the source files named throughout. Where something was not
confirmed in the code or docs reviewed, that is stated explicitly rather than
guessed.

## 1. WHAT THIS IS

Think of an AI coding assistant (like Claude Code) as a storyteller with
total amnesia between sittings. Every session it wakes up with no memory of
you, your machine, your repos, or the five facts it re-learned last time
("this repo disables ROS pytest plugins via pytest.ini"). memsub is a small
library that gives that assistant a private, persistent memory: a single
file on your own disk that stores facts, preferences, and decisions across
sessions, and a retrieval system that can pull the right handful of those
memories back out when they are relevant to what you are doing right now.
No cloud service holds this data, no company's servers see your
conversations, and nothing runs when your assistant is not running.

The core idea is not "store everything and hope search finds it later."
Two things distinguish memsub's approach. First, there is a hard token
budget: an AI assistant's context window is finite and every memory you
inject competes directly with the code and conversation it also needs to
see, so memsub treats "best set of memories that fits in N tokens" as the
actual engineering problem, not an afterthought. Second, there is an
explicit, auditable policy for what gets written, what gets superseded when
a fact changes, and what gets quietly forgotten over time, and every one of
those decisions is logged so you (or the agent) can ask "why do you
remember that, and when was it used." Nothing is ever silently deleted:
old, corrected, or stale memories are marked as such and kept for the audit
trail, they are just no longer retrieved.

The project's design lineage matters for understanding its priorities. It
began life as the memory layer for an AI-safety experiment (the "Lying
Game", preserved on a separate branch) that needed to know exactly what an
agent could have remembered at the moment of any decision. That safety-first
requirement, "full provenance, nothing deleted, everything auditable," is
inherited wholesale by the developer-tool product it became. The project's
own positioning doc is blunt that it is not trying to out-feature mem0 (a
much larger, funded memory platform); instead it picks a narrow, honest
wedge: zero-infrastructure, fully local memory for coding agents, where the
differentiator is the write/consolidate/audit policy rather than clever
retrieval scoring alone (a claim the project's own benchmarks partly
confirmed and partly walked back, discussed in section 5).

## 2. HOW IT WORKS, END TO END

Data flows through memsub in five stages. Each stage is a real module you
can open and read.

**Stage 1: input arrives as a raw transcript.**
A conversation log (JSONL, including the shape Claude Code itself writes, or
plain text) is handed to `memsub.extraction.load_transcript()`
(`memsub/extraction.py`), which parses it into a list of `Turn` objects
(role, content, timestamp, index). Plain text is split into paragraphs;
JSONL is parsed message-by-message, tolerating both `{"role","content"}` and
Claude-Code-style `{"type","message":{"role","content"}}` shapes, including
content that is a list of `{"type":"text","text":...}` blocks.

**Stage 2: extraction turns turns into candidate memories.**
`extract_memories()` in `memsub/extraction.py` chunks the turns
(`chunk_turns`, default 12,000 characters per chunk, never splitting a
single turn) and sends each chunk to an LLM backend with a fixed system
prompt (`EXTRACTION_SYSTEM`) instructing it to pull out "durable" facts,
preferences, decisions, or events, and explicitly not transient chit-chat.
The model must return a JSON array of `{"content", "kind", "confidence"}`
objects; these become `CandidateMemory` objects, each stamped with
provenance (`source` file, `turn_span`, `backend` name, `model` name,
`extracted_at` timestamp, `confidence`). This is the only stage that
strictly needs an LLM call per chunk (backend is swappable: Anthropic API,
local ollama, the `claude` CLI in headless mode, or a deterministic mock
used by tests, see `memsub/backends.py`).

**Stage 3: candidates become episodes in the store.**
`CandidateMemory.to_episode()` converts each candidate into an `Episode`
(`memsub/store.py`): `content`, `run_id`, `session_id`, `kind`, `importance`
(set from confidence, floored at 0.05), and `provenance`. `EpisodicStore`
(`memsub/store.py`) appends it to a single SQLite table (`episodes`). The
store is append-only by design: there is no UPDATE or DELETE on that table.
Every lifecycle event (superseded, forgotten) and every usage event
(retrieved, injected) is written instead to a second table, `memory_events`,
via `store.log_event()`. An episode's live/dead status is *derived*, not
stored: `store.status()` looks for the most recent terminal event
(`"superseded"` or `"forgotten"`) for that episode id, and `store.active()`
returns everything with no terminal event. This is what makes the audit
trail complete: nothing is ever actually gone.

**Stage 4 (offline, periodic): consolidation cleans up the store.**
`memsub.consolidate.consolidate()` (`memsub/consolidate.py`) runs as a
separate, explicit pass (`memsub consolidate`), not on every write. It has
three phases, detailed in section 3: dedup, contradiction-based supersede,
and decay-based forgetting. `memsub.sleep.sleep_store()`
(`memsub/sleep.py`, `memsub sleep`) is a second, opt-in offline pass that
replays memories into summaries, inferences, and entity-co-occurrence links.

**Stage 5: retrieval and injection at query time.**
When the agent needs memory, it calls `MemoryClient.search()` or
`MemoryClient.inject()` (`memsub/sdk.py`). `search()` builds (and caches)
an index over the active episodes in the requested namespace, either a
`HybridRetriever` (`memsub/hybrid.py`, the default, "retrieval v2") or the
older `SemanticIndex` (`memsub/retrieval.py`, dense-only "v1", selectable
via `retrieval="dense"`). The retriever ranks all active episodes for the
query (section 3 covers exactly how) and returns the top `k`. If a
`budget_tokens` value is supplied, `search()` walks the ranked hits in order
and greedily keeps only as many as fit under a `chars/4` token
approximation (`memsub.policy.approx_tokens`), logging both the full
retrieved set and the packed/injected subset as events via
`memsub.audit.log_recall()`. `inject()` is a thin wrapper that joins the
packed episode contents with newlines into one text block, which is exactly
the string an agent would splice into its context.

That five-stage pipeline is exposed to a real coding agent through the MCP
server, `memsub/mcp_server.py`: a stdlib-only, dependency-free JSON-RPC 2.0
server speaking over stdin/stdout, launched on demand by `claude mcp add
memsub -- <venv>/bin/memsub mcp` and exiting when the client does. It
exposes four tools that map directly onto `MemoryClient` methods:
`add_memory` (write), `search_memory` (Stage 5), `list_memories`, and
`forget_memory` (marks an episode `"forgotten"`, an event, not a deletion).

## 3. THE ALGORITHMS, SPECIFICALLY

### Extraction

One LLM call per ~12,000-character chunk of transcript
(`extract_memories()`, `memsub/extraction.py`). The prompt
(`EXTRACTION_SYSTEM`) asks for a bare JSON array of
`{"content", "kind", "confidence"}` objects, where `kind` must be one of
`fact | preference | decision | event` (anything else is coerced to
`fact`). Confidence is clamped to `[0, 1]`; malformed or missing confidence
defaults to `0.5`. The response is parsed by finding the first `[` and last
`]` in the raw text and JSON-decoding that slice (`_parse_candidates`),
which is deliberately tolerant of a model wrapping its JSON in prose. When
using ollama's `qwen3` models, the README notes their `<think>...</think>`
reasoning blocks are stripped before this JSON parse (this stripping logic
was not directly inspected in `backends.py` during this review, but is
described as the flow in the README).

### Consolidation

`memsub.consolidate.consolidate()` runs three phases in order, per
namespace, over the *active* episode set only:

1. **Dedup.** All active episodes in a namespace are embedded (the same
   local embedder used for retrieval) and an all-pairs cosine similarity
   matrix is computed. Any pair with similarity at or above
   `dedup_threshold` (default `0.92`) is collapsed: the newer episode
   survives, the older one gets a `"superseded"` event with
   `reason="duplicate"`. One asymmetric rule guards sleep output: if the
   *older* of the pair is an observed memory and the *newer* is a
   sleep-generated inference (`kind == "inference"` or
   `provenance["inferred"] is True`), the inference loses even though it is
   newer, via `_is_inferred()`. This is the "observed beats inferred" rule
   at the dedup stage.
2. **Contradiction.** Pairs that are similar but below the dedup threshold
   (`>= contradiction_min_sim`, default `0.45`) and share a kind (or where
   either one is an inference) are sent to the LLM backend in one batched
   call (`_judge_contradictions`, `CONTRADICTION_SYSTEM` prompt) asking
   `{"pair": n, "contradicts": true|false}` for each pair, capped at
   `max_llm_pairs` (default `40`). Where the model says two memories
   contradict, the newer one supersedes the older, *except* the same
   observed-beats-inferred override applies: an inference never supersedes
   an observed memory it contradicts, no matter which is newer. This phase
   is skipped cleanly if no backend is supplied (`consolidate --no-llm`).
3. **Decay.** For every surviving episode older than `decay_min_age_days`
   (default 14), a value is computed:

       age_days = (now - episode.ts) / 86400
       value = importance * 0.5 ** (age_days / decay_half_life_days) * (1 + retrieved_count)

   with `decay_half_life_days` defaulting to `30.0`. If `value` falls below
   `decay_value_threshold` (default `0.2`), the episode gets a `"forgotten"`
   event with `reason="decay"`. Being retrieved often protects a memory from
   decay because `retrieved_count` is a multiplicative boost, not an
   additive one.

All three phases write `Action` records (`action`, `episode_id`, `reason`,
`detail`) which become store events unless `dry_run=True`.

### Policy scoring under a token budget

Two related but distinct budget mechanisms exist in the codebase.
`memsub.policy.MemoryPolicy.pack_injection()` (`memsub/policy.py`) is the
simpler, generic greedy packer: given an ordered list of text chunks, it
keeps adding chunks (`approx_tokens(chunk) + 1` each, where
`approx_tokens = max(1, len(text) // 4)`, a chars/4 approximation) until the
next chunk would exceed `injection_budget_tokens` (default 700), then stops
(no chunk is dropped from the middle to make room for a later, better one).
`MemoryClient.search()`/`.inject()` in `memsub/sdk.py` implements the same
greedy chars/4 budget logic directly against the retriever's ranked output:
walk the fused ranking in order, keep a hit if adding it does not exceed
`budget_tokens`, otherwise skip it and keep checking later (cheaper) hits.
Either way, the budget question is answered by ranking quality (what order
are candidates considered in) combined with greedy first-fit packing, not
by any knapsack-style optimal packing.

`docs/benchmark.md` grid-searched a separate set of *scoring* parameters
(recency weight, recency floor, importance floor, dedup threshold) on a dev
split, landing on
`{'recency_weight': 0.999, 'recency_floor': 0.75, 'importance_floor': 0.0, 'dedup_threshold': 0.88}`
for the benchmark harness; the shipped `HybridRetriever` and
`MemoryClient` defaults are `recency_weight=0.999`, `recency_floor=0.75`
(the benchmark doc's `dedup_threshold: 0.88` is a benchmark-harness
parameter, not the same as `ConsolidationConfig.dedup_threshold`'s shipped
default of `0.92` seen in `consolidate.py`; this discrepancy between the
two `dedup_threshold` numbers exists in the docs and code as reviewed and
was not further reconciled here).

### Hybrid retrieval v2 (BM25 + dense + RRF + temporal + speaker + session)

`memsub.hybrid.HybridRetriever` (`memsub/hybrid.py`) is the default search
path in the SDK, CLI, and MCP server. For a query it builds up to five
separate rankings over the same episode set and fuses them with reciprocal
rank fusion (`rrf_fuse()`, `memsub/lexical.py`, Cormack, Clarke, Buettcher,
SIGIR 2009):

    fused_score(doc) = sum over rankings r of  weight_r / (rrf_k + rank_r(doc) + 1)

with `rrf_k = 60` by default. The rankings, each contributing only when its
inputs allow it (so the retriever degrades gracefully to fewer signals):

1. **Dense** (`_dense_order`, weight `0.5`): cosine similarity between the
   query embedding and every episode's hashed bag-of-features embedding
   (`memsub/embed.py`), multiplied by a bounded recency factor
   `recency_floor + (1 - recency_floor) * recency_weight ** age_rank` (rank
   0 = newest episode, so recency never zeroes out old-but-relevant memories
   the way unbounded exponential decay would) and a bounded importance
   factor of the same shape. Sorted by the product.
2. **BM25** (weight `1.0`): Okapi BM25 (`k1=1.5, b=0.75`) over a small
   custom tokenizer (`tokenize()`/`stem()`, `memsub/lexical.py`) that lower
   cases, strips a hand-picked English stopword list, and applies a
   deliberately non-Porter suffix stripper (handles plurals, `-ing`,
   `-ed`, `-ly`, and doubled final consonants) "just enough to unify
   research/researching, camp/camped/camping." If `temporal=True` (default),
   the BM25 corpus is built from `"{session_date} {content}"` rather than
   raw content, so date tokens participate in lexical scoring even though
   they are never shown to the model as injected text.
3. **Temporal / date-match** (weight `1.0`, only added when the query
   contains a recognizable month name via `DATE_RE`): episodes whose
   `provenance["session_date"]` tokens are a superset of the date terms in
   the query are pulled into their own ranking list.
4. **Speaker** (weight `1.0`, only added when exactly one known speaker
   name is mentioned in the query): episode content beginning with
   `"Name: "` is detected at ingest time (`_speakers` set); if the query
   names exactly one such speaker, that speaker's own turns (in BM25 order)
   form a dedicated ranking.
5. **Session granularity** (weight `3.0`, only added when there is more
   than one session indexed): all episode contents belonging to one
   session are concatenated into a single pseudo-document and BM25-ranked
   against the query at the session level (a lightweight, RAPTOR-style
   multi-granularity signal); each episode inherits its session's rank,
   with within-session ties broken by that episode's own BM25 rank.

A sixth signal, pseudo-relevance feedback (`prf_weight`), exists in the code
as an RM3-style query-expansion pass but defaults to `0.0` (off) because it
was reverted after benchmarking (see section 5).

`search(query, k)` returns the top `k` fused results with a rank-derived
score of `1 / (rrf_k + position + 1)`: this score is only meaningful within
one query's ranking, not comparable across different queries.

A small worked intuition: for a query like "What activities does Melanie
partake in in August?", BM25 alone would rank Melanie's individual
activity-mentioning turns; the date-match signal pulls forward anything
tagged with an August `session_date`; the speaker signal (Melanie is named)
boosts her own turns specifically; and the session signal rewards the whole
session where "activities" and "Melanie" co-occur heavily, even if no
single turn scores highest on lexical overlap. RRF then combines wherever
these four (or five, with dense) rankings agree, without needing the raw
scores to be on comparable scales.

### Sleep consolidation

`memsub.sleep.sleep()` (`memsub/sleep.py`) is a separate, opt-in offline
pass (`memsub sleep`) producing three new episode *kinds*, each a fully
provenance-tracked `Episode` retrieved through the exact same hybrid
pipeline as any other memory:

- **Summaries**: produced by `memsub.summarize.summarize_sessions()`
  (module not read in detail during this review; behavior described per
  `memsub/sleep.py` and `docs/memory-design.md` as multi-granularity
  session-level notes), for sessions with at least `min_session_turns`
  (default 3) turns.
- **Inferences**: one LLM call per batch of session summaries
  (`generate_inferences()`), prompted (`INFERENCE_SYSTEM`) to draw
  higher-order conclusions ("a pattern across sessions, an implied
  preference, a likely reason behind a decision") rather than restate a
  summary, returning up to `max_inferences` (default 5) JSON objects
  `{"content", "confidence", "based_on": [session_ids]}`. Each becomes an
  `Episode` with `kind="inference"`, `provenance["inferred"] = True`, and
  `provenance["derived_from"]` set to the supporting session ids (falling
  back to all summarized session ids if the model's `based_on` list doesn't
  validate).
- **Links**: `generate_links()` builds a dependency-free entity
  co-occurrence graph with no LLM call at all: a regex over capitalized
  words (`_CAP_RE`, excluding a stopword-like set `_ENTITY_STOP` of common
  capitalized function words) plus the leading `"Speaker: "` name pulls
  candidate entities out of each episode's content; any entity appearing in
  at least `min_sessions` (default 2) distinct sessions becomes one link
  episode ("`{entity} comes up across sessions: {session_list}`"), capped at
  `max_links` (default 10), sorted by how many sessions the entity spans.

**The observed-beats-inferred rule.** This is enforced in
`memsub.consolidate._is_inferred()` and used in both the dedup phase (an
inference cannot survive a near-duplicate collision against an older
observed memory, even though newer normally wins) and the contradiction
phase (an inference cannot supersede an observed memory it's judged to
contradict, even though newer normally wins). `sleep_store()` is
incremental and idempotent: it only replays episodes without a prior
`"slept"` event and that are not themselves sleep output
(`SLEPT_KINDS = {"summary", "inference", "link"}`), marking each source
episode `"slept"` after a run so a second `memsub sleep` call is a no-op
over unchanged data.

### The benchmark designs

Three separate measurement protocols are used across the docs, and it is
important not to conflate them:

- **Part A: synthetic recall/clean/stale (docs/benchmark.md).** A seeded
  generator creates 30 "fact families" with 3 versions each (later versions
  supersede earlier ones) plus 120 low-importance distractors, run over 5
  seeds, n=150 queries per cell. Three metrics per system per budget:
  `recall` (the latest version of the fact is present in the injected
  context at all), `clean` (the latest version is present *and* no
  superseded version of the same fact is also injected), `stale` (only an
  outdated version made it in). This is the metric that most directly
  exercises consolidation, since a system with no dedup/supersede logic can
  score perfect `recall` while scoring 0% `clean` (injecting all three
  conflicting versions of a fact at once).
- **Part B: LOCOMO evidence recall (docs/benchmark.md).** LOCOMO (Maharana
  et al. 2024, a public long-conversation QA benchmark) provides real
  multi-session conversations with annotated "evidence turns" per question.
  `evidence (all)` = every annotated evidence turn for a question is present
  in the injected context under budget; `evidence (any)` = at least one is;
  `answer-in-ctx` is a noisier secondary check (the gold answer string
  appears verbatim, lowercased, anywhere in the injected context). The
  adversarial LOCOMO category (5) is excluded from this metric because it
  has no evidence turns to recall by construction.
- **The three-way (and four-way) answer-quality F1 table
  (docs/metrics-local.md).** A local model (`qwen3:8b`) is both the memory
  backend *and* the answerer. Conditions are `no_memory`, `naive_topk`
  (store everything, rank by cosine relevance only, no consolidation),
  `memsub_hybrid_v2` (the shipped default), and `memsub_plus_sleep`. Answers
  are graded with deterministic token-level F1 against the LOCOMO gold
  answer (no LLM judge, so nothing here is fabricated or subjective), plus
  an `exact-substring rate`.

"Clean recall" specifically refers to the Part A `clean` metric: the right
(current) fact was injected, with no contradicting stale copy of the same
fact alongside it. It is the headline number for the consolidation/policy
write-time story (98.0% clean vs 0.0% for naive top-k), separate from the
LOCOMO evidence-recall numbers, which measure retrieval-ranking quality
against real annotated conversations rather than fact freshness against a
synthetic supersede scenario.

## 4. CURRENT RESULTS

All numbers below are reproduced as stated in `docs/metrics-local.md` and
`docs/benchmark.md`. Interpretations are added in plain English; the numbers
themselves are quoted verbatim.

### Answer quality with a local 8B model (docs/metrics-local.md, dev split, n=171)

| condition | n | mean token-F1 | exact-substring rate | mean answer latency (s) |
|---|---|---|---|---|
| no_memory | 171 | 0.0186 | 0 | 0.651 |
| naive_topk | 171 | 0.1136 | 0.0819 | 1.639 |
| memsub_hybrid_v2 | 171 | 0.157 | 0.1228 | 1.532 |
| memsub_plus_sleep | 171 | 0.1593 | 0.1287 | 1.724 |

Plain English: with no memory at all, the model barely answers questions
about earlier conversation (F1 0.019). Giving it a naive "store everything,
rank by similarity" memory roughly 6x's answer quality. memsub's hybrid v2
retrieval does better still, and adding the opt-in sleep pass nudges it up
a little more on this dev split (this small sleep edge did not hold up on
the holdout run below).

### FINAL HOLDOUT answer quality (docs/metrics-local.md, LOCOMO conversations 4-10, n=80, one run)

| condition | n | mean token-F1 | exact-substring rate |
|---|---|---|---|
| no_memory | 80 | 0.0217 | 0.0000 |
| naive_topk | 80 | 0.1170 | 0.1250 |
| memsub_hybrid_v2 | 80 | 0.1515 | 0.1625 |
| memsub_plus_sleep | 80 | 0.1431 | 0.1625 |

Plain English: this is the number the README leads with. On data the system
never saw during tuning, memsub's default hybrid retrieval lifts answer
quality from 0.117 (naive) to 0.152, and having no memory at all leaves the
model at 0.022. On this holdout, sleep actually made answers slightly
*worse* (0.143 vs 0.152), which is why it ships off by default.

### Retrieval latency and storage footprint (docs/metrics-local.md, 1451 episodes, 45 queries)

| metric | value |
|---|---|
| ingest / index build time | 0.127s |
| query latency p50 | 2.27 ms |
| query latency p95 | 2.84 ms |
| query latency mean | 2.32 ms |
| SQLite store size | 512,000 bytes (352.9 bytes/episode) |

Plain English: search itself (no LLM in the loop) is essentially free,
under 3ms at the 95th percentile against a budget of 300ms, roughly 106x
headroom. On disk, each memory costs about 350 bytes.

### Write-time extraction cost (docs/metrics-local.md, one 28-turn chunk, qwen3:8b)

| metric | value |
|---|---|
| extraction wall time | 7.21s |
| approx input tokens/s | 94.9 |
| candidate memories extracted | 8 |

Plain English: turning one real 28-turn conversation session into 8
candidate memories takes about 7 seconds on a local 8B model. This is the
one part of the pipeline with meaningful latency, and it is offline (it
happens at `memsub extract` time, not at query time).

### Part A: synthetic recall/clean/stale under budget (docs/benchmark.md, held-out seeds, n=150/cell)

| budget | system | recall | clean | stale |
|---|---|---|---|---|
| 100 | naive-topk | 100.0% | 0.0% | 0.0% |
| 100 | policy+cons | 98.7% | 98.0% | 0.0% |
| 800 | naive-topk | 100.0% | 0.0% | 0.0% |
| 800 | policy+cons | 98.7% | 98.0% | 0.0% |

(Full table in `docs/benchmark.md`; the pattern holds identically at 100,
200, 400, and 800 tokens.) Plain English: naive retrieval always finds *a*
version of the fact (100% recall) but never distinguishes the current
version from stale ones (0% clean, it injects all conflicting copies).
Consolidation trades a hair of raw recall (98.7% vs 100%) for a huge clean
win (98.0% vs 0%): the agent almost never sees contradicting versions of
the same fact at once.

### Part B: LOCOMO evidence recall, dev tuned policy vs naive (docs/benchmark.md, n=1153)

| budget | system | evidence (all) | evidence (any) |
|---|---|---|---|
| 500 | naive-topk | 35.4% | 46.2% |
| 500 | policy | 35.4% | 45.5% |
| 1000 | naive-topk | 43.4% | 56.9% |
| 1000 | policy | 42.9% | 55.9% |
| 2000 | naive-topk | 52.0% | 66.7% |
| 2000 | policy | 51.2% | 66.2% |

Plain English: the tuned scoring policy (relevance x bounded recency x
bounded importance) essentially ties naive cosine-similarity retrieval on
real long conversations, within about 1 percentage point at every budget.
This is the result that made the project rebuild retrieval from scratch
(retrieval v2, below); scoring tweaks alone were not the win.

### Retrieval v2 FINAL HOLDOUT (docs/benchmark.md, conversations 4-10, n=1153, one run)

| budget | system | evidence (all) | evidence (any) |
|---|---|---|---|
| 500 | policy (v1) | 35.4% | 45.5% |
| 500 | naive-topk | 35.4% | 46.2% |
| 500 | hybrid-v2 | 64.7% | 78.7% |
| 1000 | policy (v1) | 42.9% | 55.9% |
| 1000 | naive-topk | 43.4% | 56.9% |
| 1000 | hybrid-v2 | 73.8% | 86.7% |

Plain English: this is the retrieval-v2 headline. Fusing stemmed BM25,
dense similarity, date/temporal matching, speaker matching, and
session-level aggregation with reciprocal rank fusion roughly doubles the
fraction of questions where all annotated evidence turns fit in the
injected budget, compared to both the old scoring-only policy and naive
top-k. This was a single held-out run, frozen before execution, on data
never used to choose parameters.

### Sleep-inference safety check (docs/benchmark.md, adversarial stale inferences, n=150/cell)

| cell | clean | stale |
|---|---|---|
| adversarial inferences, rule ON | 98.0% | 0.0% |
| adversarial inferences, rule OFF | 0.0% | 98.7% |

Plain English: when a sleep-generated inference is deliberately made to
restate a stale value with a newer-looking timestamp (a worst-case "sleep
hallucinated something outdated" scenario), the observed-beats-inferred
rule is the entire difference between the agent still getting the correct
fact (98.0% clean) and it getting fooled almost every time (0.0% clean,
98.7% stale) if that rule is disabled.

## 5. WHAT WAS TRIED AND REJECTED

- **Raw multiplicative recency decay (v1 scoring, no floor).** Noted in
  `docs/benchmark.md`: "raw multiplicative recency (v1, floors at 0) was
  strictly worse, which is why the floors exist." Old-but-relevant memories
  could get scored to zero; the shipped `recency_floor`/`importance_floor`
  parameters exist specifically to prevent this.
- **Tuned relevance x recency x importance scoring alone, as "the"
  differentiator.** `docs/positioning.md`'s Option 3 argued this could be
  the crown jewel; the LOCOMO Part B result (policy ties naive within ~1pp)
  falsified that framing. Verdict recorded in `docs/positioning.md`:
  "Crown-jewel language is therefore retired for scoring and earned for
  consolidation + audit."
- **BM25 alone (probe).** 58.7/70.8 dev evidence-all/any, beating dense-only
  scoring by 16.9pp, but not used standalone; it became one input to RRF
  fusion instead.
- **Hybrid RRF with equal signal weights.** 57.2/70.2, worse than
  down-weighting dense to 0.5; equal weighting let dense "drag BM25 down."
  Reweighted, kept at dense_weight 0.5.
- **Pseudo-relevance feedback / RM3-style query expansion.** Reached
  66.8/77.8 at best, a "+0.5pp plateau" that "degrades when pushed";
  judged a dev-overfitting risk that outweighed the gain. REVERTED, but the
  code was kept behind a default-off flag (`prf_weight=0.0` in
  `HybridRetriever`).
- **Dialog-locality neighbor boost.** "Strictly worse at every weight
  tried; never landed in library code." REVERTED, no flag kept.
- **Heuristic query decomposition** (splitting a question into clauses on
  and/or/,/; and RRF-fusing sub-query rankings). Moved the total score
  nowhere (72.6-72.8/84.9-85.1 vs a 72.8/85.1 baseline) because, per
  `docs/benchmark.md`, "only 12/93 dev multi-hop+open-domain questions have
  real compound-clause structure"; LOCOMO's "multi-hop" category turned out
  to be mostly single-topic aggregation questions, not chainable
  sub-questions. REVERTED.
- **Retrieval chaining** (a second BM25 hop seeded by co-mentioned
  speaker names from the first hop). Made results *worse* at every weight
  tried (68.1-70.5/79.4-82.5 vs 72.8/85.1 baseline), especially on
  open-domain (down to 21.1% evidence-all from 31.6%), because in
  two-person conversations the "co-mentioned speaker" signal degenerates
  into a generic "boost the other person's turns" boost that drowns out
  topic-specific signal. REVERTED.
- **mem0 as a direct comparison baseline.** Attempted, then explicitly
  skipped rather than reported: mem0's extraction stage failed on this
  machine because the only available local LLM (qwen3:8b) wraps responses
  in `<think>` blocks that break mem0's JSON parser, and there is no
  OpenAI/Anthropic key on the machine for mem0's intended providers. Per
  `docs/benchmark.md`: "A row measured under those conditions would
  benchmark qwen3's JSON compliance, not mem0's memory system, so per
  protocol the row is skipped rather than fought."
- **Sleep consolidation as an answer-quality feature.** Implemented in full
  (`memsub/sleep.py`), measured on dev (a small positive edge, +0.002 F1,
  multi-hop 0.204 to 0.213) but the edge reversed on the single frozen
  holdout run (0.143 vs 0.152 for plain hybrid v2, multi-hop specifically
  going from 0.204/0.213 dev-positive to 0.137 vs 0.161 holdout-negative).
  Per `docs/benchmark.md`: "the dev multi-hop gain reversed... VERDICT: sleep
  artifacts are NOT enabled by default and earn no answer-quality claim."
  It ships opt-in on safety/auditability merits only (the observed-beats-
  inferred rule), not on a performance claim.
- **"Beats mem0" and similar competitive quality claims.** Deliberately not
  claimed anywhere; `docs/positioning.md` states this outright: "no
  'beats mem0' (the comparison could not be run fairly on this machine and
  the doc says exactly why)."

## 6. HOW TO USE IT

All commands below are quoted from `README.md` / `memsub/cli.py`.
Output shapes described here are what the code structurally produces
(exact numbers will vary run to run); nothing here is a fabricated number.

**Install:**

```bash
git clone <this repo> && cd memory-sublayer
uv venv .venv && uv pip install -p .venv/bin/python -e .
.venv/bin/python -m pytest   # all tests local, LLM/embedding calls mocked
```

Runtime dependency list is `numpy`, per the README.

**Wire into Claude Code as an MCP server (one command):**

```bash
claude mcp add memsub -- /home/user/projects/memory-sublayer/.venv/bin/memsub mcp
```

This registers a stdio MCP server that Claude Code spawns on demand. It
exposes four tools (`add_memory`, `search_memory`, `list_memories`,
`forget_memory`); nothing runs when the agent is idle. Memories land in
`~/.memsub/memory.db` by default (override with `MEMSUB_DB`); scope an
agent to a namespace with `MEMSUB_NAMESPACE` and `MEMSUB_AGENT`.

**Python SDK:**

```python
from memsub.sdk import MemoryClient

client = MemoryClient()   # ~/.memsub/memory.db
client.add("This repo disables ROS pytest plugins via pytest.ini")
print(client.inject("how do we run tests here?", budget_tokens=300))
```

Expected output shape: `client.add()` returns an `Episode` object (id,
timestamp, content, etc.); `client.inject()` returns a single string, the
newline-joined content of whichever stored memories fit under the 300-token
budget, best match first.

**CLI:**

```bash
memsub extract session.jsonl --dry-run     # transcript -> candidate memories, prints only
memsub extract session.jsonl               # ... and writes them to the store
memsub consolidate --dry-run               # propose dedup/supersede/forget, apply nothing
memsub ns create shared:team --owner lead  # shared namespace with ACL
memsub search "how do we run tests?"       # hybrid retrieval (v2, default)
memsub audit --recent                      # recent memory activity
memsub audit <memory-id>                   # provenance and full event history for one memory
memsub sleep                               # offline: summaries, inferences, entity links
memsub mcp                                 # the MCP stdio server
```

Expected shapes: `memsub extract` prints one line per candidate
(`[      fact] conf=0.85  <content>`), then a count and whether anything was
written. `memsub consolidate` prints one line per action taken
(`supersede [duplicate] <episode_id> by=<other_id>`), then a count of
actions and remaining active memories. `memsub search` prints one line per
hit (`[<id prefix>] (<kind>) <content>`), best match first, or
"no matching memories". `memsub sleep` prints counts of
summaries/inferences/links produced, then each inference with its
confidence. `memsub audit <id>` prints the episode's provenance and its
full event trail (write, retrieve, inject, supersede/forget events, if
any).

**Local models (ollama), per the README:**

```bash
ollama pull qwen3:8b            # extraction / consolidation (fast)
ollama pull deepseek-r1:32b     # optional: higher-quality judgment calls
ollama pull nomic-embed-text    # optional: real local embeddings

memsub extract session.jsonl --backend ollama
memsub search "how do we run tests?" --embedder ollama
```

`--backend auto` (the CLI default) picks ollama automatically when a daemon
answers at `localhost:11434` and no `ANTHROPIC_API_KEY` is set; otherwise it
falls back to the `claude` CLI in headless mode, per the backend table in
`README.md`.

## 7. WHERE IT GOES NEXT

This is the ranked roadmap as stated in `docs/extensions.md`, written before
the sleep-consolidation work described in section 3/5 was implemented. Sleep
was actually built and shipped opt-in (see section 5); the remaining
candidates below are described in the doc as "the next queue if this
direction is revisited," picked up only if sleep (or its successor) plateaus.

1. **Local LLM reranking of the hybrid shortlist** (top priority, per the
   doc, "implement first"). Take the top ~20-30 hybrid-v2 candidates and
   have a local model (qwen3:8b) rerank them for relevance before packing
   under budget. Rationale: the dominant dev-split failure mode was
   `ranked_low` (evidence exists in the store, just ranked too low to fit
   the budget) at 220/223 of dev failures per the doc's error analysis;
   reranking is the direct fix for a ranking problem. Proposed as an
   opt-in mode (one extra local LLM call per query, ~1-1.5s measured
   elsewhere in the session) rather than a silent default, given the
   latency cost.
2. **Multi-hop query decomposition via a local LLM.** Have qwen3:8b split a
   multi-hop question into 2-3 sub-queries, run the existing hybrid
   retriever per sub-query, and RRF-fuse the resulting lists (reusing the
   existing fusion code). Targeted specifically at the worst-performing
   category (multi-hop, 20.3% evidence-all on dev); expected to move that
   one category and little else "by construction."
3. **Session-level LLM-written summaries as an additional retrieval
   signal.** A quality upgrade to the existing session-granularity RRF
   signal (raw session-text concatenation today) using an actual LLM
   summary instead; expected medium impact since the underlying mechanism
   already works, this is refinement rather than a new capability.
4. **Entity/co-occurrence graph index for multi-hop chaining**, a
   dependency-free heuristic approximation of HippoRAG-style personalized
   PageRank over an entity graph. Noted as the highest-cost of the top four
   candidates (needs an LLM entity-extraction pass) and the least certain
   gain, since the literature result it is inspired by used a much more
   thorough graph than this "lite" version would build. (Its simpler
   cousin, dependency-free entity co-occurrence links with no LLM call, was
   already folded into sleep's third output, "links," per section 3.)
5. **Reflection / self-questioning at write time (A-MEM style).** Generate
   auxiliary questions per memory at write time so future differently
   phrased queries still match. Ranked low-medium confidence: the doc notes
   the main literature evidence for this technique came only from a
   competitor's own benchmark harness (mem0's), "the weakest-sourced number
   in the whole survey."
6. **Re-tuning the forgetting/decay curve.** Deprioritized: the decay
   mechanism already exists and is already part of the dev-tuned grid
   search; it targets stale-fact hygiene (already at 98% clean recall in
   Part A), not the LOCOMO evidence-recall weak spot that is the actual
   current bottleneck.
7. **Surfacing contradiction/temporal-validity at query time** (rather than
   only resolving it offline in consolidation). Deferred: "real but
   orthogonal to the measured weak cells, and no clean way to benchmark the
   gain with the harness this repo already has."
