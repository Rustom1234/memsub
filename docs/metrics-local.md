# Local-model metrics

Generated 2026-07-09 01:30 by `scripts/metrics_local.py` on: Linux 6.17.0-35-generic, 20 CPUs (x86_64), GPU: NVIDIA GeForce RTX 5090, 32607 MiB, Python 3.12.3.
Every number below is from an executed run against a real local ollama daemon (no cloud calls). Numbers on different hardware (no GPU, less RAM) will differ, especially latency and extraction wall time.

## (a) Answer quality: does memsub help a local model answer questions?

Local model `qwen3:8b` is both the memory backend (embeds and retrieves via memsub) and the answerer. LOCOMO dev conversations `conv-26, conv-30, conv-41` (never the holdout split), budget 1000 tokens, n=171 questions per condition, stratified across LOCOMO categories 1-4 (category 5 adversarial excluded per docs/benchmark.md convention; up to 50 sampled per category, capped by availability). Graded with deterministic token-F1 against the gold answer (no LLM judge, nothing to fabricate). `memsub_plus_sleep` is extension #1 (docs/extensions.md): sleep consolidation's three outputs (session summaries, higher-order inferences, entity co-occurrence links) stored as first-class episodes alongside facts, competing for the same token budget through the same retrieval path.

### Overall (required 3-way table: no-memory / naive-topk / memsub, plus the extension)

| condition | n | mean token-F1 | exact-substring rate | mean answer latency (s) |
|---|---|---|---|---|
| no_memory | 171 | 0.0186 | 0 | 0.651 |
| naive_topk | 171 | 0.1136 | 0.0819 | 1.639 |
| memsub_hybrid_v2 | 171 | 0.157 | 0.1228 | 1.532 |
| memsub_plus_sleep | 171 | 0.1593 | 0.1287 | 1.724 |

### Per question-type (category) breakdown

| condition | category | n | mean token-F1 | exact-substring rate |
|---|---|---|---|---|
| no_memory | multi-hop | 50 | 0.0256 | 0 |
| no_memory | temporal | 50 | 0.0088 | 0 |
| no_memory | open-domain | 21 | 0.0043 | 0 |
| no_memory | single-hop | 50 | 0.0275 | 0 |
| naive_topk | multi-hop | 50 | 0.124 | 0.04 |
| naive_topk | temporal | 50 | 0.038 | 0 |
| naive_topk | open-domain | 21 | 0.0666 | 0.0476 |
| naive_topk | single-hop | 50 | 0.1985 | 0.22 |
| memsub_hybrid_v2 | multi-hop | 50 | 0.2037 | 0.08 |
| memsub_hybrid_v2 | temporal | 50 | 0.0473 | 0.02 |
| memsub_hybrid_v2 | open-domain | 21 | 0.0422 | 0 |
| memsub_hybrid_v2 | single-hop | 50 | 0.2682 | 0.32 |
| memsub_plus_sleep | multi-hop | 50 | 0.2131 | 0.08 |
| memsub_plus_sleep | temporal | 50 | 0.049 | 0.02 |
| memsub_plus_sleep | open-domain | 21 | 0.033 | 0 |
| memsub_plus_sleep | single-hop | 50 | 0.2689 | 0.34 |

Total wall time for this cell: 949.0s.

### Time-boxed quality-comparison row: deepseek-r1:32b vs qwen3:8b

One-off comparison only, not used for volume metrics above (orchestrator model-budget guardrail: r1:32b is 19GB and slow on this box). n=12 questions (memsub condition only), hard wall-clock cap 480.0s, actual 91.6s.

| model | mean token-F1 | mean answer latency (s) |
|---|---|---|
| deepseek-r1:32b | 0.2036 | 5.743 |
| qwen3:8b | 0.1386 | 1.89 |

## (b) Retrieval latency and memory footprint

Hybrid v2 retrieval, 1451 episodes indexed (3 LOCOMO conversations concatenated), 45 queries.

| metric | value |
|---|---|
| ingest / index build time | 0.127s |
| query latency p50 | 2.27 ms |
| query latency p95 | 2.84 ms |
| query latency mean | 2.32 ms |
| SQLite store size | 512,000 bytes (352.9 bytes/episode) |
| retrieval index RSS delta | 0 KB |

**Latency guardrail**: search (retrieval only, excluding any LLM call) is budgeted at 300ms warm on this box. Measured p95 is 2.84ms — PASS, 105.6x headroom.

Retrieval here uses the local hashed embedder (zero-dependency default), not ollama embeddings; hashed-embedder latency is retrieval-side CPU cost only, no network hop, which is the honest number for the zero-service default path.

## (c) Write-time cost: extraction

One real transcript chunk (LOCOMO `conv-30` session 1, 28 turns, ~685 input tokens) through `qwen3:8b` via ollama.

| metric | value |
|---|---|
| extraction wall time | 7.21s |
| approx input tokens/s | 94.9 |
| candidate memories extracted | 8 |

## FINAL HOLDOUT: end-to-end local-model answer quality (executed 2026-07-09 02:37, `scripts/holdout_answer_quality.py`)

One run on held-out LOCOMO conversations 4-10 (never used in any selection decision; stack and sampling seed frozen before this run). qwen3:8b as both memory backend and answerer, budget 1000, up to 20 questions per category, deterministic token-F1. Hardware: Linux 6.17.0-35-generic, 20 CPUs (x86_64), GPU: NVIDIA GeForce RTX 5090, 32607 MiB, Python 3.12.3.

| condition | n | mean token-F1 | exact-substring rate |
|---|---|---|---|
| no_memory | 80 | 0.0217 | 0.0000 |
| naive_topk | 80 | 0.1170 | 0.1250 |
| memsub_hybrid_v2 | 80 | 0.1515 | 0.1625 |
| memsub_plus_sleep | 80 | 0.1431 | 0.1625 |

Per category (mean token-F1):

| condition | multi-hop | temporal | open-domain | single-hop |
|---|---|---|---|---|
| no_memory | 0.0192 (n=20) | 0.0000 (n=20) | 0.0205 (n=20) | 0.0471 (n=20) |
| naive_topk | 0.1572 (n=20) | 0.0826 (n=20) | 0.0365 (n=20) | 0.1918 (n=20) |
| memsub_hybrid_v2 | 0.1614 (n=20) | 0.0722 (n=20) | 0.1019 (n=20) | 0.2707 (n=20) |
| memsub_plus_sleep | 0.1365 (n=20) | 0.0672 (n=20) | 0.0841 (n=20) | 0.2846 (n=20) |
