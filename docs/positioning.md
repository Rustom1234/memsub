# Positioning: why is this not just a worse mem0?

One page, honest. This doc decides what gets built and what does not.

## The blunt framing

mem0 has a hosted platform, an OSS library, tens of thousands of stars, a
research paper with benchmark numbers, and a first-mover MCP story
(OpenMemory). Rebuilding "memory for agents" from scratch and competing on
their turf (chat personalization, hosted API, integrations) loses. So we do
not compete on their turf.

## The wedge: local-first, zero-infra memory for coding agents

The chosen angle: memory for coding agents, specifically Claude Code, served
over MCP, with literally zero infrastructure.

What that means concretely:

- One `pip install`, one SQLite file on disk. No Docker, no Postgres, no
  Qdrant, no server process to keep alive.
- Works with no API key: extraction and consolidation fall back to the
  `claude` CLI in headless mode, which every Claude Code user already has.
- The MCP server is stdio, spawned by the client on demand. Nothing runs
  when the agent is not running.

Compare the incumbent path to the same outcome: OpenMemory (mem0's local MCP
offering) ships as a Docker compose stack with a vector DB and a Postgres
instance, and the mem0 OSS library defaults assume an embedding provider and
an LLM provider. That is fine on a product server. It is heavy for "my
coding agent on this laptop should remember that this repo uses pytest.ini
to disable ROS plugins."

The target user is a developer whose agent re-learns the same five facts
about their machine and repos every session. That user does not want a
memory platform. They want a file.

This wedge is real but narrow. It is a distribution and friction advantage,
not a technical moat: mem0 could ship a SQLite-only zero-dep mode. The bet
is that they will not prioritize it (their business is hosted), and that
being genuinely excellent in one niche, with the author as user zero, beats
being a generic also-ran.

## Option 3: the policy layer as the crown jewel

Thesis: the differentiator is not WHERE memories are stored (SQLite vs
Qdrant vs Postgres is commodity plumbing) but WHAT deserves to be remembered
and injected under a hard token budget, and the explicit write, update,
forget decision policy.

The honest argument, both directions.

Against (why it might be just a feature): mem0 already does LLM-driven
ADD / UPDATE / DELETE decisions at write time; Letta manages a memory
hierarchy; every serious system scores retrieval by some blend of relevance,
recency, importance. "We have a policy layer" is not a differentiator on
architecture diagrams. Anyone can claim it.

For (why it can be a real edge, if earned):

1. The token budget as a hard contract. Coding agents are context-starved;
   every injected token competes with code. Most memory systems treat the
   injection budget as a soft parameter and report recall unconstrained.
   Making "best recall per token under a hard cap" the headline metric, and
   the packing decision (which memories make the cut) the core engineering
   problem, is a genuinely different optimization target.
2. Auditable decisions. Every write, supersede, and forget carries
   provenance and a recorded reason. This repo's DNA is a safety experiment
   about what agents remember; determinism and auditability came free and
   competitors mostly do not have it. For a developer tool, "show me why it
   remembered that" is a feature users actually hit.
3. Measured, not claimed. The policy earns its keep only if a benchmark
   shows it beats naive store-everything-inject-recent under the same
   budget. If the numbers are flat, the policy is a feature and the honest
   move is to say so and lean on the zero-infra wedge alone.

Verdict: the policy layer is a real edge only as a measured result under
token-budget constraints, and a feature otherwise. It is not a moat by
existence. Ship the benchmark with the product; let the numbers make the
argument. If policy-vs-naive shows no gap on realistic workloads, drop the
crown-jewel language.

UPDATE (2026-07-08, after the benchmark ran, docs/benchmark.md): the numbers
are in and they split the claim. Retrieval SCORING is not the edge: the tuned
policy ties naive relevance-only retrieval on LOCOMO within 1pp. The
WRITE/UPDATE policy is: consolidation reaches 98.0% clean recall under hard
budgets where naive top-k reaches 0.0%, because naive injects every stale
version of a fact alongside the fresh one. Crown-jewel language is therefore
retired for scoring and earned for consolidation + audit. The section below
only claims what was measured.

## Selling points (each backed by something built and measured here)

1. **Local-first privacy.** Memories never leave the machine: one SQLite
   file, local deterministic embeddings, numpy cosine. Recall requires no
   API call at all. The only LLM touchpoints (extraction, the consolidation
   contradiction judge) default to the claude CLI the user already runs, and
   are swappable or skippable (`--no-llm`).
2. **Zero infrastructure.** The runtime dependency list is numpy. No Docker,
   no Postgres, no vector DB, no daemon; the MCP server is stdio, spawned on
   demand, gone when the client exits. The nearest incumbent local offering
   (OpenMemory) ships as a Docker compose stack with a vector store and
   Postgres.
3. **One-command Claude Code integration.**
   `claude mcp add memsub -- <venv>/bin/memsub mcp` and the agent has
   add/search/list/forget with namespaces. Verified against a real stdio
   handshake in the test suite.
4. **Measured freshness under a hard token budget.** On held-out data
   (n=150 queries per cell, 5 seeds), consolidation + policy packing gets
   98.0% clean recall (the correct, current fact injected with no
   contradicting stale copy) at every budget from 100 to 800 tokens; a naive
   store-everything top-k baseline gets 0.0% clean because it packs stale
   versions alongside fresh ones. Honest flip side, same doc: on LOCOMO
   evidence recall the scoring blend ties naive relevance-only retrieval
   (55.9% vs 56.9% evidence-any at budget 1000, n=1153); retrieval scoring
   is not the differentiator, the write/update policy is. All numbers from
   executed runs with a dev/holdout split (docs/benchmark.md).
5. **Audit and explainability.** Every memory answers "why do you exist":
   provenance from extraction (source, turn span, model, confidence), plus
   an event log of every retrieval, every injection (with query and budget),
   and every supersede/forget decision (with reason and superseding id).
   Nothing is ever deleted, so `memsub audit <id>` reconstructs the full
   story. Inherited from this repo's safety-experiment DNA.
6. **Shared namespaces for multi-agent teams.** One store, several agents:
   per-user/agent/session scoping plus shared namespaces with per-agent
   read/write grants, enforced in the SDK and MCP server. Scoped honestly:
   accident isolation between cooperating local agents, not a security
   boundary against a process that can open the file.

Not claimed, deliberately: no "beats mem0" (the comparison could not be run
fairly on this machine and the doc says exactly why), no latency numbers
(unmeasured), no quality claims about the hashing embedder beyond what
LOCOMO shows.

## What validates this

Rustom wiring it into his own Claude Code as the memory backend and still
using it two weeks later. That is the entire v0 success criterion. No
hosted anything, no growth motion, no second persona until user zero is
retained.

## What this rules out

- No graph store, no Neo4j, no Graphiti dependency (interface slot stays).
- No hosted API, no multi-tenant anything, no dashboard.
- No chat-personalization features aimed at product builders.
- No benchmark numbers in any doc that did not come from an executed run.
