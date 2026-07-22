---
type: "topic"
namespace: "topics"
slug: "inference-stack-and-tooling"
member_count: 23
member_hash: "8feaf34d728253e2"
created: "2026-07-21T16:57:34"
updated: "2026-07-21T16:57:34"
---

<!-- memsub:distill:begin -->

# Coldstart's Inference Infrastructure Choice

Arjun faced a decision between self-hosting a large language model and using a hosted API for Coldstart's inference. He eventually switched to the Claude API after evaluating performance and cost, while maintaining some self-hosted work for learning purposes.

## Key facts

- Arjun weighed the pros and cons of self-hosting open-weight models on a rented GPU server versus using hosted frontier APIs before launching Coldstart. [[arjun-weighed-the-pros-and-cons-of-self-hosting-op--8d01c9ba]]
- Arjun found hosted API pricing tiers and rate limits confusing for a one-person product that was still pre-revenue. [[arjun-read-through-hosted-api-pricing-tiers-and-ra--45dc2eb9]]
- Arjun chose to run Coldstart's inference on a self-hosted Qwen3 8B model, served by vLLM on a rented RTX 4090 server, motivated by the zero per-token cost, full data control, and his local-first instincts. [[arjun-chose-to-run-coldstart-s-inference-on-a-self--fd2ddec5]]
- Arjun finished the vLLM quickstart, got Qwen3 8B serving on the RTX 4090, and began sketching Coldstart's draft-generation prompt chain in Python. [[arjun-finished-the-vllm-quickstart-got-qwen3-8b-se--8cc6b045]]
- Arjun followed a vLLM structured-output tutorial to constrain Coldstart's reply classification to strict JSON labels: interested, objection, unsubscribe, and out-of-office. [[arjun-followed-a-vllm-structured-output-tutorial-t--80ff3fda]]
- Arjun hit a wall with GPU memory fragmentation on the RTX 4090 under concurrent Coldstart requests and spent a weekend in the vLLM Discord getting help. [[arjun-hit-a-wall-with-gpu-memory-fragmentation-on--be96321b]]
- Arjun interviewed Marcus Webb, a freelance email-deliverability engineer, about taking on Coldstart's domain warmup, DNS, and bounce-handling work. [[arjun-interviewed-marcus-webb-a-freelance-email-de--77d13247]]
- Arjun hired Marcus Webb for ten hours a week of contract work to help build Coldstart's deliverability infrastructure. [[arjun-hired-marcus-webb-for-ten-hours-a-week-of-co--7a4da6b2]]
- Marcus Webb shipped the first version of Coldstart's sending infrastructure, including domain warmup schedules and bounce tracking. [[marcus-webb-shipped-the-first-version-of-coldstart--dbbe305b]]
- Arjun and Marcus Webb spent two hours debugging a bounce-handling bug that only appeared on the third day of a domain warmup schedule. [[arjun-and-marcus-webb-spent-two-hours-debugging-a--8f70b573]]
- Arjun upgraded the Coldstart server after a new Qwen3 point release, fixing two small prompt-template regressions. [[arjun-upgraded-the-coldstart-server-after-a-new-qw--8dfcd0c7]]
- Arjun posted in an applied-LLM engineering forum asking self-hosters and API users about running production agents, and most replies favored hosted APIs for reliability. [[before-deciding-on-a-path-forward-arjun-posted-in--01e60dba]]
- During Coldstart's April evaluation run, the self-hosted Qwen3 8B model misread one in five interested replies, and each misread interested reply resulted in a lost booked meeting. [[during-coldstart-s-april-evaluation-run-the-self-h--a73438dd]]
- Arjun re-ran the reply-triage evaluation against the Claude API and was impressed to see misreads drop to one in fifty, with a per-email cost much lower than he had anticipated once prompt caching started to take effect. [[arjun-re-ran-the-reply-triage-evaluation-against-t--9a9aed16]]
- Arjun worried that the single rented RTX 4090 was a single point of failure for a paid product, and cold restarts were taking minutes while a customer's campaign stalled. [[arjun-worried-that-the-single-rented-rtx-4090-was--bdb1ba92]]
- Arjun used a rented RTX 4090 server to self-host a Qwen3 8B model via vLLM for Coldstart's inference, but faced challenges with GPU memory fragmentation and single point of failure concerns. [[arjun-used-a-rented-rtx-4090-server-to-self-host-a--fusion:c]]
- Arjun decided to switch Coldstart's inference from the self-hosted Qwen3 stack to the Claude API after evaluations showed far fewer misread replies, and batch pricing plus prompt caching fixed the unit economics. [[arjun-decided-to-switch-coldstart-s-inference-from--497a99fe]]
- Arjun and Marcus Webb agreed to trim the deliverability contract to maintenance hours, and Marcus recommended a monitoring service for the pieces he was handing off. [[arjun-and-marcus-webb-agreed-to-trim-the-deliverab--9e45a001]]
- Arjun spent a week porting Coldstart's prompt chains from vLLM's OpenAI-compatible endpoint to the Claude API, incorporating tool-use structured outputs. [[arjun-spent-a-week-porting-coldstart-s-prompt-chai--89a52703]]
- Arjun rebuilt Coldstart's reply classifier on the Claude API using tool-call JSON schemas, drawing from Marcus Webb's original label taxonomy for the data schema. [[arjun-rebuilt-coldstart-s-reply-classifier-on-the--476c6b1f]]
- Arjun Mehta worked on stabilizing Coldstart's Claude API build, which successfully ran three pilot campaigns end to end through research, drafting, approval, sending, and reply triage. [[arjun-mehta-worked-on-stabilizing-coldstart-s-clau--32646978]]
- Arjun wrote a devlog reflecting that starting self-hosted wasn't wasted time, as running the GPU server taught him token economics and forced the eval harness that later justified the switch. [[arjun-wrote-a-devlog-reflecting-that-starting-self--8468ec1c]]
- Arjun tallied Coldstart's total inference costs and discovered the Claude API bill remained below the previous monthly GPU rental once caching and batching became active. [[arjun-tallied-coldstart-s-total-inference-costs-an--ab73df92]]

## Open questions

- What long-term maintenance and monitoring strategies did Marcus Webb recommend for Coldstart's deliverability infrastructure?

<!-- memsub:distill:end -->
