# LOCOMO Wave 2 item 4: open-domain autopsy

Executes item 4 of `docs/improvement-plan-2026-07-18.md`, ordered directly by
Rustom: "make a plan to improve each now, and then actually execute the full
plan" / "i need to beat current top locomo score also." Reads all open-domain
(category 3) failures from the v3/b2000 run (best-effort prompt + dates,
budget 2000, `docs/locomo-v3-2026-07-18.md`, 55.0% open-domain accuracy,
-17.9pp vs mem0's reproduced 72.9%) and classifies each.

## Data correction before starting

The task briefing said "11/20 wrong" for open-domain. A direct read of
`results/locomo-jscore-v3-2026-07-18-b2000.jsonl` shows 20 open-domain
records, 0 duplicates, **11 correct / 9 wrong** (55.0%, matches the
already-published summary). This autopsy covers the actual 9 wrong records,
not 11 -- see `WORKLOG.md` 2026-07-19 for the arithmetic check.

## Method

The v3/b2000 run does not persist packed context (only question/gold/answer/
verdict), so `scripts/locomo_open_domain_autopsy_2026-07-19.py` reruns
retrieval only (no answer/judge calls) at the same condition/budget/dates
config to reconstruct it. This is read-only against the same holdout slice
and sample. **Caveat**: the decomposition sub-query LLM call
(`OllamaBackend`, no fixed temperature) is not deterministic, so the
reconstructed context is an approximation of the original run's, not a
byte-identical replay -- confirmed materially different for one question
(Jolene's age, case 1 below), where the original model's answer cited facts
absent from the reconstruction. Classification below cross-checks the
reconstructed context against the LOCOMO dataset's own `evidence` dialogue
IDs (`data/locomo10.json`, `qa[].evidence`) for each question, which is
ground truth for "does this fact exist in the source at all" independent of
the reconstruction's stochasticity.

## Classification table (9 actual wrong open-domain records)

| # | conversation | question | gold | model answer | evidence in reconstructed context? | class |
|---|---|---|---|---|---|---|
| 1 | conv-43 | Which popular music composer's tunes does Tim enjoy playing on the piano? | John Williams | "a movie theme song" (no composer named) | D8:14 yes / **D8:16 no** (the turn naming "Harry Potter and the Philosopher's Stone" as the movie) | **(a) retrieval miss** |
| 2 | conv-48 | How old is Jolene? | likely no more than 30, since she's in school | mid-to-late 20s, but invents "mother passed away" (not supported -- the source turn only says the pendant is a gift from her living mother) | 8 of 10 evidence turns absent (the repeated "engineering student" / "studying" / "finals" turns establishing she's still in school never made it into context) | **(a) retrieval miss**, compounded by an unsupported invented detail |
| 3 | conv-44 | Which US state do Audrey and Andrew potentially live in? | Minnesota | "northeastern or central US," no state named | Single evidence turn (D11:9) is a **photo of a trail map** (`blip_caption: "a photo of a map of a park with a lot of trees"`); the state name is legible only in the image pixels, never in any text turn anywhere in the full conversation (checked directly) | **(d) out of memory scope** -- text-only ingestion structurally cannot see this fact; not a retrieval-tuning problem |
| 4 | conv-47 | Are John and James fans of the same football team? | No -- James: Liverpool, John: Manchester City | "Yes, both ... Manchester City fans" | **Both** evidence turns present in context verbatim ("no club better than Liverpool" / "As a Manchester City fan...") | **(b) present-but-unused** -- both facts were retrieved, the model still merged them into one team |
| 5 | conv-47 | Was James feeling lonely before meeting Samantha? | Most likely yes (only pets/games gave him joy) | "no ... life as generally content" | D9:16 ("My pets, computer games, travel and pizza are all that bring me happiness in life") absent from context | **(a) retrieval miss** |
| 6 | conv-43 | What would be a good hobby related to his travel dreams for Tim to pick up? | Writing a travel blog | "learning a musical instrument" | 3 of 4 evidence turns absent, specifically the two establishing Tim writes (fantasy-novel articles for an online mag, mid-novel) | **(a) retrieval miss** -- without knowing Tim writes, "travel blog" is unreachable |
| 7 | conv-43 | What is a Star Wars book that Tim might enjoy? | Star Wars: Jedi Apprentice (by Blundell/Farland) | "Harry Potter" (his stated favorite *book*, not a Star Wars book) | **Both** evidence turns present ("Harry Potter is my favorite book" / "Definitely Star Wars! It's my favorite [movie]") | **(b) present-but-unused** (model answered with the already-known favorite book instead of noticing "Star Wars" was the actual topic), compounded by **(d)** -- no specific Star Wars book title exists anywhere in context, so even a model that used the Star Wars clue correctly would be guessing one specific real title among dozens via world knowledge alone |
| 8 | conv-49 | Which US state was Sam travelling in during October 2023? | California | Nevada (Lake Tahoe) | Evidence present ("We're traveling through Lake Tahoe!") | **(c) judge/gold artifact** -- Lake Tahoe genuinely straddles the CA/NV border; no other turn in the conversation disambiguates which side. "Nevada" is not an indefensible reading of the same evidence the gold answer uses. |
| 9 | conv-44 | What is an indoor activity that Andrew would enjoy doing while make his dog happy? | Cook dog treats | "indoor dog daycare with a big play space" | Both evidence turns absent, specifically the one establishing Andrew has gotten into cooking/trying new recipes | **(a) retrieval miss** |

## Counts

| class | count | questions |
|---|---|---|
| (a) retrieval miss | **5** | 1, 2, 5, 6, 9 |
| (b) present-but-unused | 2 (1 compounded with d) | 4, 7 |
| (c) judge/gold artifact | 1 | 8 |
| (d) out of memory scope | 1 (+1 compounded) | 3 (+7) |

**(a) retrieval miss dominates (5 of 9, 56%).** Per the task's own decision
rule ("if (a) dominates, the fix is retrieval"), the fix applied is
retrieval, not prompt phrasing or judge-prompt changes.

A structural pattern inside the (a) cases: in 4 of 5, the missing fact sits
in the **same session**, often the same few-turns radius, as a fact that
*was* retrieved (case 1: D8:14 retrieved, D8:16 two turns later missed;
case 6: 3 of 4 evidence turns from the same "Tim writes" thread missed
while an unrelated travel turn from later in the conversation was kept).
This looks like a ranking/recall problem inside the fused order, not a
budget problem (budget 2000 already packs ~9,000-9,500 characters per
question, comfortably inside the 2000-token cap) -- more of the *right*
episodes need to survive into the top of the RRF fusion before the budget
cutoff, not more room once they're there.

## Fix applied

Given (a) dominates and the task's own candidate list is (raise open-domain
k / add the graph 2-hop flag / increase decomposition depth), the bounded
experiment run first is the **graph 2-hop flag** (`hops=2` on
`rank_decomposed_graph`, `memsub/hybrid.py:257,281` -- the flag already
existed, unused by any eval until now). Rationale: 2-hop graph co-mention
is the mechanism most likely to surface a same-session, entity-linked
follow-up turn (like D8:16 following D8:14, or the second Jolene "studying"
turn following the first) that plain BM25/dense ranking placed just below
the fused cutoff, without changing the decomposition or the budget (both
already tuned in Wave 1).

