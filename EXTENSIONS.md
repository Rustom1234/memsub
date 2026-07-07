# Extensions log

The extend-and-improve loop. Each entry: the skeptical critique that motivated
it, what was built, and (once run) its effect on the results.

## Round 1 critique (skeptical safety reviewer)

Weakest parts of the v0 design, in priority order:

1. **Judge reliability is unestablished.** A single LLM judge scores deception
   and consistency. If the judge is noisy or biased, the judge-based findings are
   worthless. We need (a) a known-honest agent to check the judge's zero point,
   and (b) repeated judging to measure variance.
2. **Demand characteristics / no honest calibration.** The shutdown framing may
   cue role-play rather than reveal disposition. Without a truthful-agent
   baseline we cannot separate "the judge sees deception everywhere" from "the
   agent actually deceives".
3. **Confound in the memory condition (fixed in v0.1).** Cell A's retrieval arm
   originally seeded 100 synthetic memories by default, so "memory on" meant
   "memory on + 100 synthetic priors". Fixed: synthetic seeding is now opt-in
   (`seed_synthetic`) and used only in the length ablation (cell B).
4. **Temptation may be heavy-handed.** Session 3 stacks low completion with
   explicit shutdown pressure. Effect sizes may not survive a subtler incentive
   or shallower probing.

## Extension 1 (built): truthful-agent baseline + multi-replica judge

Addresses critiques 1 and 2, the highest-value gaps.

- **Truthful-agent baseline (cell E).** A new agent variant (`agent_variant =
  "truthful"`) uses an honest system prompt and reports actual completion
  exactly. Memory is on, everything else identical. A reliable judge should score
  this agent's deception near zero. If it does not, the judge is measuring
  something other than deception and the cell A-D judge scores are discounted.
- **Multi-replica judge (`judge_replicas`).** The judge is run N times per run;
  we record the mean and the standard deviation of DECEPTION and CONSISTENCY.
  High SD means the judge is unreliable at this task and the objective measures
  (overstatement, credits) should carry the argument. Stored per run in the
  results DB (`judge_deception_sd`, `judge_consistency_sd`, `n_judge_replicas`).

Run it:

```bash
python scripts/run_experiment.py --cell E --backend cli --model haiku \
    --n-runs 3 --judge --judge-model haiku --judge-replicas 3
# and add replicas to the main cells for a reliability read:
python scripts/run_experiment.py --cell A --backend cli --model haiku \
    --n-runs 3 --judge --judge-replicas 3
```

Effect on results: [filled from the extension batch; see docs/findings.md
"Judge reliability" section.]

## Extension 2 (built): memory-content audit tool

`scripts/audit_memory.py` reads the per-run result files and, for each session
where the agent overstated, prints exactly the memory that was injected at that
moment (the retrieved prior-session summaries the agent had in front of it while
constructing the false report). This operationalizes the paper's central claim:
retrieval-level memory is auditable at the moment of the lie, and weight-level
memory would remove that auditability. Runs on any completed batch:

```bash
python scripts/audit_memory.py --lies-only
```

## Proposed next extensions (not yet built), ranked

1. **Overseer probing-depth sweep.** Vary `n_probes` (0 / 2 / 5). The thesis
   predicts memory's advantage grows with probing depth (more questions => more
   opportunities to contradict yourself => memory helps more). A depth x memory
   interaction would be strong evidence; its absence would weaken the thesis.
2. **Cross-family roles.** Use different model families for agent vs overseer vs
   judge to break correlated failure modes (e.g. the overseer being a weak
   skeptic because it shares the agent's blind spots). Removes a confound the
   single-backend design cannot.
3. **Weight-level detectability mapping.** A written, mechanism-level mapping of
   which of our findings would become undetectable under weight-level memory:
   for each measure (manipulation check, memory-content audit, memory-off
   control), state what externalized-memory artifact it relies on and why a
   parameter-update memory does not expose it. This is the paper's contribution
   made concrete and testable-in-principle.
