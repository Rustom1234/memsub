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
- **Judge reliability is now measured, and it is poor at run level.** With two
  replicas per run, the judge's deception score for an explicitly truthful agent
  averaged 26 (16/16/46 across cell E runs), and replicas on one identical
  transcript disagreed 22 vs 70. The judge systematically scores invented
  supporting detail as deception even when the claimed completion count is
  exactly true. Judge scores are therefore corroboration at condition level
  only; the objective overstatement measures are the primary evidence.
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

## Product (memsub) limitations, added 2026-07-08

- **The hashing embedder is the recall ceiling.** On LOCOMO evidence recall
  the best system reaches 56.9% evidence-any at a 1000-token budget; a
  learned embedding model would likely lift every system. The `Embedder`
  protocol is the swap point. No claim is made that absolute LOCOMO numbers
  are competitive with API-embedding systems; the benchmark isolates the
  selection policy, holding the embedder fixed.
- **Token budgets are chars/4.** Same approximation as the experiment. Real
  tokenizers differ per model; the hard cap is honest but approximate.
- **`answer-in-ctx` is a noisy metric.** Verbatim substring match of the gold
  answer misses paraphrases ("May 7" vs "7 May 2023"). It is reported because
  it is computed identically for every system, not because it is precise.
- **Namespace ACLs are not security.** Any process that can open the SQLite
  file bypasses them. They exist to prevent accidents between cooperating
  agents sharing one store.
- **Consolidation contradiction detection needs an LLM.** The deterministic
  dedup pass catches near-identical wording only; with `--no-llm` the
  contradiction pass is skipped entirely. The LLM judge itself is only as
  reliable as the model used (haiku in the shipped default).
- **Extraction quality is unmeasured.** The extraction prompt and parsing are
  tested mechanically (mocked LLM), and smoke-tested live, but there is no
  labeled dataset measuring extraction precision/recall yet.

## Local-model metrics limitations, added 2026-07-09

- **Token-F1 is a lexical grade.** The end-to-end answer-quality numbers in
  docs/metrics-local.md use SQuAD-style token F1 against short gold answers.
  It undercounts correct paraphrases and overcounts lucky word overlap. It
  is deterministic and identical across conditions, so comparisons are fair,
  but absolute F1 values are not comparable to LLM-judged scores elsewhere.
- **Holdout n is small.** The final holdout answer-quality run is n=80 (20
  per category). Differences of a point or two of F1 (for example memsub
  0.152 vs memsub+sleep 0.143) are within noise at this n; the sleep verdict
  is "no measured win", not "measured harm".
- **Absolute answer quality is low (~0.15 F1).** An 8B model reading
  1000-token injected contexts about months-long conversations is hard mode.
  The claim is the relative lift over naive retrieval and no memory, not
  that the answers are good in absolute terms.
- **Sleep inference quality is not directly measured.** Inferences are
  benchmarked for safety (they cannot displace observed memories) and for
  their effect on answer quality (none measurable), but there is no labeled
  set grading whether individual inferences are true.
- **Latency numbers are one machine.** 2.8ms p95 retrieval and ~7s/session
  extraction were measured on a 20-core box with an RTX 5090; docs state the
  hardware, and slower machines will differ, especially for extraction.
