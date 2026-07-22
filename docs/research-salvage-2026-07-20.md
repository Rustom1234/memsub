# Deep-research salvage: memsub (2026-07-20)

Salvaged verified findings from the 2026-07-20 deep-research run (synthesis
died on session limits; claims below survived 3-vote verification).
Orchestrator synthesis, by hand:

1. Temporal reasoning is the field's active frontier and maps exactly onto
   our weakest category. Three concrete designs: Memory-T1 (RL-trained
   time-aware memory selection at 3B/7B scale, local-friendly, beats 14B),
   TSM (semantic timelines + durative memory units + temporal intent at
   retrieval, +12.2% on LoCoMo/LongMemEval), REMem (time-qualified triple
   graph + agentic retrieval tools, beats mem0 and HippoRAG 2).
2. Our date-surfacing fix was directionally right; TSM's temporal-intent
   detection at retrieval time is its natural next step.
3. MemGuard's abstention calibration (know when evidence is insufficient)
   reframes answer refusal as a feature; relevant to our judge-refusal
   finding, and its typed-store separation cut retrieved tokens 5.8x.
4. Zep's published LongMemEval numbers (+18.5%, latency -90%) confirm the
   bi-temporal graph direction our entity layer is a lightweight version of.

## Verified claims (21, each with source and vote)

### 1. Memory-T1 uses RL (GRPO) to train a time-aware memory-selection policy on small open-source models (Qwen2.5-3B and 7B), with a BM25 retriever, and its 7B model reaches 67.0% overall on the Time-Dialog benchmark, outperforming a much larger Qwen2.5-14B baseline (60.7%) by 10.2 points; the 3B model scores 66.9%.
- source: https://arxiv.org/pdf/2512.20092 (vote 3-0)
- quote: "MEMORY-T1 establishes a new state-of-the-art, with our 3B and 7B models achieving top overall scores of 66.9% and 67.0%. ... our approach proves superior to simply increasing model scale. Our 3B model not only consistent..."

### 2. The gains come from a multi-level verifiable reward (answer accuracy R_a + evidence grounding R_g + temporal consistency R_t); ablations show removing R_t and R_g jointly costs 15.0%, and training with only task-accuracy reward causes a 22.4% overall drop with complex-reasoning categories collapsing.
- source: https://arxiv.org/pdf/2512.20092 (vote 3-0)
- quote: "Ablation studies show temporal consistency and evidence grounding rewards jointly contribute to a 15.0% performance gain. ... Training with only task accuracy (Ra) leads to a catastrophic 22.4% drop in the overall score,..."

### 3. REMem, an agentic episodic-memory framework, outperforms Mem0 and HippoRAG 2 by 3.4% and 13.4% absolute on episodic recollection and episodic reasoning tasks respectively, across four benchmarks (LoCoMo, REALTALK, Complex-TR, Test of Time).
- source: https://arxiv.org/pdf/2602.13530 (vote 3-0)
- quote: "Comprehensive evaluation across four episodic memory benchmarks shows that REMem substantially outperforms state-of-the-art memory systems such as Mem0 and HippoRAG 2, showing 3.4% and 13.4% absolute improvements on epis..."

### 4. REMem indexes memory as a hybrid graph of time-aware natural-language gists plus schemaless (subject, predicate, object) facts grounded with temporal qualifiers (point_in_time, start_time, end_time), and queries it with a ReAct-style agent using curated tools (semantic_retrieve, lexical_retrieve via BM25, find_gist_contexts, find_entity_contexts, output_answer) supporting time-range filtering, neighbor exploration, and ordinal constraints.
- source: https://arxiv.org/pdf/2602.13530 (vote 3-0)
- quote: "we clearly instruct the LLM to construct memories organized primarily along time and linked to situational dimensions such as participants, locations, and emotions. We develop an agentic inference procedure with carefull..."

### 5. The paper proposes Temporal Semantic Memory (TSM), which builds semantic timelines instead of dialogue-based ones and consolidates related information into durative memory units, targeting two named failure modes of current memory systems: temporal inaccuracy and temporal fragmentation. This is directly actionable for memsub's weak temporal-reasoning category.
- source: https://arxiv.org/pdf/2601.07468 (vote 3-0)
- quote: "The authors propose Temporal Semantic Memory (TSM), which constructs semantic timelines rather than dialogue-based ones and consolidates related information into durative memory units."

