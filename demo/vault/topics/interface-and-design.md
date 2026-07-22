---
type: "topic"
namespace: "topics"
slug: "interface-and-design"
member_count: 17
member_hash: "77ba229b71d6d31d"
created: "2026-07-21T16:58:02"
updated: "2026-07-21T16:58:02"
---

<!-- memsub:distill:begin -->

# Designing Coldstart's Dashboard

Coldstart's interface evolved from a CLI tool to a web dashboard, emphasizing transparency and user experience. Arjun and Lena Petrova focused on making the agent's work visible and trustworthy, resulting in a detailed and cohesive design system.

## Key facts

- Arjun initially planned for Coldstart to launch as a CLI tool to keep the project manageable for a solo engineer. [[arjun-initially-decided-coldstart-would-ship-as-a--de5539d6]]
- Arjun created a CLI mockup for Coldstart's campaign flow but felt it failed to showcase the product's magic. [[arjun-mocked-up-coldstart-s-campaign-flow-as-a-cli--2a7ed98b]]
- Arjun built a web dashboard with a live agent-activity feed, which he believed better captured the product's essence. [[arjun-prototyped-a-web-dashboard-with-a-live-agent--12e18865]]
- Arjun decided to switch Coldstart's interface to a web dashboard, retaining a CLI for advanced users and scripting. [[arjun-decided-to-switch-coldstart-s-interface-to-a--5a0dc3f6]]
- Arjun sought a product designer who could make the agent's work feel legible and calm, rather than mysterious and opaque. [[arjun-began-searching-for-a-product-designer-who-c--b2f62a83]]
- Arjun read about trust in interface design, which influenced his discussions with Lena Petrova about style guides. [[arjun-read-a-chapter-on-trust-in-interface-design--fd8f0b76]]
- Arjun hired Lena Petro, a freelance product designer, to design Coldstart's dashboard. [[arjun-hired-lena-petrova-a-freelance-product-desig--dc816a5c]]
- Lena created a design system that visually represented each campaign stage with its own distinct state. [[lena-petrova-created-a-design-system-that-mapped-e--4707f328]]
- Lena completed the approval-queue screen, which Arjun integrated into the live Coldstart build. [[lena-petrova-finished-the-approval-queue-screen-an--732274d6]]
- Arjun maintained a simple marketing site while Lena's dashboard was intricate and detailed. [[arjun-kept-coldstart-s-marketing-site-deliberately--ed0f5b00]]
- Arjun and Lena locked design tokens to ensure the dashboard's tone remained consistent during the inference migration. [[arjun-and-lena-petrova-locked-the-final-design-tok--26313468]]
- Arjun reused all dashboard components during the Claude API migration since the interface was unaware of the underlying model. [[arjun-reused-every-dashboard-component-unchanged-d--cfdb310c]]
- Arjun designed dashboard icons to align with Lena's system instead of using generic icons. [[arjun-designed-coldstart-s-dashboard-icons-to-matc--4627241e]]
- Arjun added a live activity feed so founders could observe the agent's real-time work on prospects and emails. [[arjun-added-a-live-activity-feed-so-founders-could--63c3ce9b]]
- Lena delivered analytics and settings screens, leaving the onboarding flow as the final task. [[lena-petrova-delivered-the-analytics-and-settings--5a23966a]]
- Arjun and Lena wrote a style guide for design tokens to ensure future contractors matched the visual language. [[arjun-and-lena-petrova-wrote-a-short-style-guide-d--015e81fe]]
- Lena completed the onboarding flow, finishing the design for all five Coldstart modules. [[lena-petrova-finished-the-onboarding-flow-completi--90fafef6]]

## Open questions

- How will the design system scale as Coldstart adds more features?

<!-- memsub:distill:end -->
