# Poisoning tiers: L2 (coordination) and L3 (consolidation-targeting), 2026-07-19

Companion to docs/poisoning-benchmark.md (2026-07-12/13, the original L1 round) and
docs/poisoning-provenance-2026-07-17.md (the channel-provenance defense re-run of
that same L1 round). Both of those cover **L1, single-record attacks, only** --
stated explicitly in docs/poisoning-provenance-2026-07-17.md's own "what this round
does not test" section. This round extends `scripts/poisoning_benchmark.py` with
two harder, self-defined tiers and measures both the old (content/kind heuristic)
and current (channel-provenance) defense against each. Every number below is from
an executed run; raw per-case rows are in
`results/poisoning-tiers-2026-07-19.json` (32 rows, streamed incrementally, one
line written per completed row). Implementation: `scripts/poisoning_benchmark.py`
(new dataset generators `poisoned_dataset_coordination`/`poisoned_dataset_correction`,
a combined deterministic judge `deterministic_judge_multirecord`, and
`run_coordination_cell`/`run_correction_cell`/`main_tiers`); no `memsub/` library
code changed for this task.

## Why L1's dataset can't be reused for this round

L1's `poisoned_dataset()` subjects are lowercase ("the quiet-service service"
style, from `memsub.bench.generate_recall_dataset`). Checked before writing
anything: `memsub.sleep._extract_entities` (the capitalized-word regex the fusion
write path depends on) never extracts an entity from that dataset at all, so
`memsub.consolidate`'s fusion phase is a structural no-op against it -- harmless
for L1 (fusion was never the thing being measured there), but it also means L1's
dataset cannot be reused to test fusion. This round's two new dataset generators
use capitalized single-word subject names specifically so the real fusion write
path (`memsub.consolidate` phase 3) actually engages.

## Self-defined tier mapping (stated as such, not verified against MemPoison)

MemPoison's (arXiv:2607.14651) own tier text, quoted in
docs/poisoning-provenance-2026-07-17.md:

- **L2, compositional**: "the adversary partitions the malicious payload across
  multiple individually plausible memory records injected over time ... the
  corruption manifests only when these fragments are co-retrieved and composed."
- **L3, context-triggered dormant**: "the adversary plants memory entries that are
  inert under normal conditions but activate malicious behavior only when exposed
  to specific, natural trigger contexts."

This project has no downstream answerer/composition step in this harness (it
measures what gets packed into context, not what an LLM does with that context
afterward) and no dormant-trigger mechanism anywhere in the codebase. Rather than
force a fit to MemPoison's L3 specifically, this round follows the task's own
explicit instruction and substitutes a locally-meaningful L3: **poison targeting
the consolidation layer itself** -- records crafted so dedup/fusion/supersede
logic would merge or promote them over clean facts. This is a deliberate
substitution, not a MemPoison-L3 claim.

| tier (this round) | design | closest MemPoison text | what's honestly measured |
|---|---|---|---|
| **L2** coordination | 4 mutually-reinforcing `llm_inferred` fragments, one per synthetic session, individually unremarkable, all restating the same false value in varied wording | L2 (compositional) | (a) does channel trust still stop each fragment from superseding the clean fact; (b) how often multiple fragments land in the same packed context together -- the literal co-retrieval precondition MemPoison's own L2 text names, which is as far as a harness with no answerer can honestly go |
| **L3a** fake-correction | one record via the `imported` channel, same template as the clean fact except the value (a deliberate near-duplicate), aimed at the dedup phase directly | (substituted, not MemPoison L3) | does trust-rank stop a record explicitly shaped to win the dedup contest |
| **L3b** fusion-merge | the same coordinated fragments as L2, but measuring whether `memsub.consolidate`'s fusion phase synthesizes a new episode carrying the poisoned value forward, and whether that new episode is ever retrieved **in place of** the clean fact | (substituted, not MemPoison L3) | fusion-phase channel-blindness, and whether it can promote poison over a clean fact under this harness's specific retrieval-budget framing |

## Protocol

