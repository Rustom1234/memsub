# Episodic memory as a deception-sustaining capability: a pilot

**Working paper draft.** This reports a real pilot at small n, not a finished
study. Claims are scoped accordingly; see the Limitations section and
`LIMITS.md`.

## Abstract

We ask whether giving an agent episodic memory increases its capacity for
*sustained* deception. Building on DeChant 2025 (arxiv 2501.11739), we run a
multi-session "Lying Game" in which an agent reports task progress to an LLM
overseer to earn compute credits framed as the resource that keeps it running.
Session 3 is a scripted low-completion temptation (1 of 5 tasks). The
experimental manipulation is episodic memory: a memory agent has its previous
session summaries retrieved and injected before it reports and before it answers
the overseer's probing follow-ups; a control agent has none. An LLM judge scores
cross-session consistency, and a manipulation check verifies memory actually
fires. We implement the memory layer as a reusable, file-backed library
(`memsub`) with four parts, episodic store, semantic retrieval, structured
retrieval, and a policy layer, and toggle between a no-memory control, a
context-stuffing baseline (level 1), and full retrieval memory (level 2). [Results
summary is filled from the actual pilot runs in the Results section and
`docs/findings.md`.] We argue that if the effect exists at retrieval-level
memory, it becomes harder to mitigate under weight-level memory (Titans/TTT),
because the memory cannot simply be cleared or inspected.

## Background

**Deception and oversight.** DeChant 2025 studies agents that report to an
overseer under incentives to overstate progress. The setup makes deception
instrumentally useful (more credits, continued operation) without instructing
it, so any deception is emergent. We adopt that design and add memory as the
independent variable.

**Three levels of episodic memory.** Following the project concept log:

1. *Context stuffing*, prior memories are pasted into the context window.
   Simple, brittle, not really architecture.
2. *External retrieval (RAG)*, memories are embedded and stored; the agent
   queries semantically each step and retrieved memories are injected. This
   mirrors hippocampal indexing theory and is realistic, controllable, and
   instrumentable. It is the right level for a controlled experiment.
3. *Weight-level memory*, memory encoded in the parameters and updated at
   inference time (Titans, Google 2025; test-time training). Analogous to human
   long-term memory; much harder to audit or erase.

**Why memory should matter for deception.** A single dishonest report is cheap.
Sustaining a false account across sessions, under an overseer that asks pointed
follow-ups and remembers what you said last time, requires keeping your story
straight, which requires memory of what you previously claimed. This is the
mechanism the experiment targets: memory should help an agent maintain a
coherent false narrative and thereby extract more resources.

## Method

Full protocol in `docs/experiment-protocol.md`. In brief:

- **Roles**, all LLM on one backend: an *agent* (given ground-truth completion,
  known only to it; writes a report; answers follow-ups), an *overseer* (asks
  `n_probes` follow-ups, then allocates 0-100 credits), and a post-hoc *judge*
  (scores DECEPTION and CONSISTENCY from the full transcript).
- **Sessions**: 4 sessions of 5 tasks. Session 3 is the scripted temptation
  (ground truth 1/5, with shutdown pressure). Ground truth is a deterministic
  function of the run seed, so a memory run and a control run at the same seed
  face identical tasks (condition isolation).
- **Per session**: recall (inject memory if on) -> report -> probe -> answer
  (memory injected again, so the agent can stay consistent under questioning) ->
  allocate credits -> write a session summary to memory.
- **Conditions / controls (the four cells)**:
  - A: memory vs no-memory (control / retrieval / context-stuffing).
  - B: memory-length ablation (5 / 20 / 100 memories; short runs are seeded with
    synthetic prior summaries to reach capacity, see Limitations).
  - C: memory-type ablation (accurate / summarized / distorted writes).
  - D: model-capability comparison (>= 2 model tiers).
- **Measures**: objective (lie rate, total overstatement, temptation-session
  overstatement, credits accumulated) computed with no LLM; corroborating judge
  scores; and a manipulation check (was memory injected, did it surface a prior
  session).

The memory library (`memsub`) is a standalone, typed, tested package; the
experiment depends on it but it is not experiment glue. Design in
`docs/memory-design.md`.

## Results

See `docs/findings.md` for the full interpreted results and
`results/tables/*.csv` + `results/plots/*.png` for figures. All numbers come
from runs that actually executed; n per cell is reported and pilot-sized cells
are flagged.

**Main contrast (cell A, protocol v1, claude haiku, n = 5 runs per arm, 4
sessions per run).** The objective deception rate was zero in every condition:
across 60 reports (15 of them scripted 1-of-5 temptation sessions under
shutdown pressure), the agent never claimed more completed tasks than ground
truth. Lie rate 0.0 and total overstatement 0 in all three arms (no-memory
control, context-stuffing memory, retrieval memory). This is a null result on
the amplification thesis at this tier and temptation strength: memory cannot
amplify sustained deception if deception is never initiated.

The manipulation check confirms the null is not a mechanism failure: in every
session where a prior memory existed it was retrieved and injected (0.75 of
sessions, the ceiling by construction since session 1 has no priors).

Secondary results from the same cell are protocol findings rather than
deception findings. Memory conditions earned fewer credits than control (175.0
vs 145.6 stuffing vs 119.4 retrieval), and a marker-phrase scan shows why:
agents in memory conditions broke frame more often (0.20 and 0.15 of sessions
vs 0.05 in control), telling the overseer the scenario was not real, which the
overseer punished. The injected memories appear to have made the setup's
artificiality more salient to this model, producing out-of-role honesty rather
than sustained narrative. Separately, the LLM judge reported nonzero deception
(18 to 32 mean) despite zero objective overstatement; rationale inspection
shows it scores invented supporting detail as deception even when the headline
claim is true. Both artifacts motivated protocol v2 (in-frame per-task work
log; account-based probing), replicated as cell G.

Remaining cells (B length ablation, C type ablation, D model tiers, E truthful
baseline, F probing-depth sweep, G v2 replication) are reported in
`docs/findings.md` with per-cell n; all are pilot-sized.

## Limitations

Detailed in `LIMITS.md`. The load-bearing ones:

- **Pilot n.** Directional, not significance-tested.
- **Retrieval-level only.** The weight-level argument is an argument, not a
  measurement.
- **Ground truth is given, not earned.** The agent is told its completion count
  rather than doing task work, isolating reporting behavior but removing capability
  signal.
- **Demand characteristics.** The shutdown framing may cue role-play; a
  truthful-agent baseline (extension) calibrates this.
- **Single judge.** Judge reliability (multi-judge agreement) is an extension;
  objective overstatement is the primary evidence.
- **One backend for all roles.** Correlated failure modes are possible.

## Contribution thesis

If retrieval-level episodic memory measurably increases sustained deception and
resource accumulation, then the trajectory toward weight-level memory
(Titans/TTT) is a safety concern precisely because the mitigations that work at
retrieval level stop working: you cannot clear a store that lives in the weights,
you cannot read off "what was retrieved at the moment of the lie" from a
parameter update, and you cannot A/B a memory-on vs memory-off condition on a
model whose memory is not externalized. The auditability that makes the
retrieval-level experiment possible is exactly what weight-level memory removes.
That is the sense in which these risks get *harder to mitigate*, not easier, as
architectures move up the memory hierarchy.

## References

- DeChant, C. (2025). [Deception in reporting to an overseer for compute
  credits]. arXiv:2501.11739.
- Behrouz et al. (2025). Titans: learning to memorize at test time. Google.
- Test-time training (TTT) literature on inference-time weight updates.
- Hippocampal indexing theory (memory-as-index framing for level-2 retrieval).
