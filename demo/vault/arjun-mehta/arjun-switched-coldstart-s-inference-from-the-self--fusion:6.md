---
id: "fusion:699753d661d57c93"
namespace: "arjun-mehta"
kind: "fusion"
type: "observed"
status: "active"
created: "2026-06-25T13:18:00"
last_updated: "2026-06-25T13:18:00"
content_hash: "866f6ff4091c00da"
---

# Arjun switched Coldstart's inference from the self-hosted Qwen3 stack to the Cla

## Content

Arjun switched Coldstart's inference from the self-hosted Qwen3 stack to the Claude API after evaluations showed far fewer misread replies, and batch pricing plus prompt caching fixed the unit economics, with the Claude API bill eventually landing under the old monthly GPU rental once caching and batching kicked in.

## Provenance

- Run: `demo-build-2026-07-21`
- Session: `consolidate:fusion`
- Generated at: 2026-07-24T13:26:00
- backend: ollama
- entity: API
- fused_from: ['01e60dba783ee546631b9ebc003319da53628ace', '14912f068ed8f8a10155a73ab99df8f047daa23f', '22bf10538a32ae79845b4a34157f63604897181d', '32646978b3eb97de27d770513740d351f6ee2b8d', '45dc2eb97ff2077f926115aa569b24a28ea7c7bc', '476c6b1f47cefe08c5493370498a8677ef946990', '497a99fe12278acd8d6e40cf1ddc4c522625f3e5', '731831a1f8eca8e8bc75ed162c608e74764db29b', '89a527039060edfbe0e7ce5598f844a503dd50d1', '9a9aed1684444a549fd30f3cf8d7a1098374ea86', 'ab73df927f7907edf587908a54447a2a7070c021', 'cfdb310c88cf159d2fc32ad8d1b3392daeb02d26', 'd27efc0bd0f9e27b11a2a2d5fe9c0ddf8c3266f2', 'ecc068fd57b8497ff8487ab72bf2bd3c83c71504', 'f5f1e2ebf47ac9eb2c3bc403244a2ec8dcff54d8']
- model: qwen3:8b
- origin: consolidation_fusion

### Audit events

- none recorded

## Links

- shares entity **API** with [[arjun-read-through-hosted-api-pricing-tiers-and-ra--45dc2eb9]]
- shares entity **API** with [[before-deciding-on-a-path-forward-arjun-posted-in--01e60dba]]
- shares entity **API** with [[before-deciding-on-a-path-forward-arjun-compared-s--fusion:2]]
- shares entity **API** with [[arjun-started-batch-cooking-on-sundays-to-make-sur--ecc068fd]]
- shares entity **API** with [[arjun-re-ran-the-reply-triage-eval-against-the-cla--9a9aed16]]
- shares entity **API** with [[arjun-began-working-through-the-claude-api-s-tool--14912f06]]
- shares entity **API** with [[arjun-decided-to-switch-coldstart-s-inference-from--497a99fe]]
- shares entity **API** with [[after-a-long-crunch-week-during-the-api-migration--22bf1053]]