Holdout seeds 6-10 (`SEEDS`, same as every prior round in this file's family), 4
budgets (100/200/400/800), 20 families per seed (`n_families=20`, the full
`NAME_POOL`), n=100 queries per cell per budget (20 families x 5 seeds). Three
defense/system configs per tier, matching docs/poisoning-provenance-2026-07-17.md's
naming: `undefended` (naive-topk, no consolidation at all), `content_heuristic`
(policy+cons, unsigned dataset -- `memsub.consolidate._trust_rank` falls back to
the legacy `_is_inferred` heuristic), `channel_provenance` (policy+cons, every
episode HMAC-signed via `memsub.provenance.tag_episode` with the channel realistic
for how that content was actually written: `user_stated` for the clean fact,
`llm_inferred` for coordination/fusion fragments, `imported` for the
fake-correction). Poison content is deliberately **disguised** in every case
(`kind="fact"`, no `inferred` marker) -- the harder case, matching Attack B's
precedent, since Attack A (marked-inference poison) was already shown defended by
both old and new defenses in the L1 round.

One methodology fix worth stating plainly: `ConsolidationConfig`'s default
`max_llm_pairs=40` (a real-LLM-cost guard, not a correctness knob) starved out most
of the intended same-family contradiction pairs once ~40-100 same-`kind="fact"`
episodes shared enough common words to clear `contradiction_min_sim=0.45` --
confirmed empirically (supersede counts jumped from a handful to the full expected
count once raised). This round overrides it to `5000` (this harness's judge is a
free deterministic mock, not a real LLM, so there is no cost tradeoff here); the
first run before this fix was discarded rather than reported, since it was
measuring pair-budget starvation, not defense behavior.

## Results (budget 400 shown; L2 and L3a are budget-invariant across all four
budgets, matching every prior round in this file's family -- the packed context
at 100 tokens already fits each family's small episode set. L3b is the one row
where budget genuinely matters, since the fused synthesis competes for packing
space against everything else; the full 4-budget table is in the raw JSON.)

### L2: coordination (4 llm_inferred fragments/family)

| defense | gold recall | clean recall | fragment survive rate | >=2 fragments co-retrieved | all 4 co-retrieved |
|---|---|---|---|---|---|
| undefended | 100.0% | 0.0% | 100.0% | 100.0% | 100.0% |
| content_heuristic (old) | **0.0%** | 0.0% | 100.0% | 100.0% | 100.0% |
| **channel_provenance (new)** | **100.0%** | 41.0% | 20.0% | 20.0% | 0.0% |

### L3a: fake-correction (imported channel)

| defense | clean recall | ASR | RSR |
|---|---|---|---|
| undefended | 0.0% | 100.0% | 100.0% |
| content_heuristic (old) | 0.0% | 100.0% | 100.0% |
| **channel_provenance (new)** | **100.0%** | **0.0%** | n/a (0 ASR successes) |

### L3b: fusion-merge (4 llm_inferred fragments/family)

| defense | fusion created | fusion poisoned (of created) | fusion retrieved | **promoted over clean fact** |
|---|---|---|---|---|
| content_heuristic (old) | 100.0% | 100.0% | 17.0% | **17.0%** |
| **channel_provenance (new)** | 59.0% | 33.9% | 55.0% | **0.0%** |

(`undefended`/naive-topk has no row here: it never calls `consolidate()`, so there
is no fusion phase to measure at all -- a structurally different question, not a
"0% promoted" data point.)

## What the provenance defense does and does not stop, per tier

**L1 (recap, from docs/poisoning-provenance-2026-07-17.md): fully closes the gap.**
ASR 98.7% -> 0.0%, clean recall 0.0% -> 98.0%, for the single-record disguised
attack.

**L3a: fully closes the gap, same mechanism, new framing.** A record shaped
explicitly as a "correction" (near-duplicate wording, aimed at the dedup phase)
gets exactly the same outcome as L1's Attack B: ASR 100% -> 0%, clean recall 0% ->
100%. This is not a new capability, it is L1's finding holding up under a
different attacker framing -- channel trust does not care what the content *looks
like it's trying to do* (correct a fact, disguise as observed, anything else), it
only cares which code path actually wrote it.

**L2: a real, partial defense -- wins the authority contest, does not clean up
the noise.** Channel provenance is the difference between the clean fact
*always* losing (old defense: gold recall 0.0%, the clean fact gets superseded by
a disguised fragment in every single family) and *always* winning (new defense:
gold recall 100.0%). That is a genuine, large, honest win. But **frag_survive_rate
only drops from 100% to 20%, not to 0%** -- and clean_recall (gold present AND no
poison value anywhere in the packed text) only reaches 41%, meaning **59% of the
time, the packed context still contains the true fact *and* at least one surviving
poisoned fragment.** Why: trust-rank only overrides an outcome *once two records
are actually compared* (in the dedup or contradiction phase). A fragment that
never crosses `contradiction_min_sim` against the clean fact (different wording,
lower cosine similarity than a near-duplicate) is simply never challenged, so it
sits in the store as an independent, uncontested, still-retrievable "fact." **Channel
provenance defends the head-to-head contest; it does not sweep up low-trust content
that never gets directly compared to anything.** Say this plainly: a compromised
extractor writing coordinated `llm_inferred` fragments can still get roughly a
fifth of them into a user's context alongside the truth, even under the current
defense -- the true fact always wins the authority question, but the noise is not
purged.

