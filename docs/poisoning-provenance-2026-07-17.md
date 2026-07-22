# Poisoning re-run: HMAC channel provenance vs the content heuristic (2026-07-17)

Companion to docs/poisoning-benchmark.md (2026-07-12/13, the original round) and
docs/improvement-plan-2026-07-17.md Part B items 4 and 5. Every number below comes
from an executed run of `scripts/poisoning_benchmark.py --provenance`; the full
per-case rows are in `results/poisoning-provenance-2026-07-17.json`. Implementation:
`memsub/provenance.py` and the channel-trust ranking in `memsub/consolidate.py`
(`_trust_rank`), tests in `tests/test_provenance.py`.

## Why this round exists

The original round's honest negative result: the observed-beats-inferred rule is a
*content/kind* heuristic (`kind == "inference"` or `provenance["inferred"]`). It fully
defends Attack A (poison marked as a sleep-inference: 0% ASR) and does **nothing**
against Attack B (identical poison, disguised as an ordinary observed fact: 98.7%
ASR, rule ON and rule OFF produce the same number). SMSR (arXiv:2606.12703) proves
this is not a tuning gap, it is a formal ceiling: no provenance-free, content-based
heuristic can be certified secure against an adversary who disguises the payload as
an observation, because the heuristic has no way to check whether the content's
self-description is true.

The fix in this round does not try to out-guess disguised content. It stops asking
"what does this memory's content/kind claim about itself" and asks instead "which
code path actually wrote this," recorded cryptographically at write time
(`memsub/provenance.py`): every episode is tagged with a channel (`user_stated`,
`llm_inferred`, or `imported`) and an HMAC-SHA256 signature over
`(content, channel, timestamp, namespace)`, keyed by a local secret
(`~/.memsub/provenance.key`, 0600, generated on first use). Consolidation verifies
the signature and ranks trust by the **verified channel**, never by content or kind.
A row whose signature does not check out is quarantined: excluded from retrieval and
from winning any supersede decision, flagged in the audit trail (a `quarantined`
event), never deleted.

## Threat model: what an attacker can and cannot forge

This is the part that actually matters, stated plainly rather than implied by a
results table:

- **Cannot forge: the `user_stated` channel itself.** The channel tag is assigned by
  the specific code path that produced the episode (memsub/cli.py's transcript
  extraction, memsub/wiki.py's page ingest, memsub/vault.py's human correction and
  note promotion), not read off the episode's content or `kind` field. An attacker
  who only controls what text ends up inside a memory's content cannot make that
  text cause `channel="user_stated"` to be written; the field is set before the
  content is even considered, by which write-path function is calling
  `provenance.tag_episode`. This is exactly what defeats Attack B's whole trick
  (dressing the content as `kind="fact"`, no `inferred` flag): the channel assigned
  to a memory has nothing to do with what its content looks like.
- **Cannot forge: a valid signature without the key.** `~/.memsub/provenance.key`
  never leaves the machine and is 0600. Without it, HMAC-SHA256 forgery is
  computationally infeasible. Tested directly: `tests/test_provenance.py`'s
  `test_tampered_content_is_quarantined` and `test_tampered_channel_field_is_quarantined`.
- **Can still reach: the `llm_inferred` or `imported` channel, if extraction or an
  import path is compromised.** This is the honest limit of this defense, stated as
  plainly as this project states every other one: **a compromised extractor can
  still poison memsub through the `llm_inferred` channel** (memsub/sleep.py's
  inference pass, or any future extraction path that legitimately writes that
  channel), and **a compromised or untrusted import path can still poison memsub
  through the `imported` channel** (memsub/sdk.py's `MemoryClient.add`, the
  `add_memory` MCP tool default). What channel signing buys is not "no channel can
  ever carry poison," it is "a poison entering through `llm_inferred` or `imported`
  can never impersonate `user_stated`, so it can never outrank a genuine user
  statement, no matter how it disguises its content." This is a real trust-ordering
  win, not immunity: memsub.consolidate's default trust ranking has
  `user_stated (2) > llm_inferred = imported (1)`, so poison through either
  lower channel loses to any conflicting genuine `user_stated` fact, but two
  `llm_inferred`/`imported` writes still settle by recency between themselves, same
  as any other tie.
- **Cannot defend: poison injected through a genuinely `user_stated` turn.** No
  memory system can; a human actually typing the malicious content in a real turn is
  a social-engineering problem, not a provenance problem. Stated in
  docs/improvement-visuals-2026-07-17.md section 4 before this round ran, repeated
  here because it is still true.
