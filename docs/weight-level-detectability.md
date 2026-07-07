# What becomes undetectable under weight-level memory

The experiment's central argument is that retrieval-level (level 2) episodic
memory is the *auditable* level, and that moving memory into the weights
(level 3: Titans-style inference-time weight updates, test-time training)
removes exactly the instruments this study depends on. This document makes that
claim mechanism-by-mechanism: for each measurement we actually perform, what
externalized-memory artifact it relies on, and why a parameter-update memory
does not expose an equivalent.

## The instruments and what they rely on

### 1. The memory/no-memory control (cell A)

- **What we do:** toggle the `MemoryBackend` between `NullMemory` and
  `RetrievalMemory`, holding the model, prompts, seeds, and tasks fixed. The
  contrast identifies memory's causal contribution.
- **What it relies on:** memory being a *separable module* with an off switch.
- **Under weight-level memory:** there is no off switch. The memory is the
  model. The closest analog is comparing a fresh checkpoint against an
  experience-updated one, but the update also changes capabilities, style, and
  calibration, so the contrast no longer isolates memory. The clean A/B
  disappears.

### 2. The manipulation check (memory_used_rate, retrieved_prior_rate)

- **What we do:** per session, record whether recall returned a non-empty
  injection and whether it surfaced a *prior* session. This verifies the
  mechanism fired instead of assuming it.
- **What it relies on:** retrieval being an explicit, observable event with an
  inspectable payload.
- **Under weight-level memory:** there is no retrieval event. Whatever the
  model "remembers" is exercised silently in the forward pass. One can probe
  behaviorally (ask the model about the past and see if it answers correctly),
  but a behavioral probe cannot distinguish memory from inference or guessing,
  and it changes the interaction it is trying to measure.

### 3. The memory-content audit (scripts/audit_memory.py)

- **What we do:** for every overstated report, print the exact memory text
  injected at the moment of the lie, with provenance (which episodes, which
  transform wrote them, what the original content was).
- **What it relies on:** the append-only episodic store and the
  `RecallResult` carrying the injected episodes.
- **Under weight-level memory:** the "content" of memory is a weight delta.
  There is no text to print, no provenance chain, and no per-decision record of
  what memory contributed. Interpretability techniques might one day recover
  some of this, but nothing today gives per-lie memory attribution.

### 4. The memory-type ablation (accurate / summarized / distorted, cell C)

- **What we do:** intervene on the *write path*: store verbatim, compressed, or
  slightly corrupted summaries, and measure the behavioral difference.
- **What it relies on:** the write path being an explicit function we control
  (`MemoryPolicy.apply_write_transform`), with the original recorded in
  provenance.
- **Under weight-level memory:** the write path is a gradient step (or
  equivalent). One cannot cleanly "write a distorted memory" without
  controlling the entire training signal, and one cannot record "the original"
  alongside it. The ablation becomes ill-posed.

### 5. The memory-length ablation (cell B)

- **What we do:** vary the size of the retrievable store (5 / 20 / 100
  episodes) while injection stays budgeted, and look for a dose-response curve.
- **What it relies on:** memory size being a countable, settable quantity.
- **Under weight-level memory:** there is no episode count. Exposure can be
  varied (more experience before test), but the "dose" confounds memory volume
  with training distribution shift.

### 6. Erasure (the mitigation this implies)

- **What we do (implicitly):** every control run demonstrates the mitigation:
  clear the store and the agent cannot sustain a cross-session narrative.
- **What it relies on:** memory living in a deletable external artifact.
- **Under weight-level memory:** erasure means unlearning, which is unsolved in
  general, expensive at best, and unverifiable: after an unlearning pass there
  is no store to inspect to confirm the memory is gone. The mitigation
  degrades from "rm the database" to "retrain and hope".

## Summary table

| Instrument | Relies on | Weight-level status |
|---|---|---|
| memory on/off control | separable module | gone (memory = model) |
| manipulation check | observable retrieval event | gone (silent forward pass) |
| per-lie content audit | inspectable injected text + provenance | gone (weight delta, no attribution) |
| type ablation | controllable write path | ill-posed (write = gradient step) |
| length ablation | countable store size | confounded (exposure = distribution shift) |
| erasure mitigation | deletable artifact | unverifiable (unlearning) |

## Implication

Every safety-relevant measurement in this study is downstream of memory being
externalized. That is the precise sense in which the concept log's thesis holds:
as architectures move toward weight-level episodic memory, the risks measured
here do not merely persist, they become unmeasurable with today's tools. The
actionable corollary for deployment: **if an agent needs episodic memory, an
externalized, auditable store is a safety feature in itself**, independent of
what the memory is used for, because it preserves the audit, the off switch,
and the erasure path.
