# memsub improvement plan v2 (2026-07-18 evening)

Goal, in Rustom's words: beat the current top LOCOMO scores. Current standing
(docs/locomo-frontier-v2-2026-07-18.md): memsub+Sonnet 52.5% overall, gap to
mem0 reproduced tier -9.2pp, to Zep revised -22.6pp. Single-hop 85% already
beats mem0. Gaps: open-domain -22.9, temporal -25.5 (-10.5 with dates),
multi-hop -6.2.

## Wave 1 (mechanical, high-confidence)

1. Combined v3 run: best-effort prompt + dated context together, full n=80,
   graph_decompose condition. The two fixes have never run combined.
2. Context-budget ablation: our packed-context budget (1000 tokens) is
   self-imposed austerity; competitors pack far more. Run graph_decompose
   with Sonnet at budgets 1000 vs 2000 vs 4000 (same slice, same judge).
   If budget moves the number materially, adopt the best and note the cost.
3. Productize date surfacing: the session-date prefix goes from eval-path
   hack to an opt-in retrieval formatter flag with tests, since it is now
   measured (+15pp temporal).

## Wave 2 (diagnosis-driven)

4. Open-domain autopsy: read all 20 open-domain questions and their v2
   transcripts; classify failures (retrieval miss vs context-present-but-
   unused vs judge artifact). Fix what the autopsy names, re-run category.
5. Multi-hop push: 2-hop graph flag + decomposition combined (never run);
   if flat, one bounded new idea informed by the autopsy, not speculation.
6. Final: full n=80 run of the best config, updated comparison table and
   claim language in docs, README sync.

Success bar for the day: beat 61.7% (mem0 reproduced) on the n=80 slice with
every deviation documented. Stretch: dent the gap to 75.14 (Zep revised).
All runs stream per-question JSONL, all local commits, no pushes.

