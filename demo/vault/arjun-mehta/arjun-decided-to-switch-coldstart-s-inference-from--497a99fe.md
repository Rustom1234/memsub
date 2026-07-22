---
id: "497a99fe12278acd8d6e40cf1ddc4c522625f3e5"
namespace: "arjun-mehta"
kind: "decision"
type: "observed"
status: "active"
created: "2026-04-14T09:49:00"
last_updated: "2026-04-14T09:49:00"
content_hash: "e1b13139307ae0f3"
---

# Arjun decided to switch Coldstart's inference from the self-hosted Qwen3 stack t

## Content

Arjun decided to switch Coldstart's inference from the self-hosted Qwen3 stack to the Claude API after evaluations showed far fewer misread replies, and batch pricing plus prompt caching fixed the unit economics.

## Provenance

- Run: `demo-build-2026-07-21`
- Session: `arjun-2026-04-14`
- channel: user_stated
- channel_sig: 35160221c00fd596d604f05e5b713f894542ae4b83b07b53d5ea94d43b5274a1
- origin: demo_build
- persona: arjun-mehta
- tag: stack.decision.api

### Audit events

- 2026-07-21T16:56:54  **fused_into**  by=fusion:5b96bbe26be28bbe entity=Claude
- 2026-07-21T16:56:53  **fused_into**  by=fusion:699753d661d57c93 entity=API
- 2026-07-21T16:56:51  **fused_into**  by=fusion:a07112598f75af73 entity=Coldstart
- 2026-07-21T16:56:49  **fused_into**  by=fusion:7db35a4994cdfaec entity=Arjun
- 2026-07-21T16:56:37  **graphed**

## Links

- supersedes [[arjun-chose-to-run-coldstart-s-inference-on-a-self--fd2ddec5]]
- shares entity **API** with [[arjun-read-through-hosted-api-pricing-tiers-and-ra--45dc2eb9]]
- shares entity **API** with [[before-deciding-on-a-path-forward-arjun-posted-in--01e60dba]]
- shares entity **API** with [[arjun-started-batch-cooking-on-sundays-to-make-sur--ecc068fd]]
- shares entity **API** with [[arjun-re-ran-the-reply-triage-evaluation-against-t--9a9aed16]]
- shares entity **API** with [[arjun-began-going-through-the-claude-api-s-tool-us--14912f06]]
- shares entity **API** with [[after-a-long-crunch-week-during-the-api-migration--22bf1053]]
- shares entity **API** with [[arjun-published-a-devlog-explaining-the-switch-fro--731831a1]]
- shares entity **API** with [[arjun-spent-a-week-porting-coldstart-s-prompt-chai--89a52703]]
