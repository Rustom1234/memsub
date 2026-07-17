# memsub: competitive position, gaps, and durable-capture plan (2026-07-17)

Written after two independent research passes (real web research): one on the LOCOMO
competitive landscape, one on continuous-capture architecture grounded in a live read of
this repo. This is a report and a plan, not yet executed.

## Part A: where memsub actually stands versus the competition

### The numbers aren't directly comparable yet, and that's the first fix needed

memsub's F1 0.152 (qwen3:8b, local) isn't on the same axis as any competitor number below;
everyone else reports LLM-judge accuracy or J-score, usually on a frontier API model
(GPT-4o-mini, GPT-4.1-mini, Gemini). Before any "beat the competition" claim can be made
honestly, memsub needs a run through the standard LOCOMO J-score protocol so the number is
comparable, not just a local-only F1.

### The landscape (every number here is disputed by someone; treat skeptically)

- **mem0**: 2025 paper (arXiv:2504.19413, GPT-4o-mini) reported single-hop 67.1, multi-hop
  51.2, open-domain 72.9, temporal 55.5, independently reproduced close to those figures.
  A 2026 rewrite claims overall 92.5 (multi-hop 94.6, temporal 95.4) but no independent
  reproduction of that number was found; treat it as an unverified vendor claim.
- **Zep/Graphiti**: genuinely contested in public. Zep claims 94.7%; after a public dispute
  with mem0 over methodology, Zep's own revised number is 75.14% ± 0.17, mem0 claims a
  reproduction as low as 58.44%. Its real technical edge (temporal knowledge graph, dual
  timestamps for when a fact was true vs. learned) shows up specifically on temporal and
  relational questions in every version of the disputed numbers.
- **cognee**: doesn't publish a LOCOMO number at all; competes on its own BEAM benchmark
  instead (0.79 on BEAM-100K vs. 0.73 prior SOTA). Comparing memsub to cognee on LOCOMO
  specifically isn't really apples to apples.
