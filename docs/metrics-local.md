# Local-model metrics

Generated 2026-07-09 00:55 by `scripts/metrics_local.py` on: Linux 6.17.0-35-generic, 20 CPUs (x86_64), GPU: NVIDIA GeForce RTX 5090, 32607 MiB, Python 3.12.3.
Every number below is from an executed run against a real local ollama daemon (no cloud calls). Numbers on different hardware (no GPU, less RAM) will differ, especially latency and extraction wall time.

## (a) Answer quality: does memsub help a local model answer questions?

Local model `qwen3:8b` is both the memory backend (embeds and retrieves via memsub) and the answerer. LOCOMO conversation `conv-26`, budget 1000 tokens, n=40 questions per condition (stratified across LOCOMO categories 1-4; category 5 adversarial excluded per docs/benchmark.md convention). Graded with deterministic token-F1 against the gold answer (no LLM judge).

| condition | n | mean token-F1 | exact-substring rate | mean answer latency (s) |
|---|---|---|---|---|
| no_memory | 40 | 0.0229 | 0 | 0.697 |
| naive_topk | 40 | 0.0744 | 0.025 | 1.39 |
| memsub_hybrid_v2 | 40 | 0.1477 | 0.1 | 1.417 |

Total wall time for this cell: 140.2s.

## (b) Retrieval latency and memory footprint

Hybrid v2 retrieval, 1451 episodes indexed (3 LOCOMO conversations concatenated), 45 queries.

| metric | value |
|---|---|
| ingest / index build time | 0.129s |
| query latency p50 | 2.24 ms |
| query latency p95 | 2.77 ms |
| query latency mean | 2.27 ms |
| SQLite store size | 512,000 bytes (352.9 bytes/episode) |
| retrieval index RSS delta | 2,116 KB |

Retrieval here uses the local hashed embedder (zero-dependency default), not ollama embeddings; hashed-embedder latency is retrieval-side CPU cost only, no network hop, which is the honest number for the zero-service default path.

## (c) Write-time cost: extraction

One real transcript chunk (LOCOMO `conv-30` session 1, 28 turns, ~685 input tokens) through `qwen3:8b` via ollama.

| metric | value |
|---|---|
| extraction wall time | 4.38s |
| approx input tokens/s | 156.4 |
| candidate memories extracted | 8 |
