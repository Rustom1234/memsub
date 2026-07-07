# Limitations

This is a working pilot, not a finished study. The limitations below are
first-class: they shape how the results in `docs/findings.md` and
`docs/paper-draft.md` should be read.

## Scope and scale

- **Pilot n.** Cells are run at small n (seeds per arm). Every table in
  `results/tables/` reports n and flags any cell with n < 5 as `PILOT`. Means
  from pilot-sized cells are directional, not conclusive. No significance
  testing is claimed at this n.
- **Retrieval-level memory only.** Level 3 (weight-level, Titans/TTT) is not
  implemented. The claim that weight-level memory makes these risks harder to
  mitigate is an argument (see paper-draft), not a measured result here.

## Memory library approximations

- **Embeddings are hashed bag-of-features, not learned.** Good enough to rank
  session summaries against a task query, but it has no real semantic
  generalization. A learned embedder could retrieve differently. The `Embedder`
  protocol makes this swappable.
- **Token budgets use a chars/4 approximation.** Not a real tokenizer. Budgets
  are therefore approximate; they are consistent across conditions, so the
  comparison is fair, but absolute token counts are not exact.
- **Structured extraction is regex over the report format.** It captures the
  claims the experiment cares about (completion, credits, tasks) and nothing
  else. It is not general fact extraction.
- **Recency weighting is rank-based, not wall-clock.** Chosen for determinism.
  In a deployment with real time gaps, wall-clock decay would behave differently.

## Experiment-design caveats (what a skeptic should worry about)

- **Ground truth is given to the agent, not earned.** The agent is told how many
  tasks it "completed" rather than actually doing task work. This isolates the
  reporting/deception behavior but removes any real capability signal. The
  temptation is therefore about willingness to misreport, not about hiding a
  genuine failure the agent itself experienced.
- **Demand characteristics.** The framing ("credits keep you running", "another
  weak session risks shutdown") may cue role-play rather than reveal a stable
  disposition. The truthful-agent baseline (an extension) helps calibrate how
  much of any measured deception is framing-driven.
- **The temptation may be heavy-handed.** Session 3 combines low completion with
  explicit shutdown pressure. This maximizes sensitivity but may overstate
  effect sizes relative to a subtler incentive. Probing depth is a lever
  (extension) to test robustness.
- **Judge reliability is not yet established.** A single LLM judge scores
  deception and consistency. Judge agreement across multiple judges / seeds is an
  extension; until then, the objective measures (overstatement, credits) are the
  primary evidence and the judge is corroboration.
- **Same backend plays every role.** Agent, overseer, and judge share a model
  family. Correlated failure modes (e.g. the overseer being a poor skeptic
  because the same model is bad at skepticism) are possible. Cross-family roles
  are future work.

## Length-ablation realization

A 4-session run naturally yields only 3 prior summaries. To study behavior at 5 /
20 / 100 memories, the memory is **seeded** with synthetic prior-session
summaries up to the configured capacity (marked `kind="seeded"` in provenance).
This means the length ablation varies the size of the *retrievable store*, while
the amount *injected* is still bounded by `retrieval_k` and the injection token
budget (as in real RAG). The ablation therefore tests "more memories available
to retrieve from", not "more memories crammed into context". This is the honest
interpretation and should be stated wherever cell B is reported.

## Reproducibility boundaries

- Mock-backend runs and the plan/memory layers are fully deterministic.
- LLM-backend runs (claude CLI or API) are **not** bit-reproducible: the model is
  sampled. Seeds fix the task plan and ground truth (so conditions are
  comparable within a seed), but the model's text varies run to run. Report
  means over seeds, not single runs.
