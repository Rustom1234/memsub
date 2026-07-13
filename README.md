# memsub: local-first memory for local models

Persistent memory for AI agents that runs entirely on your own computer:
one `pip install`, one SQLite file on disk, and (with ollama) a fully local
model stack, so memories and transcripts never leave your machine. No
Docker, no vector database, no server process, no API key required. The MCP
server is stdio, spawned by the client on demand; nothing runs when your
agent is not running.

Built for the developer whose agent re-learns the same five facts about
their machine and repos every session, and who wants that memory private.

Measured, not claimed (docs/metrics-local.md, all numbers from executed
runs on real hardware, stated n): with qwen3:8b as both memory backend and
answerer on held-out LOCOMO long conversations, memsub lifts mean answer F1
to 0.152 vs 0.117 for naive top-k retrieval and 0.022 with no memory
(n=80, budget 1000). Retrieval itself is 2.8ms p95 warm over ~1450 memories
(no LLM in the search path), the store costs ~350 bytes per memory on disk,
and extraction runs at roughly 7s per 28-turn session on a local 8B model.

## What it does

- **Extraction**: turn raw conversation transcripts (JSONL or plain text,
  including Claude Code transcripts) into candidate memories, each with full
  provenance (source, turn span, model, confidence). `memsub extract <file>`
- **Consolidation**: an explicit offline pass that dedups near-identical
  memories, lets new facts supersede contradicted stale ones (LLM-judged,
  superseded copies kept for audit), and forgets low-value memories by decay.
  `memsub consolidate`
- **Sleep (opt-in)**: an offline pass that replays recent memories and
  writes session summaries, higher-order inferences, and entity links as
  first-class, fully audited memories. Inferences are marked as inferred and
  can never silently supersede an observed memory (measured: this rule holds
  98.0% clean recall under adversarial stale inferences where disabling it
  collapses to 0.0%, docs/benchmark.md). Honest caveat: sleep did not
  improve answer quality on the holdout benchmark, so it ships opt-in with
  no performance claim. `memsub sleep`
- **Namespaces**: scope memories per user / agent / session in one store,
  plus shared namespaces for multi-agent teams with per-agent read/write
  grants. `memsub ns create|grant|revoke|list`
- **Audit**: every memory can answer "why do you exist and when were you
  used": provenance, every retrieval, every injection, every supersede or
  forget decision. `memsub audit <id>`, `memsub audit --recent`
- **Obsidian vault export**: turn the store into a browsable, plain-markdown
  knowledge graph (no plugin needed): one note per memory with frontmatter,
  provenance, and wiki-links for shared entities and supersede chains.
  `memsub vault sync --vault ~/memsub-vault` (docs/obsidian-layer.md).
- **Benchmarked retrieval under a hard token budget**: the default search
  is a hybrid stack (stemmed BM25 + dense embeddings + temporal, speaker,
  and session signals fused with reciprocal rank fusion), dependency-free
  and measured with a dev/holdout protocol. Headlines from
  `docs/benchmark.md`: 73.8% evidence-all / 86.7% evidence-any at a
  1000-token budget on held-out LOCOMO conversations (vs 42.9% / 55.9% for
  dense-only), and 98.0% clean recall with consolidation (right fact
  injected, no contradicting stale copy) where naive top-k gets 0.0%.
  Cross-checked on a second, independently-authored benchmark
  (LongMemEval_S, pre-registered n=30, `docs/benchmark-external.md`): the
  LOCOMO-frozen parameters transferred without collapsing (token-F1 0.238 vs
  naive 0.206; semantic-judge accuracy a statistical tie at 14/30 vs 15/30),
  a no-regression result, not a superiority claim. `memsub search "<query>"`

## Install

```bash
git clone <this repo> && cd memory-sublayer
uv venv .venv && uv pip install -p .venv/bin/python -e .
.venv/bin/python -m pytest   # all tests local, LLM/embedding calls mocked
```

Dependencies: numpy. That is the list.

## Wire into Claude Code (one command)

```bash
claude mcp add memsub -- /home/user/projects/memory-sublayer/.venv/bin/memsub mcp
```

(Adjust the path to wherever your venv lives: `claude mcp add memsub -- $(pwd)/.venv/bin/memsub mcp`.)

