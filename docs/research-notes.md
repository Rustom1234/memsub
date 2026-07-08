# Research notes: improving long-conversation memory recall

Written 2026-07-08 for the LOCOMO evidence-recall push. Sources were surveyed
via web research (citations inline); every claim is labeled peer-reviewed,
preprint, or vendor/marketing. Our own failure data comes from
`docs/error-analysis-locomo.md` (dev split only).

## Our failure modes (measured, dev split, budget 1000)

- 220 of 223 evidence-all failures are retrieval ranking misses (`ranked_low`):
  the evidence turn exists in the store but ranks median 174 (p75 300) when
  the budget packs roughly the top 50 turns.
- Budget cuts (2) and ingestion gaps (1) are negligible. Oracle bound with
  perfect ranking is 99.7%: retrieval quality is the entire game at this
  budget.
- Per category fail rate: multi-hop 94.6%, open-domain 94.7%, single-hop
  51.5%, temporal 35.6%.
- Mean cosine(query, evidence) is 0.18-0.21 on failures vs 0.32-0.39 on
  successes: the hashed bag-of-features embedder has weak semantic signal;
  failures are cases with little lexical overlap between question wording and
  evidence wording, or where only one hop of a multi-hop question matches.

Implication: techniques must attack ranking (better matching signal, query
decomposition, complementary retrievers), not budget packing or storage.

(Technique survey follows.)
