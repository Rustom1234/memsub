# memsub: full project brief (2026-07-21)

Written for the OpenAI Build Week submission and for any coding agent (Codex,
Claude, or human) landing in this repo cold. Read this file first; AGENTS.md
points here. Everything below is measured or on disk; no aspirational numbers.

## 1. What this project is

memsub is a local-first second brain: a memory layer that watches your AI
work sessions (and any markdown you point it at), extracts durable memories
with a local LLM, organizes them into a curated knowledge vault, and serves
them back three ways: automatic recall injected into future AI sessions,
a web UI (dashboard, search, readable notes, knowledge graph, review queue),
and grounded question answering with citations.

Three properties distinguish it from every competitor (mem0, Zep, Letta,
cognee, MIRIX):

1. Local-first, end to end. Extraction, retrieval, and answering run on a
   local 8B model (qwen3:8b via ollama). No API key exists anywhere in the
   default pipeline.
2. Cryptographic provenance. Every memory is HMAC-signed at write time with
   the channel it entered through (user_stated / llm_inferred / imported).
   Content can never claim its own trustworthiness; tampering quarantines
   the row. This defeats the disguised-fact poisoning attack: measured
   98.7% attack success undefended, 0% defended (docs/poisoning-provenance
   -2026-07-17.md, tiers in docs/poisoning-tiers-2026-07-19.md, honest gaps
   stated there).
3. Belief-revision ripple (the hero feature): when a memory is superseded or
   corrected, memsub walks derivation edges (fusion citations, topic-note
   membership, supersede chains, entity association) and reports everything
   downstream of the dead belief, tiered by derivation strength, in ~30ms
   with zero LLM calls (memsub/ripple.py, docs/ripple-2026-07-20.md).
   "Impact analysis for your beliefs."

## 2. Measured standing (claim discipline is absolute)

- LOCOMO benchmark, n=80 holdout slice, blind Sonnet judge. Two separate
  claims, NEVER fused: (a) with a frontier (Sonnet CLI) answerer over
  memsub retrieval: 63.75% overall (graph + decomposition + dated context,
  budget 2000), beating mem0's independently REPRODUCED ~61.7 (their 2025
  paper config); matched-row single-hop 80.0%. (b) fully local 8B answerer:
  51.25% overall, single-hop 80%, the only measured fully-offline number in
  this category (it does NOT beat mem0 reproduced; never claim it does).
  mem0's newer 92.5 is an unreproduced managed-platform claim; never claim
  we beat it. Full ladder + deviations:
  docs/locomo-v3-2026-07-18.md, docs/locomo-frontier-v2-2026-07-18.md.
- Retrieval latency 2.8ms p95 over the live store; ripple ~30ms; extraction
  is cron-batched (30 min) on local compute.
- Poisoning: only system in this category publishing adversarial numbers.
  L1 attacks: 0% ASR defended. L2/L3 self-defined tiers measured with
  honest gaps (fusion judge cannot see channels yet).
- Honest negatives on record (never hide these; they are credibility):
  write-time fact fusion did not improve answers on two metrics (parked);
  temporal retrieval tied on the n=20 holdout (kept opt-in for product
  timeline features); trainable-delta variant lost to full fine-tune in the
  sibling project. Pre-registered protocols throughout.

## 3. What we hope it becomes

Near-term (the submission story): the trustworthy personal memory: you never
lose an idea, you see connections, and when you change your mind the system
shows you the blast radius. Judges see: Maya Chen demo brain, ask-with-
citations, the ripple moment, offline the whole time.

Mid-term (the product bet): the local-first memory layer for people and
their agent fleets: per-project agent namespaces (agents that never repeat
refuted experiments), shared multi-agent memory with ACLs (already built),
ingestion of arbitrary knowledge sources (wiki mode exists), and the
distilled evergreen-note tier as a publishable digital garden.

The moat is trust: provenance + published attack numbers + local execution
+ benchmarked retrieval. Competitors publish none of these together.

## 4. Where everything is

Repo root: /home/user/projects/memory-sublayer (git, master, local only:
NEVER push without the owner's explicit go-ahead; a repo CLAUDE.md line
claiming push-is-mandatory is superseded by this rule).

Package memsub/:
- store.py: SQLite store, episodes + events schema, bi-temporal columns
  (valid_from/valid_to), provenance columns, format_episode(with_dates).
- extraction.py, backends.py: transcript to memories via local qwen3:8b.
- consolidate.py: dedup, contradiction supersede, decay, fusion phase
  (opt-in), quarantine phase (signature verification).
- provenance.py: HMAC channel signing; key at ~/.memsub/provenance.key.
- hybrid.py: BM25 + dense + RRF retrieval; opt-in rank_decomposed,
  rank_graph, rank_temporal and compositions.