- **Migration / grandfather rule:** episodes written before this module existed
  carry no `channel`/`channel_sig` at all. They are graded `unsigned-legacy`: still
  retrievable (nothing was tampered, the field simply predates this scheme), and
  they keep their old relative ordering via the pre-existing `_is_inferred`
  heuristic as a fallback -- but an unsigned-legacy row can **never** outrank a
  signed `user_stated` row, regardless of recency or its legacy kind. Verified by
  `tests/test_provenance.py::test_unsigned_legacy_cannot_outrank_signed_user_stated_even_if_newer`.
- **Not exercised by this benchmark:** the quarantine path itself (a *tampered*
  signature). Neither Attack A nor Attack B in this harness tampers a valid
  signature; both attacks write a validly-signed low-trust-channel episode. The
  quarantine mechanism (signature verification failure) is covered by
  `tests/test_provenance.py`, not by this benchmark's ASR numbers -- a real
  distinction: this round measures the channel-trust-ordering defense, not the
  tamper-detection defense, and the two are separate code paths that happen to
  share one verification function.

## MemPoison L1/L2/L3 mapping

MemPoison (arXiv:2607.14651, "MemPoison: Uncovering Persistent Memory Threats and
Structural Blind Spots in LLM Agents") defines three difficulty tiers, quoted
directly from the paper (fetched 2026-07-17, arxiv.org/html/2607.14651v1):

- **L1, single-record corruption:** "the adversary injects a single explicitly
  harmful memory record, where malicious intent is detectable at write time."
- **L2, compositional corruption:** "the adversary partitions the malicious payload
  across multiple individually plausible memory records injected over time. The
  corruption manifests only when these fragments are co-retrieved and composed by
  the agent during downstream processing."
- **L3, context-triggered dormant corruption:** "the adversary plants memory entries
  that are inert under normal conditions but activate malicious behavior only when
  exposed to specific, natural trigger contexts."

MemPoison also defines three separate *injection channels* (`user_input`,
`tool_return`, `cross_agent`) and three memory substrates (`flat_chunk`,
`fact_store`, `hierarchical_notes`). Flag this explicitly so it is never conflated
with memsub's *write channel* concept (`user_stated`/`llm_inferred`/`imported`):
same word, two unrelated axes from two different papers.

**Mapping memsub's two attacks onto MemPoison's tiers (self-defined by this
project, per the task's fallback instruction -- not verified against MemPoison's
own released test-case labels, which this project has no access to; the
mapping below is inferred from the tier text quoted above):**

| memsub attack | MemPoison tier | Why |
|---|---|---|
| A: inferred-marked | **L1** | One adversarial record per fact family, no fragmentation across records, no dormant/trigger mechanic. Matches L1's record-cardinality criterion directly. |
| B: disguised-as-observed | **L1**, with a caveat | Also exactly one record per fact family (not L2, no fragmentation; not L3, no dormant trigger) -- structurally L1. The caveat: MemPoison's own L1 text bundles in "malicious intent is detectable at write time" as part of what makes L1 the *easy* tier. Attack B is specifically built to defeat write-time content detectability while remaining a single record, so it is structurally L1 by cardinality but adversarially harder than MemPoison's L1 description implies for a content-only defense. |

**What this round does not test, stated plainly:** L2 (compositional, multi-record)
and L3 (context-triggered dormant) are not exercised at all. memsub's harness has no
mechanism for a payload split across records that only becomes harmful once
co-retrieved, and no mechanism for a dormant trigger. This is the same category of
honest gap the original poisoning-benchmark.md documents for MPBench's six attack
classes (one of six covered there); here it is one of three MemPoison tiers covered,
and only the easiest one.

## Results: old defense vs new defense

Holdout seeds 6-10 (docs/benchmark.md protocol), n=150 queries per cell per budget,
150 adversarial episodes per attack (one per fact family), budgets [100, 200, 400,
800] -- identical protocol to the original round, only the defense changed. Full
rows (including per-budget breakdown) in
`results/poisoning-provenance-2026-07-17.json`; all four budgets produce identical
percentages per cell (as in the original round), so one representative budget (400)
is shown here for readability.

| attack | defense | system | clean recall | ASR | RSR | tier |
|---|---|---|---|---|---|---|
| A: inferred-marked | (undefended) | naive-topk | 0.0% | 100.0% | 100.0% | L1 |
| A: inferred-marked | content heuristic (old) | policy+cons | 98.0% | 0.0% | n/a (0 ASR successes) | L1 |
| A: inferred-marked | **channel provenance (new)** | policy+cons | **98.0%** | **0.0%** | n/a (0 ASR successes) | L1 |
| B: disguised-as-observed | (undefended) | naive-topk | 0.0% | 100.0% | 100.0% | L1 (caveat above) |
| B: disguised-as-observed | content heuristic (old) | policy+cons | **0.0%** | **98.7%** | 100.0% | L1 (caveat above) |
| B: disguised-as-observed | **channel provenance (new)** | policy+cons | **98.0%** | **0.0%** | n/a (0 ASR successes) | L1 (caveat above) |

**The one number this whole round exists to change:** Attack B's clean recall goes
from 0.0% to 98.0%, and its ASR from 98.7% to 0.0%, under the new defense --
identical to Attack A's numbers, because the new defense does not distinguish the
two attacks at all. That is the point: it was never able to see the content-level
difference (kind="inference" vs kind="fact") that the old rule leaned on, and it
does not need to, because the channel that actually mattered (`llm_inferred` /
`imported`, both below `user_stated`) was bound at write time regardless of what
either attack's content claimed to be.

Attack A's numbers are unchanged (0.0% ASR, 98.0% clean recall, matching the
original round's rule-ON row and docs/benchmark.md's 98.0% regression figure
exactly) -- the new defense does not regress the case the old rule already handled.