That exposes four tools to Claude Code: `add_memory`, `search_memory`,
`list_memories`, `forget_memory`. All memories land in
`~/.memsub/memory.db` (override with `MEMSUB_DB`). Set `MEMSUB_NAMESPACE`
and `MEMSUB_AGENT` in the server env to scope an agent to its own namespace.

## Python SDK

```python
from memsub.sdk import MemoryClient

client = MemoryClient()   # ~/.memsub/memory.db
client.add("This repo disables ROS pytest plugins via pytest.ini")
print(client.inject("how do we run tests here?", budget_tokens=300))
```

`MemoryClient` enforces namespace ACLs and logs every retrieval and
injection to the audit trail.

## CLI

```bash
memsub extract session.jsonl --dry-run     # transcript -> candidate memories
memsub extract session.jsonl               # ... and write them to the store
memsub extract sessions/*.jsonl            # batch: multiple files/globs, --min-turns filters thin ones
memsub consolidate --dry-run               # propose dedup/supersede/forget
memsub ns create shared:team --owner lead  # shared namespace with ACL
memsub search "how do we run tests?"       # hybrid retrieval (v2, default)
memsub audit --recent                      # what has memory been doing?
memsub audit <memory-id>                   # why does this memory exist?
memsub vault --db <db> sync --vault DIR    # export to an Obsidian-compatible vault
memsub vault --db <db> apply --vault DIR   # read human edits/verdicts back into the store
memsub mcp                                 # the MCP stdio server
```

## Local models (ollama)

memsub runs entirely on your machine, including the model. With
[ollama](https://ollama.com) running locally it needs zero cloud calls and
zero API key:

```bash
ollama pull qwen3:8b            # extraction / consolidation (fast)
ollama pull deepseek-r1:32b     # optional: higher-quality judgment calls
ollama pull nomic-embed-text    # real local embeddings (optional; the
                                 # default hashed embedder needs no model)

memsub extract session.jsonl --backend ollama              # qwen3:8b
memsub extract session.jsonl --backend ollama --model deepseek-r1:32b
memsub search "how do we run tests?" --embedder ollama     # nomic-embed-text
```

`--backend auto` (the default) picks ollama automatically whenever a daemon
answers at `localhost:11434` (override with `$OLLAMA_HOST`) and no
`ANTHROPIC_API_KEY` is set. qwen3's `<think>...</think>` reasoning blocks are
stripped before JSON parsing, so extraction and consolidation see clean
output. Embeddings stay on the zero-dependency hashed embedder by default
(`--embedder hashing`); `--embedder ollama` opts into real embeddings, and
`--embedder auto` uses ollama when available.

## LLM backends

Extraction and the consolidation contradiction judge need an LLM. The
backend is swappable (`memsub/backends.py`):

| Backend | Selected when | Notes |
|---|---|---|
| Anthropic API | `ANTHROPIC_API_KEY` set, or `--backend api` | prefers `claude-haiku-4-5` |
| ollama | a local daemon answers, no API key, or `--backend ollama` | `qwen3:8b` by default, zero cloud calls |
| claude CLI | no key, no ollama, or `--backend cli` | headless `claude -p`, which every Claude Code user already has |
| mock | `--backend mock` | deterministic, zero cost, used by tests |

Everything else (retrieval, storage, consolidation dedup and decay) is
local: stemmed BM25 + embeddings (hashed bag-of-features by default, or real
ollama embeddings) + numpy cosine + SQLite, fused with reciprocal rank
fusion. No embedding service, no API required for recall. The v1 dense-only
path stays available via `retrieval="dense"` (SDK) or `--retrieval dense`
(CLI/MCP).

## Design

The four-part decomposition: episodic store (append-only SQLite; supersede
and forget are events, never row deletions) / semantic retrieval (local
deterministic embeddings) / structured facts / policy (what to write, what
to retrieve, what fits the injection budget). `docs/memory-design.md` has
the details, `docs/benchmark.md` the measured numbers, `docs/positioning.md`
the honest competitive picture, `LIMITS.md` the caveats.

## Origins

This repo began as an AI-safety experiment on whether episodic memory
enables sustained deception (the Lying Game). That work is preserved on the
`lying-game-experiment` branch and under `experiments/lying-game/`. The
audit-first design is inherited from it: a safety experiment needs to know
exactly what an agent could remember at any moment, and a developer tool
benefits from the same discipline.