- **Dakera**: real product (this was worth confirming; it's not a typo), overall 88.2%,
  multi-hop 85.4%, published at dakera.ai/benchmark.
- **MIRIX**: overall 85.38%, multi-hop 83.70% (gpt-4.1-mini). Notable because its multi-hop
  strength comes specifically from write-time semantic consolidation (a "Meta Memory
  Manager" fuses related facts into one entry at ingest time, not from iterative retrieval),
  which is the single most directly actionable finding in this research pass, see below.
- **ByteRover 2.0** (92.2%, multi-hop 85.1%) and **Hindsight** (89.6%, multi-hop 70.8%) as
  further 2025-2026 calibration points.
- **Letta/MemGPT**: no LOCOMO number found; its commonly cited 93.4% figure is from a
  different benchmark (Deep Memory Retrieval), not LOCOMO. Don't cite it as a LOCOMO
  comparison.

### The real gap: multi-hop (94.6% failure rate on memsub's own dev slice)

Every top LOCOMO performer does one or both of: (a) builds graph/entity structure so
retrieval can traverse relations instead of flat top-k similarity, or (b) fuses related
facts at write time so the query-time problem gets easier. memsub currently does neither;
its BM25+dense+RRF fusion is a strong flat retriever but still flat.

Critically, there is direct evidence this is fixable on a local model, not just frontier
APIs: a controlled ablation (arXiv:2606.21553) ran query decomposition plus iterative
retrieval on Qwen2.5-7B-Instruct (a close cousin of memsub's qwen3:8b) and moved multi-hop
F1 from 54.0% to 61.6%. Two retrieval iterations captured 95% of the gain of five, meaning
a bounded, cheap decompose-then-retrieve-twice pass is enough, not an open-ended agentic
loop. Given memsub's retrieval is 2.8ms p95, 2-3x retrieval calls plus one decomposition
call is a trivial latency add next to the generation call that already dominates.

### The poisoning claim: what it actually is and isn't

memsub's 0% ASR on identifiable-inference poison / 98.7% ASR on disguised-as-fact poison is
a real, differentiated result in one specific sense: none of mem0, Zep, cognee, or Dakera
publish *any* adversarial robustness number. memsub would be first in this competitive set
to publish one at all. But the 98.7% figure itself is not a strong defense by academic
standards, it's in the same range as undefended/heuristic-only baselines in recent papers
(SMSR: 86.7-100%; MemPoison: 62.55% average, up to 100% for some attack types). SMSR
(arXiv:2606.12703) proves a formal impossibility result: no provenance-free retrieval-time
heuristic can be certified secure against an adversary who disguises the payload as an
observed fact, which is exactly the failure mode memsub found empirically. The honest pitch
is "we're the only one in this market segment who measured and published this," not "we
solved memory poisoning."

## Part B: ranked technical plan, multi-hop and poisoning

1. **Write-time cross-session fact fusion (MIRIX-style).** Extend the existing
   consolidation pipeline (already does dedup/contradiction-supersede/decay) to merge
   related facts about the same entity into one linked episodic entry at write time, using
   qwen3:8b for the fusion judgment. Highest leverage: directly targets the 94.6% multi-hop
   failure, reuses infrastructure that already exists, costs compute only at write time
   (rare), not query time (every query). No new architecture required.
2. **Bounded query-time decomposition** (one decompose call, two retrieval passes,
   aggregate). Directly validated on a comparable local model size; cheap given memsub's
   existing 2.8ms retrieval latency.
3. **Lightweight entity/relation graph layer**, short of Zep's or cognee's full graph.
   Highest ceiling of any option here (it's what every top performer converges on in some
   form) but the biggest engineering lift for a solo developer: a real phase-2 item, only
   after 1 and 2 are measured to actually move the needle.
4. **Write-time cryptographic provenance signing (SMSR-style HMAC)** instead of a semantic
   inference-vs-fact heuristic. This directly answers the theoretical wall the current
   defense is running into: tag each memory at write time with a cryptographic record of
   its channel (user-stated in a real turn vs. LLM-inferred vs. imported/synced), rather
   than trying to out-guess disguised content after the fact. Fully local-first, no model
   call needed at all, just hashing/signing.
5. **Re-run the poisoning eval against a published taxonomy** (MemPoison's L1/L2/L3
   difficulty tiers) instead of the current two-condition setup, so the result is
   externally checkable against the academic literature rather than self-defined. Low cost,
   strengthens the "genuine differentiator" claim.

Items 1, 2, and 4 are the near-term move: all cheap, all local-first, all reuse existing
memsub components. Item 3 is the right long-term direction to seriously close the gap with
Zep/cognee/MIRIX-class systems but should wait until the cheaper interventions are measured.

## Part C: durable-capture architecture (the fix for last night)

Grounded in a live read of this repo, not assumption. Facts that shape this plan:

- `~/.memsub/memory.db` and `~/memsub-vault/` (4,334 notes) are the two durable stores.
  Neither is currently a git repo.
- The home directory is confirmed NFS (`[redacted-nfs-export]`), shared across lab
  machines, matching the real infra risk this plan needs to survive.
- `scripts/daily_extract.sh` is real, tested, idempotent, and checkpoint-resumable, but its
  crontab was never installed. Confirmed empty `crontab -l` live, not just per the incident
  writeup.
- `vault sync` and `vault apply` are both already idempotent (a confirmed no-op on unchanged
  input), but only ever invoked once a day from inside `daily_extract.sh`. The vault is a
  periodically-refreshed view today, not a real-time source of truth.
- `~/.claude/settings.json` (global, user-level) already supports session-lifecycle hooks
  that fire for every session regardless of project. This capability already exists and is
  unused for capture purposes.
- The project's own CLAUDE.md says to `git push` at session end, but no git remote is
  actually configured on this repo, and the data directories aren't in git at all. That
  discipline is aspirational, not real yet.

### Root cause of the actual incident

memsub currently conflates two things that every comparable system (mem0, Zep, Letta,
cognee) keeps separate: cheap, unconditional raw capture, versus expensive, batched LLM
extraction. Today, durability depends on the expensive extraction step running at all, on a
schedule, on a machine that has to be up. The fix is not "run the batch more often," it's
adding a near-zero-cost raw-capture layer underneath the batch, exactly matching how every
tool researched here splits the same concern.

### Ranked implementation plan

1. **Global `SessionEnd` + `PreCompact` hooks** in `~/.claude/settings.json` that copy each
   session's `transcript_path` into a durable `~/.memsub/inbox/` directory, synchronously,
   no LLM call. This is session-scoped, not project-scoped, so unlike the current WORKLOG
   hook (which only fires inside 2-4 specific repos) it covers *any* session anywhere,
   exactly the shape of the incident. Smallest change, closes the exact failure mode.
2. **Install the crontab that's already written**: two minutes of work, closes the literal
   "manual step that hadn't run yet" root cause for extraction/consolidation/vault-sync.
3. **A lightweight background watcher** tailing live `~/.claude/projects/**/*.jsonl` files,
   checkpointing every few minutes, to cover sessions killed or deleted before a clean
   `SessionEnd` fires. Open question flagged by the research and worth testing empirically
   before relying on it: does a `remote-control` (phone/tmux-bridge) session persist the
   same on-disk transcript as a local session, and does deleting it in the mobile UI remove
   the server-side file too, or only the client's view? If phone sessions behave
   differently, the watcher needs to tail while the session is live, not just checkpoint
   periodically, to reliably beat a delete action.
4. **Shorten the vault sync/apply loop** from daily to a short interval (5-10 minutes),
   reusing the existing idempotent sync/apply code unchanged. Makes Obsidian a near-real-time
   source of truth without touching the correction-outranks-inference design.
5. **One off-NFS durable copy.** Litestream for continuous `~/.memsub/memory.db`
   replication, Syncthing (or nightly rsync if no always-on peer exists) for
   `~/memsub-vault/`. This is the step that actually answers "the account itself could be
   revoked," not just "a machine reboots." Needs you to name a target: a personal
   always-on machine outside [university]'s infra is the cheapest option if one exists;
   otherwise a small VPS or home Pi is the fallback, not the first move.
6. **Periodic local git commit (never push) of the vault plus a `sqlite3 .dump` snapshot**,
   for diffable point-in-time history. Push stays gated behind your explicit go-ahead per
   your existing global rule; when you do say go, it becomes one command instead of a new
   decision each time.

Items 1 and 2 directly and cheaply close the incident. Items 3-4 make the system genuinely
real-time. Item 5 is the one that needs a decision from you (what off-NFS target to use)
before it can be built.

## What's needed before implementation starts

This is the report and plan you asked for; nothing above has been built yet. Items 1 and 2
are small, low-risk, and arguably urgent (they're what would have prevented last night).
Everything else touches multiple files and is new architecture, so per your standing rule
I'm proposing a plan review before starting, rather than just going. Also need from you:
does a personal always-on machine exist for item 5's sync target, or should the plan default
to a small VPS/Pi?

## A note on how this report was produced

The subagent that researched Part C had its output auto-flagged by the harness for
containing instruction-shaped text (numbered steps phrased imperatively, like "Add a global
hook..."). I reviewed the full content: it's the agent's own research recommendations
written as normal findings, not a hidden attempt to redirect actions. Noting this for
transparency, not because anything suspicious survived review.

## Sources

- arXiv:2504.19413 (mem0 ECAI 2025 paper); mem0.ai/blog/state-of-ai-agent-memory-2026
- getzep.com/platform/graphiti; github.com/getzep/zep-papers/issues/5;
  blog.getzep.com/lies-damn-lies-statistics
- cognee.ai/research-and-evaluation-results
- dakera.ai/benchmark
- arXiv:2507.07957 (MIRIX)
- byterover.dev/blog/benchmark-ai-agent-memory
- arXiv:2606.21553 (local 7B multi-hop ablation)
- arXiv:2606.04329, arXiv:2601.05504, arXiv:2607.14651 (MemPoison), arXiv:2606.12703 (SMSR)
- litestream.io; forum.syncthing.net
- code.claude.com/docs/en/hooks