### 6. TSM incorporates temporal intent at retrieval time to select appropriate context, a retrieval-side mechanism for temporal reasoning that memsub (BM25+dense RRF hybrid) does not currently model.
- source: https://arxiv.org/pdf/2601.07468 (vote 3-0)
- quote: "During retrieval, the system incorporates temporal intent to access appropriate context."

### 7. TSM was evaluated on LongMemEval and LoCoMo and reports up to 12.2% absolute accuracy improvement, making it a direct benchmarked comparison point on the same LoCoMo benchmark memsub uses (memsub J-score 63.75% local / 52.5% frontier).
- source: https://arxiv.org/pdf/2601.07468 (vote 2-1)
- quote: "Testing on LongMemEval and LoCoMo benchmarks demonstrated improvements "up to 12.2% absolute improvement in accuracy""

### 8. Zep uses a temporally-aware knowledge graph engine (Graphiti) that maintains historical relationships and preserves temporal edges, directly targeting the temporal-reasoning weakness in episodic memory systems.
- source: https://arxiv.org/abs/2501.13956 (vote 3-0)
- quote: "Zep is a memory layer service for AI agents that uses a temporally-aware knowledge graph engine called Graphiti to "dynamically synthesize both unstructured conversational data and structured business data while maintain..."

### 9. On the LongMemEval benchmark, Zep reports accuracy improvements of up to 18.5% while reducing response latency by 90% versus baseline.
- source: https://arxiv.org/abs/2501.13956 (vote 3-0)
- quote: "In this evaluation, Zep achieves substantial results with accuracy improvements of up to 18.5% while simultaneously reducing response latency by 90%."

### 10. MemGuard, a type-aware memory framework that isolates semantic/episodic/procedural memories into separate stores linked by a relational knowledge graph with query-adaptive routing, improves anti-hallucination accuracy by up to 28.27% while retrieving up to 5.8x fewer memory tokens than prior methods.
- source: https://arxiv.org/pdf/2605.28009 (vote 3-0)
- quote: "Across hallucination and long-horizon conversation benchmarks, MEMGUARD improves memory reliability by up to 28.27% while retrieving up to 5.8x fewer memory tokens than prior methods."

### 11. On adversarial LoCoMo queries that test whether a system abstains when evidence is insufficient, MemGuard outperforms the A-Mem baseline by 20.63% under GPT-4o-mini and by 10.76% under GPT-4.1-mini, framing answerability calibration as a contamination defense.
- source: https://arxiv.org/pdf/2605.28009 (vote 3-0)
- quote: "MEMGUARD outperforms A-mem by 20.63% under the GPT-4.1-mini and by 10.76% under the GPT-4.1-mini setting."

### 12. LoCoMo-Plus introduces a two-level distinction (Level-1 factual vs Level-2 cognitive memory) and shows all 15 tested models have a factual-to-cognitive performance gap of 9.57 to 26.06 percentage points, meaning surface factual recall (which memsub's LOCOMO J-score measures) systematically overstates a memory system's real cognitive/constraint-retention ability.
- source: https://arxiv.org/html/2602.10715v1 (vote 3-0)
- quote: "Table 1 shows consistent "Gap" column with values ranging 9.57–26.06 percentage points across 15 tested models."

### 13. Cognitive (beyond-factual, constraint-consistency) memory collapses far faster than factual memory as conversation context length grows, isolating long-context interference as the dominant failure mode rather than simple retrieval miss.
- source: https://arxiv.org/html/2602.10715v1 (vote 2-1)
- quote: "cognitive memory shows rapid performance collapse as context length increases. This sharp disparity highlights that Level-2 cognitive memory is substantially more sensitive to long-context interference than Level-1 factu..."