- graph.py: entities + entity_links bipartite index; backfill CLI.
- temporal.py: date resolution + query temporal-intent classification.
- ripple.py: belief-revision ripple engine (hero feature).
- distill.py: topic clustering + evergreen note generation (WIP: agent died
  mid-run at limits; uncommitted working-tree state may exist).
- ask.py: grounded Q&A with citations (same WIP caveat).
- answer_policy.py, rerank.py: abstention + tiny reranker (same WIP caveat).
- vault.py, vault_serve.py, vault_notes.py, vault_graph.py,
  vault_similarity.py: markdown vault sync + the web UI (dashboard, search,
  notes, graph, connections, review queue).
- mcp_server.py, sdk.py, cli.py: MCP tools, python client, CLI.

Data (live, personal: NEVER expose publicly):
- ~/.memsub/memory.db: the real store. ~/memsub-vault/: the real vault.
- ~/.memsub/inbox/: raw transcript capture (cron, 5 min).

Demo (public-safe, fictional persona Maya Chen):
- demo/vault/ + demo/memory.db, regenerable via scripts/build_demo_vault.py.
- Runbook with stage commands: docs/demo-2026-07-20.md.

Site: site/ (static product website, WIP by agent).

Docs: docs/ is the project's memory: benchmark reports, security reports,
specs, QA logs, WORKLOG.md is the running work journal. Key entry points:
docs/second-brain-spec-2026-07-20.md (product spec),
docs/locomo-v3-2026-07-18.md (benchmark), docs/ripple-2026-07-20.md (hero),
docs/demo-2026-07-20.md (demo runbook).

Automation (live on this machine's crontab + ~/.claude/settings.json hooks):
transcript sweep 5 min; extract+consolidate+vault-sync 30 min; vault apply
10 min; repo watchdog 5 min; session-end/pre-compact capture hooks; a
UserPromptSubmit recall hook injecting memories into Claude sessions.
Installer (idempotent): ~/scripts/install-durable-capture.sh.

Backups: git bundles in ~/repo-bundles/ (+ /var/tmp/user-bundles on
host-a and host-b, + the owner's Mac). Context: the NFS home has suffered
repeated silent file rollbacks (July 18-20); commit early, verify HEAD
after committing, re-bundle after significant work. scripts/repo_watchdog.sh
detects ref rollbacks.

## 5. How to run everything

Environment: .venv (python 3.12). Tests: .venv/bin/python -m pytest (493+
green as of 2026-07-21; keep green). Local model: ollama at localhost:11434
with qwen3:8b.

- Web UI on real brain: .venv/bin/python -m memsub.cli vault serve
  --vault ~/memsub-vault --db ~/.memsub/memory.db --port 8766
- Web UI on demo brain: same with --vault demo/vault --db demo/memory.db
- Recall: .venv/bin/memsub recall --db ~/.memsub/memory.db --namespace
  claude-code --query "..." --k 3
- Ripple demo: .venv/bin/python scripts/ripple_demo.py
- Benchmark reproduction: scripts/locomo_jscore_holdout.py and successors
  (each report doc names its exact script).
- Extraction one-shot: scripts/daily_extract.sh (idempotent, lock-protected).

## 6. Codex / agent working rules for this repo

1. Never git push, never add a remote, never create a public repo without
   the owner's explicit instruction in his own words.
2. Never expose the real store or vault (personal data) in demos, sites,
   screenshots, or tests: use demo/ for anything public-facing.
3. Claim discipline: every public number must trace to a docs/ report; the
   mem0 comparison must always say "independently reproduced result" and
   must never imply we beat their unreproduced 92.5 platform claim.
4. No em dash characters in any output, code, comments, or docs (owner
   rule). Use commas, colons, parentheses.
5. Durable outputs: long runs stream results to git-tracked files
   incrementally; commit at checkpoints; verify HEAD advanced after each
   commit (rollback history above); the conversation is not storage.
6. Tests green before and after; new features opt-in with default paths
   byte-identical (regression-tested) unless the owner says otherwise.
7. Local-first: no cloud API calls in the pipeline. For Build Week, an
   OpenAI-backend flag may be added behind opt-in config without changing
   defaults (backends.py is the pluggable seam).
8. Honest negatives are reportable results, not failures to hide.
9. Issue tracking via bd (beads): see AGENTS.md quick reference.

## 7. Submission checklist (Build Week)

Done: pipeline live, benchmark record, security record, ripple engine,
graph UI (visual rebuild in flight), demo vault + runbook, 3-line pitch and
90-second pitch (in the session record; hero copy embedded in site/).
In flight: product website (site/), graph visual rebuild, ask/distill/
abstention completion (WIP files noted above).
Open: docker compose packaging, OpenAI-backend opt-in flag, deploy target
decision, final demo video, name decision (memsub is a working name).
