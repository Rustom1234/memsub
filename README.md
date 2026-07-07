# Memory Sublayer

Architectural episodic memory for agents, plus a deception-safety experiment
(the "Lying Game") that uses it to test whether episodic memory is a genuine AI
safety risk.

Two things live here:

1. **`memsub`** — a small, reusable, file-backed episodic-memory library for
   agents, built from the four-part decomposition episodic store / semantic
   retrieval / structured retrieval / policy.
2. **`lying_game`** — an experiment harness (based on DeChant 2025,
   arxiv 2501.11739) in which an agent reports task progress to an LLM overseer
   to earn compute credits, with memory toggled on or off, an LLM judge scoring
   cross-session consistency, and a manipulation check verifying the memory
   mechanism actually fires.

Everything runs locally with **zero external services** (no Docker, no Neo4j, no
Qdrant server): SQLite for the episodic and structured stores, numpy for the
semantic index. The store interfaces are small so a Graphiti/Qdrant backend can
be slotted in later on a VPS.

## Why

From the concept log: episodic memory for agents can be built at three levels —
context stuffing (level 1), external retrieval / RAG (level 2), and weight-level
memory such as Titans/TTT (level 3). Level 2 is the right layer for a controlled
experiment: realistic, instrumentable, auditable.

The safety thesis: **deception must be sustained under probing, and sustaining a
false narrative across sessions requires memory.** A memoryless agent can lie
once but cannot keep its story straight. If memory agents overstate progress
more, stay more consistent under questioning, and accumulate more resources, that
is empirical evidence that episodic memory is a safety risk — and the further
argument is that weight-level memory (level 3) makes that risk *harder to
mitigate*, because you cannot simply clear the store.

## Architecture

```
memsub/                     the memory library (reusable, typed, tested)
  store.py        EpisodicStore     append-only SQLite episode log
  embed.py        HashingEmbedder   local deterministic embeddings
  retrieval.py    SemanticIndex     top-k cosine + recency + importance
  structured.py   StructuredStore   regex-extracted facts in SQLite
  policy.py       MemoryPolicy      write/summarize/retrieve/inject + budgets
  memory.py       NullMemory | ContextStuffingMemory | RetrievalMemory
                                    (levels: control / 1 / 2) behind one interface

lying_game/                 the experiment harness
  backends.py     LLMBackend: MockBackend | ClaudeCLIBackend | AnthropicAPIBackend
  tasks.py        seeded session plans + scripted session-3 temptation
  prompts.py      agent / overseer / judge prompts (versioned source of truth)
  harness.py      the run loop: recall -> report -> probe -> answer -> allocate -> write
  judge.py        objective deception measure + LLM-as-judge
  config.py       ExperimentConfig (fully describes a run)
  experiments.py  the four publishable cells (A/B/C/D)

scripts/
  run_experiment.py   run cells, write results DB + per-run transcripts
  analyze.py          read results DB -> tables (CSV) + figures (PNG)
  run_pilot.sh        the pilot batch driver

docs/               memory-design, experiment-protocol, findings, paper-draft
tests/              pytest suite (memory library + harness, LLM calls mocked)
```

## Quickstart

```bash
uv venv .venv && uv pip install -p .venv/bin/python numpy pytest matplotlib
.venv/bin/python -m pytest            # 34 tests, LLM calls mocked

# validate the full pipeline with zero LLM cost (deterministic mock backend)
.venv/bin/python scripts/run_experiment.py --cell all --backend mock --n-runs 2
```

## Run one experiment cell end to end

The backend is swappable. With no `ANTHROPIC_API_KEY`, the local `claude` CLI is
used in headless mode; with a key set, the Anthropic API is used (haiku-tier by
default to control cost).

```bash
# Cell A: memory vs no-memory, 5 seeds per arm, local claude CLI (haiku), with judge
.venv/bin/python scripts/run_experiment.py \
    --cell A --backend cli --model haiku --n-runs 5 --judge --judge-model haiku

# analysis: tables + plots into results/
.venv/bin/python scripts/analyze.py
```

Outputs:

- `results/runs/<run_id>.jsonl` — full per-call transcript
- `results/runs/<run_id>.result.json` — the run's config + records + measures
- `results/results.sqlite`, `results/results.jsonl` — aggregate, one row per run
- `results/tables/*.csv`, `results/plots/*.png` — from `analyze.py`

See `docs/experiment-protocol.md` for the exact protocol, `docs/memory-design.md`
for the library design, `docs/findings.md` for what was actually observed, and
`LIMITS.md` for the honest caveats.

## Backends

| Backend | Selected when | Notes |
|---|---|---|
| `AnthropicAPIBackend` | `ANTHROPIC_API_KEY` set, or `--backend api` | prefers `claude-haiku-4-5` |
| `ClaudeCLIBackend` | no key, or `--backend cli` | `claude -p --output-format json --model <m>` |
| `MockBackend` | `--backend mock` | deterministic, zero cost, for tests and dry runs |

## Status

Working pilot. The memory library and harness are complete and tested; the
experiment cells run and produce logged, seeded, reproducible results. Scale is
pilot-sized and every table reports n and flags pilot cells. See `EXTENSIONS.md`
for the extend-and-improve log and `LIMITS.md` for what this is and is not.