## Reading the numbers honestly

- This is a **within-project measurement**, not a MemPoison benchmark score.
  MemPoison's dataset and harness are not available to this project (same situation
  the original round documented for MPBench); the tier labels above are MemPoison's
  own published tier *definitions* applied to memsub's own synthetic dataset, not a
  run against MemPoison's actual 1,227 hand-validated test cases.
- Both attacks tested here are L1 by the mapping above. **Nothing in this round
  says anything about L2 (compositional) or L3 (dormant-trigger) attacks against
  this defense.** Channel-trust ranking defends against a single low-trust-channel
  record losing a supersede/dedup contest against a higher-trust one; it has no
  mechanism at all for detecting that several individually-plausible `llm_inferred`
  records compose into something harmful only when co-retrieved (L2), or for
  detecting a dormant record that only activates on a trigger context (L3). Those
  remain fully open, unmeasured, and likely require different defenses entirely
  (structural: MemPoison's own finding is that `fact_store`-style decomposition,
  which is architecturally close to what memsub already does, is "comparatively
  more robust" against these tiers than `flat_chunk`, but that is MemPoison's
  finding about MemPoison's own dataset, not a memsub measurement).
- The quarantine path (tamper detection, as opposed to channel-trust ranking) is
  unit-tested (`tests/test_provenance.py`) but not exercised by this benchmark's ASR
  numbers -- see the threat model section above.
- **Claim-language guidance, consistent with docs/poisoning-benchmark.md's existing
  guidance:** do not say "solved memory poisoning" or "channel provenance defeats
  disguised-as-observed attacks in general." The precise, defensible claim this data
  supports:

  **"memsub's channel-provenance defense closes the specific gap the original round
  measured: on the same L1-tier synthetic dataset, disguising a poisoned memory as
  an observed fact no longer helps an attacker who can only reach the llm_inferred
  or imported write channel (ASR 98.7% -> 0.0%, clean recall 0.0% -> 98.0%), because
  trust is now decided by a cryptographically verified write channel instead of by
  what the memory's content claims about itself. This defense does not cover L2
  (compositional) or L3 (dormant-trigger) attacks, has not been measured against
  them, and does not defend content that genuinely entered through a real user
  turn, which is a social-engineering problem no memory system can solve."**

## Sources

- arXiv:2606.12703 (SMSR, the formal blind-spot result this whole plan item answers)
- arXiv:2607.14651 (MemPoison, tier definitions quoted above; fetched 2026-07-17,
  arxiv.org/html/2607.14651v1)
- docs/poisoning-benchmark.md (the original round this one re-runs against)
- docs/improvement-plan-2026-07-17.md Part B items 4-5; docs/improvement-visuals-2026-07-17.md section 4
- memsub/provenance.py, memsub/consolidate.py (`_trust_rank`), tests/test_provenance.py