`--condition graph_decompose --prompt best_effort --with-dates --budget
2000 --category 3 --hops 2`, n=20 (open-domain only), streamed to
`results/locomo-jscore-v4-opendomain-hops2-2026-07-19.jsonl`.

**Result: 50.0% (10/20), -5.0pp vs the 55.0% baseline.** 2-hop graph
traversal did not help and made things measurably worse -- most likely
because the extra graph-linked candidates it pulls in for entity-dense
open-domain questions (many named speakers) compete with, rather than
surface, the same-session follow-up turns the autopsy actually needed;
RRF-fusing a noisier third ranking pushed some previously-correct answers'
supporting turns below the budget cutoff.

### Iteration 2: decomposition depth (max_subqueries 2 -> 3)

Per the task's 2-iteration cap on item 4, one more bounded try: revert
`hops` to 1 (2-hop already ruled out above) and instead widen the
query-time decomposition from 2 to 3 sub-queries, on the theory that a
third sub-query might phrase the "who/what wrote X" or "is Tim a writer"
angle differently enough to surface the missed same-session turns.

`--condition graph_decompose --prompt best_effort --with-dates --budget
2000 --category 3 --hops 1 --subqueries 3`, n=20, streamed to
`results/locomo-jscore-v4-opendomain-sq3-2026-07-19.jsonl`.

**Result: 45.0% (9/20), -10.0pp vs the 55.0% baseline -- worse than both
the baseline and the hops=2 attempt.** Stopping here per the 2-iteration
cap; not chasing a third variant.

## Verdict

Neither bounded retrieval fix (2-hop graph traversal, deeper decomposition)
improved open-domain; both measurably hurt it. **The standing v3/b2000
config (hops=1, max_subqueries=2, budget 2000, 55.0% open-domain) stands
as the best config found** and is what item 6's final run uses. The
autopsy's own root-cause finding (same-session follow-up turns losing out
in the RRF-fused ranking) is real and reproducible in the case-by-case
table above, but the two retrieval knobs this repo currently exposes
(hop count, decomposition depth) are not the right lever to fix it --
pulling in more/noisier candidate rankings pushes other useful turns out
rather than promoting the missed ones. A future fix would need to target
ranking recall directly (e.g. same-session proximity boosting, which does
not exist in `memsub/hybrid.py` today) rather than adding more parallel
rankings to fuse -- out of scope for this wave's two-iteration cap, noted
here as the honest next step rather than attempted speculatively.