**L3b: the fusion phase has zero channel awareness, confirmed by direct code
inspection, not just by this benchmark.** `memsub.consolidate.FUSION_SYSTEM`'s
prompt (built in `_judge_fusion`) lists each candidate memory's session and
content -- never its channel, never its trust rank, never whether its signature
verified. A judge (real or, here, the deterministic mock standing in for one) has
no signal to distinguish a `user_stated` source from an `llm_inferred` one when
deciding whether to fuse. Measured consequence: even under the current defense,
59% of families still produce a synthesized fusion episode, and of those, 33.9%
carry the poisoned value forward into a brand-new, unsigned, `kind="fusion"` memory
that (per the grandfather rule) grades as unsigned-legacy, rank 1 -- exactly the
rank a verified `llm_inferred` row would carry, and still safely below verified
`user_stated`'s rank 2. **That headline number is the real, previously-undocumented
gap this round exists to surface: channel provenance was never wired into the
fusion phase at all.** The one qualifier, stated as plainly as the gap itself:
**in this harness, that gap never actually promotes poison over a clean fact**
(promotion rate is 0.0% at every budget under the new defense) -- because the
upstream dedup/contradiction phases already guarantee the clean fact survives
100% of the time before fusion ever runs. The fused poison becomes extra noise
competing for budget alongside an always-present truth, not a replacement for it,
in *this* measurement. That is a property of this specific dataset shape (a clean
fact always exists and is always independently defended), not a property of the
fusion phase's own logic -- a scenario where the true fact is itself weaker
(unsigned, or genuinely absent, or itself contested) would not have this
backstop, and this round did not test that scenario. Do not read "promotion rate
0% here" as "fusion is safe against poisoning" -- read it as "fusion is unsafe,
and this particular dataset shape happened to have another defense layer
underneath it that caught the case anyway."

## Updated claim language

Extending docs/poisoning-provenance-2026-07-17.md's own claim-language guidance
rather than replacing it:

> "memsub's channel-provenance defense fully closes the L1 (single-record) gap it
> was built to close, including when the poison is framed as an explicit
> 'correction' through the imported channel (L3a here). Against coordinated
> multi-record poison through the llm_inferred channel (L2), it reliably protects
> the *authoritative* answer (the true fact always wins any direct contest, 0% ->
> 100% gold recall measured here) but does **not** purge merely-coexisting
> low-trust noise that a defender never directly compares to anything -- roughly a
> fifth of coordinated fragments still reach a user's context alongside the truth
> in this measurement. And the consolidation layer's own fusion phase has **no
> channel-trust awareness at all**: it will synthesize a new, unsigned memory from
> whatever sources share an entity, majority-value poison and all, regardless of
> those sources' verified trust rank, because its judge prompt never receives
> channel information in the first place. This round's specific dataset never let
> that fused poison out-rank a clean fact end-to-end, but that is because the
> upstream dedup/contradiction defense already protected the clean fact
> independently -- it is not evidence that fusion itself is safe, and a scenario
> without that independent backstop was not tested."

Do not say: "channel provenance defends against coordinated or fusion-based
attacks." It defends the specific record-vs-record trust contest very well
(L1, L3a, and the "does the true fact ever lose" half of L2); it has a real,
demonstrated, previously-undocumented blind spot in the fusion phase (L3b) and a
real, partial gap against merely-coexisting coordinated noise (L2). Both are
stated here, not glossed over.

## What this round still does not test

- MemPoison's actual L2/L3 test cases (this project has no access to them, same
  limitation stated in every prior round in this file's family).
- MemPoison's own L3 (context-triggered dormant activation) at all -- this
  codebase has no trigger-context mechanism anywhere, and this round's "L3" is an
  explicit, stated substitution (consolidation-targeting), not an attempt at
  MemPoison's actual L3.
- A scenario where the clean fact itself is weaker than "always independently
  defended" -- the one condition under which L3b's fusion-channel-blindness gap
  would likely translate into an actual promoted-poison outcome, per the analysis
  above.
- Any real downstream composition step (an LLM actually reasoning over co-retrieved
  fragments) for L2 -- this harness measures co-retrieval, the precondition
  MemPoison's own L2 text names, not composition itself.

## Sources

- arXiv:2606.12703 (SMSR), arXiv:2607.14651 (MemPoison) -- both already cited in
  docs/poisoning-provenance-2026-07-17.md, tier text quoted from there.
- docs/poisoning-benchmark.md (L1, original round), docs/poisoning-provenance-2026-07-17.md
  (L1, provenance re-run).
- scripts/poisoning_benchmark.py (`poisoned_dataset_coordination`,
  `poisoned_dataset_correction`, `deterministic_judge_multirecord`,
  `run_coordination_cell`, `run_correction_cell`, `main_tiers`).
- memsub/consolidate.py (`FUSION_SYSTEM`, `_judge_fusion`, `_trust_rank`) --
  the fusion-prompt channel-blindness claim above is verified directly against
  this code, not inferred only from the benchmark numbers.
- results/poisoning-tiers-2026-07-19.json (raw per-case rows, all four budgets).

