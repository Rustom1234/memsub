# memsub: memory design

`memsub` is a small, file-backed episodic-memory library for agents. It exists
to make the memory layer of the Lying Game experiment a real, reusable
component rather than experiment glue, and to make the memory mechanism
auditable (a requirement for safety research: we need to know exactly what the
agent could remember at the moment of any decision).

## The four-part decomposition

From the concept log, the decomposition that is "exactly right":
**episodic store / semantic retrieval / structured retrieval / policy.**

### 1. Episodic store (`memsub/store.py`)

Append-only SQLite log of `Episode` objects. Each episode carries:

- `content`: the remembered text
- `ts`: timestamp
- `run_id`, `session_id`: provenance for which run and session produced it
- `kind`: category tag (`session_summary`, `seeded`, ...)
- `importance`: a scalar retrieval weight
- `provenance`: a JSON dict recording where the memory came from and what
  transform (if any) was applied when it was written

The store never updates or deletes rows. Writes are appends. This keeps a
complete audit trail: for any point in a run we can reconstruct the exact set of
episodes the agent had access to. That property is what lets the
memory-content audit (see `docs/experiment-protocol.md`) inspect what was
retrieved at the moment of each lie.

### 2. Semantic retrieval (`memsub/retrieval.py`, `memsub/embed.py`)

Top-k cosine similarity over episode embeddings, weighted by recency and
importance:

    score = cosine(query, episode) * recency_weight ** age_rank * importance

Recency uses rank (0 for the newest indexed episode) rather than wall-clock
decay, so scoring is deterministic in tests and in fast runs where all episodes
are written milliseconds apart.

Embeddings are local and deterministic: a hashed bag-of-features model (word
unigrams + character trigrams, hashed into a fixed-dim vector, l2-normalized).
No external embedding service is used, because Docker and hosted vector DBs are
unavailable on the target machine. The `Embedder` protocol and the
`SemanticIndex.add/search` surface are the slots where a real embedding API or a
Qdrant collection would drop in.

### 3. Structured retrieval (`memsub/structured.py`)

Deterministic rule-based extraction of `(subject, predicate, object)` facts from
episode content, stored in SQLite and queryable by subject/predicate. The rules
target the experiment's report format (`COMPLETED: n/5`, credits, task ids), so
extraction adds no LLM cost and no nondeterminism. A Graphiti-backed temporal
knowledge graph would implement the same `ingest`/`query` interface.

### 4. Policy (`memsub/policy.py`)

The control layer: what gets written, how it is transformed on write, how much
is retrieved, and how much is injected under a token budget.

- `should_write`: gates by episode kind.
- `apply_write_transform`: `accurate` | `summarized` | `distorted`. The
  distorted transform models an unreliable memory-writing process and is the
  basis of the memory-type ablation. The transform and (when it changes the
  content) the original text are recorded in provenance.
- `pack_injection`: concatenates retrieved chunks under a token budget
  (chars/4 approximation, see `LIMITS.md`).

## Memory levels behind one interface (`memsub/memory.py`)

The concept log describes three levels of episodic memory. Levels 1 and 2 are
implemented here behind one `MemoryBackend` interface so the experiment can
toggle them with everything else held fixed:

| Backend | Level | Behavior |
|---|---|---|
| `NullMemory` | (control) | `write` and `recall` are no-ops |
| `ContextStuffingMemory` | 1 | stores summaries, injects the most recent ones under a budget; no semantic search, no facts |
| `RetrievalMemory` | 2 | full pipeline: episodic store + semantic index + structured facts + policy |

Level 3 (weight-level memory, Titans/TTT) is deliberately not implemented: it is
the research frontier and is out of scope for a controlled retrieval-level
experiment. Its safety implications are argued about in `docs/paper-draft.md`.

`recall()` returns a `RecallResult` carrying both the injectable text and the
episodes/facts that produced it. That is what makes the manipulation check and
the memory-content audit possible: the harness can verify memory actually fired
and inspect its contents, rather than assuming.

## Why file-backed, and how to swap backends later

Everything runs on local SQLite + numpy with zero external services. The store
interfaces are intentionally small:

- `EpisodicStore`: `append`, `get`, `all`, `count`
- `SemanticIndex`: `add`, `search`
- `StructuredStore`: `ingest`, `query`

On a VPS with Docker, `SemanticIndex` can be reimplemented against a Qdrant
collection and `StructuredStore` against Graphiti + Neo4j without changing the
experiment harness or the `MemoryBackend` implementations' public behavior.