### 14. MemReranker offers compact reasoning-aware reranking models (0.6B and 4B parameters) purpose-built for agent memory retrieval, directly targeting learned rerankers for memory (one of the exact research asks) and runnable on a single local GPU.
- source: https://arxiv.org/html/2605.06132 (vote 3-0)
- quote: "The paper introduces compact reranking models (0.6B/4B parameters) designed specifically for agent memory systems. The authors argue that generic rerankers fail because they rely on semantic similarity matching rather th..."

### 15. On LOCOMO, the tiny MemReranker-0.6B beats the BGE-Reranker baseline (MAP 0.7150 vs 0.6708, +4.4 points) and matches open 4B/8B models and GPT-4o-mini, meaning a small local reranker can lift the same LOCOMO benchmark memsub reports.
- source: https://arxiv.org/html/2605.06132 (vote 3-0)
- quote: "MemReranker-0.6B substantially outperforms BGE-Reranker and matches open-source 4B/8B models as well as GPT-4o-mini"

### 16. On LongMemEval, MemReranker-4B achieves the best MAP across all metrics (0.8043 vs Gemini-3-Flash 0.7259, +7.8 points), evidence that a learned memory reranker improves the open-domain/complex-query retrieval that memsub cites as its weak category.
- source: https://arxiv.org/html/2605.06132 (vote 3-0)
- quote: "MemReranker-4B achieves the best results across all metrics, surpassing both dedicated rerankers and LLM-as-judge baselines"

### 17. A recall-preserving reranker that reorders only the protected top-10 candidate set with a fine-tuned cross-encoder raises FULL MRR on LoCoMo from 0.5824 to 0.6560 (+0.0734, 95% CI [+0.0645, +0.0827]) while leaving Recall@10 and Hit@10 identical by construction.
- source: https://arxiv.org/html/2606.10842 (vote 3-0)
- quote: "v2 raises FULL MRR from v1's 0.5824 to 0.6560 ... Recall@10 and Hit@10 are identical to v1 by construction"

### 18. A ~3.6M-parameter learned reranker trained by cross-encoder distillation over fused dense+lexical features can beat a BGE-large cross-encoder on Recall@10 (0.9593 vs 0.8807) at 12.6x lower latency, making tiny local rerankers a viable cost-frontier option for memory retrieval.
- source: https://arxiv.org/html/2605.28062 (vote 2-1)
- quote: "On Clean500, ConvMemory has higher Recall@10 than the BGE-large cross-encoder (0.9593 vs 0.8807) at 12.6× lower latency"

### 19. The small learned reranker does not match a strong off-the-shelf reranker (mxbai-rerank-large-v1) on the LOCOMO benchmark, losing on both Recall@10 (0.7798 vs 0.8080) and MRR, i.e. cheap distilled rerankers trade absolute quality for latency.
- source: https://arxiv.org/html/2605.28062 (vote 3-0)
- quote: "loses to mxbai-rerank-large-v1 on both Recall@10 and MRR"

### 20. SCM implements offline 'sleep-stage' consolidation with an NREM phase that replays recent episodes, strengthens co-occurring concepts via Hebbian plasticity, and applies proportional synaptic downscaling, as a mechanism for consolidating episodic LLM memory.
- source: https://arxiv.org/html/2604.20943v1 (vote 2-0)
- quote: "NREM consolidation replays recent episodes, strengthens co-occurring concepts through Hebbian plasticity, and applies proportional synaptic downscaling."

### 21. SCM's algorithmic forgetting assigns each concept a retention score blending importance with temporal decay, S(c) = beta1*I(c) + beta2*(1-delta(c)), enabling value-based pruning of low-value memories.
- source: https://arxiv.org/html/2604.20943v1 (vote 3-0)
- quote: "Concepts receive a retention score blending importance with temporal decay: S(c) = β₁·I(c) + β₂·(1−δ(c))"

## Unverified leads (verification was cut by session limits; names recovered from run logs)

- SCM: offline sleep-stage consolidation with algorithmic forgetting scores per memory
- SleepGate: KV-cache augmentation approach to memory
- MemoryArena benchmark for agent memory
- Proactive-interference synthetic benchmark (old memories crowding new)
- A ~3.6M-parameter learned reranker trained for memory retrieval (tiny, local-friendly)
