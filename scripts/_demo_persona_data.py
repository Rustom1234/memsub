"""Persona data for the build-week demo vault (Arjun Mehta, AI engineer).

Pure data, no imports beyond the standard library, deliberately separate from
scripts/build_demo_vault.py so the ~130 memory records can be reviewed on
their own (public-safe, zero real-person data -- see WORKLOG.md 2026-07-20
"Build-week demo vault" and the 2026-07-21 persona-swap entry). The persona
is a classic AI engineer modelled on the author's real workflow (self-hosted
vs API tension, evals, consulting, build-in-public), but every person,
company, number, and event here is synthetic.

Each record: (tag, date, kind, channel, bullet[, valid_from, valid_to]).
tag: stable id used to wire supersede chains and topic membership.
date: "YYYY-MM-DD", the story's world time (also becomes ts).
kind: "fact" | "decision" | "idea".
channel: memsub.provenance channel ("user_stated" default; one "imported").
bullet: the fact, rewritten into warm prose by qwen3:8b at build time --
    every proper noun/number here is preserved verbatim by the generation
    prompt, so entity extraction and the engineered showcase structures
    stay reliable regardless of the LLM's exact phrasing.
valid_from/valid_to: "YYYY-MM-DD" bi-temporal window, only set for the
    consulting-rate story (memsub/temporal.py's fields, set directly since
    the exact validity dates are already known -- see WORKLOG.md
    assumption 4).
"""

# fmt: off
RECORDS = [
    # -- inference stack: THE RIPPLE SHOWCASE ----------------------------- #
    ("stack.pricing_research1", "2026-01-08", "fact", "user_stated",
     "Arjun compared self-hosting open-weight models on a rented GPU "
     "server against hosted frontier APIs before starting his product "
     "Coldstart; self-hosting's zero per-token cost stood out "
     "immediately."),
    ("stack.pricing_research2", "2026-01-10", "fact", "user_stated",
     "Arjun read through hosted API pricing tiers and rate limits and "
     "found them confusing for a one-person product that was still "
     "pre-revenue."),
    # kind="milestone", a value no other record in this dataset uses:
    # memsub.consolidate's contradiction phase only ever pairs same-kind
    # episodes, so a unique kind makes this episode mathematically
    # unpairable there, guaranteeing it survives (still active) into the
    # fusion phase later in the same consolidate() call. kind="fact" was
    # tried first in the Maya build and still collided (paired with an
    # unrelated same-kind "fact" episode and got auto-superseded with
    # reason="contradiction" before fusion ran, stealing the engineered
    # stack-switch reversal) -- see WORKLOG.md 2026-07-20 "Build-week demo
    # vault" iteration notes. The deliberate stack-switch supersede is
    # still applied explicitly afterward, unaffected by kind.
    ("stack.decision.selfhost", "2026-01-14", "milestone", "user_stated",
     "Arjun decided to run Coldstart's inference on a self-hosted Qwen3 "
     "8B served by vLLM on a rented RTX 4090 server, drawn by the zero "
     "per-token cost, full data control, and his local-first instincts."),
    ("stack.tutorial1", "2026-01-18", "fact", "user_stated",
     "Arjun finished the vLLM quickstart, got Qwen3 8B serving on the "
     "RTX 4090, and started sketching Coldstart's draft-generation "
     "prompt chain in Python."),
    ("stack.tutorial2", "2026-01-25", "fact", "user_stated",
     "Arjun followed a vLLM structured-output tutorial to constrain "
     "Coldstart's reply classification to strict JSON labels: "
     "interested, objection, unsubscribe, and out-of-office."),
    ("stack.tutorial3", "2026-02-02", "fact", "user_stated",
     "Arjun hit a wall with GPU memory fragmentation on the RTX 4090 "
     "under concurrent Coldstart requests and spent a weekend in the "
     "vLLM Discord getting help."),
    ("stack.hiring_interview", "2026-02-10", "fact", "user_stated",
     "Arjun interviewed Marcus Webb, a freelance email-deliverability "
     "engineer, about taking on Coldstart's domain warmup, DNS, and "
     "bounce-handling work."),
    ("stack.hiring_decision", "2026-02-14", "decision", "user_stated",
     "Arjun hired Marcus Webb for ten hours a week of contract work on "
     "Coldstart's deliverability infrastructure."),
    ("stack.progress1", "2026-02-20", "fact", "user_stated",
     "Marcus Webb shipped the first version of Coldstart's sending "
     "infrastructure with domain warmup schedules and bounce tracking."),
    ("stack.progress2", "2026-03-05", "fact", "user_stated",
     "Arjun and Marcus Webb paired for two hours to debug a bounce-"
     "handling bug that only showed up on the third day of a domain "
     "warmup schedule."),
    ("stack.qwen_version", "2026-03-12", "fact", "user_stated",
     "A new Qwen3 point release shipped and Arjun upgraded the Coldstart "
     "server, fixing two small prompt-template regressions."),
    ("stack.forum_thread", "2026-04-01", "fact", "user_stated",
     "Before deciding anything, Arjun posted in an applied-LLM "
     "engineering forum asking self-hosters and API users about running "
     "production agents, and most replies favored hosted APIs for "
     "reliability."),
    ("stack.eval_results", "2026-04-02", "fact", "user_stated",
     "Coldstart's April eval run showed the self-hosted Qwen3 8B "
     "misreading one in five interested replies, and every misread "
     "interested reply is a lost booked meeting."),
    ("stack.api_eval1", "2026-04-05", "fact", "user_stated",
     "Arjun re-ran the reply-triage eval against the Claude API and was "
     "impressed: misreads dropped to one in fifty, at a per-email cost "
     "far lower than he expected once prompt caching kicked in."),
    ("stack.api_eval2", "2026-04-09", "fact", "user_stated",
     "Arjun worried that the single rented RTX 4090 was a single point "
     "of failure for a paid product, with cold restarts costing minutes "
     "while a customer's campaign stalled."),
    ("stack.decision.api", "2026-04-14", "decision", "user_stated",
     "Arjun decided to switch Coldstart's inference from the self-hosted "
     "Qwen3 stack to the Claude API after evals showed far fewer "
     "misread replies and batch pricing plus prompt caching fixed the "
     "unit economics."),
    ("stack.marcus_reassign", "2026-04-16", "fact", "user_stated",
     "Arjun and Marcus Webb agreed to trim the deliverability contract "
     "to maintenance hours, and Marcus recommended a monitoring service "
     "for the pieces he was handing off."),
    ("stack.api_migration1", "2026-04-22", "fact", "user_stated",
     "Arjun spent a week porting Coldstart's prompt chains from vLLM's "
     "OpenAI-compatible endpoint to the Claude API with tool-use "
     "structured outputs."),
    ("stack.api_migration2", "2026-05-01", "fact", "user_stated",
     "Arjun rebuilt Coldstart's reply classifier on the Claude API using "
     "tool-call JSON schemas, referencing Marcus Webb's original label "
     "taxonomy for the data schema."),
    ("stack.api_progress", "2026-05-20", "fact", "user_stated",
     "Coldstart's Claude API build hit a stable slice: three pilot "
     "campaigns running end to end through research, drafting, approval, "
     "sending, and reply triage."),
    ("stack.retro", "2026-06-10", "idea",  "user_stated",
     "Arjun wrote a devlog reflecting that starting self-hosted was not "
     "wasted time -- running the GPU server taught him token economics "
     "and forced the eval harness that later justified the switch."),
    ("stack.cost_tracking", "2026-06-25", "fact", "user_stated",
     "Arjun tallied Coldstart's total inference costs and found the "
     "Claude API bill still landed under the old monthly GPU rental "
     "once caching and batching kicked in."),

    # -- consulting clients + THE TEMPORAL STORY (rate changes twice) ----- #
    ("consulting.brightpath_intro", "2026-01-06", "fact", "user_stated",
     "Arjun took on a consulting contract with Brightpath Legal, a "
     "legal-tech startup, to build a contract-review assistant on top "
     "of an LLM."),
    ("consulting.brightpath_contact", "2026-01-06", "fact", "user_stated",
     "Grace Lin, Brightpath Legal's product lead, became Arjun's main "
     "point of contact for the contract-review work."),
    ("consulting.rate.v1", "2026-01-06", "fact", "user_stated",
     "Arjun set his consulting rate for Brightpath Legal work at ninety "
     "dollars an hour -- the amount he charges clients for a full hour "
     "of work.",
     "2026-01-06", "2026-03-01"),
    ("consulting.brightpath_delivery1", "2026-01-20", "fact", "user_stated",
     "Arjun delivered the first retrieval prototype to Brightpath "
     "Legal, and Grace Lin asked for a second phase covering clause "
     "extraction."),
    ("consulting.corvid_intro", "2026-02-01", "fact", "user_stated",
     "Arjun started an LLM-integration contract with Corvid Analytics, "
     "a small data-analytics startup."),
    ("consulting.corvid_contact", "2026-02-01", "fact", "user_stated",
     "Omar Haddad, the CTO at Corvid Analytics, briefed Arjun on a "
     "natural-language query layer for their dashboards."),
    ("consulting.invoice_habit", "2026-02-25", "fact", "user_stated",
     "Arjun switched to sending itemized invoices with per-project time "
     "logs after Brightpath Legal's finance team asked for clearer "
     "breakdowns."),
    ("consulting.corvid_delivery1", "2026-02-18", "fact", "user_stated",
     "Arjun shipped the first natural-language-to-SQL prototype for "
     "Corvid Analytics, and Omar Haddad asked him to harden it against "
     "ambiguous questions."),
    ("consulting.rate.v2", "2026-03-01", "fact", "user_stated",
     "Arjun raised the hourly rate he charges consulting clients to a "
     "hundred and twenty dollars, starting with new Brightpath Legal "
     "and Corvid Analytics contracts.",
     "2026-03-01", "2026-05-01"),
    ("consulting.taxes", "2026-03-20", "fact", "user_stated",
     "Arjun set aside thirty percent of consulting income into a "
     "separate savings account for quarterly taxes."),
    ("consulting.brightpath_delivery2", "2026-03-15", "fact", "user_stated",
     "Arjun wrapped the Brightpath Legal clause-extraction phase at the "
     "new rate without any pushback from Grace Lin."),
    ("consulting.corvid_referral", "2026-04-10", "fact", "user_stated",
     "Omar Haddad referred Arjun to two other startups looking for LLM "
     "integration work."),
    ("consulting.corvid_delivery2", "2026-04-25", "fact", "user_stated",
     "Arjun shipped the hardened query layer for Corvid Analytics, and "
     "Omar Haddad said internal adoption of the dashboards looked "
     "promising."),
    ("consulting.rate.v3", "2026-05-01", "fact", "user_stated",
     "Arjun raised the rate he charges consulting clients again, to a "
     "hundred and sixty dollars an hour, after Omar Haddad's referrals "
     "filled his calendar through the summer.",
     "2026-05-01", None),
    ("consulting.new_client1", "2026-05-08", "fact", "user_stated",
     "Arjun started a short retrieval-pipeline contract with one of "
     "Omar Haddad's referrals, a health-records startup called Lantern "
     "Health."),
    ("consulting.balance_note", "2026-05-15", "idea", "user_stated",
     "Arjun started blocking consulting work to three afternoons a week "
     "so Coldstart's Claude API migration would not stall."),
    ("consulting.lantern_delivery", "2026-06-01", "fact", "user_stated",
     "Arjun delivered the first retrieval pipeline milestone for "
     "Lantern Health, the startup Omar Haddad referred him to."),
    ("consulting.brightpath_wrap", "2026-06-05", "fact", "user_stated",
     "Arjun's Brightpath Legal contract with Grace Lin wrapped up after "
     "a final round of work for their pilot launch."),
    ("consulting.brightpath_wrap_note", "2026-06-08", "fact", "user_stated",
     "Grace Lin asked Arjun to stay on Brightpath Legal's referral list "
     "for future contract work even after the main engagement wrapped."),
    ("consulting.contract_template", "2026-01-25", "idea", "user_stated",
     "Arjun wrote a reusable consulting contract template after the "
     "Brightpath Legal negotiation took longer than expected."),
    ("consulting.calendar_review", "2026-06-20", "idea", "user_stated",
     "Arjun plans to block one full consulting-free week each quarter "
     "once Coldstart nears launch, to protect crunch time."),

    # -- product design decisions (Coldstart) ----------------------------- #
    ("product.premise", "2026-01-05", "idea", "user_stated",
     "Arjun's core idea for Coldstart is an agent that reads a "
     "founder's story and product, researches prospects, writes "
     "genuinely personal cold emails, and learns from every reply."),
    ("product.core_loop", "2026-01-12", "decision", "user_stated",
     "Arjun settled on a core loop where the agent researches and "
     "drafts overnight, the founder reviews an approval queue over "
     "coffee, and the agent sends and watches replies through the day."),
    ("product.approval_gate", "2026-01-22", "decision", "user_stated",
     "Arjun decided every first-touch email in Coldstart goes through a "
     "human approval gate, avoiding the autonomous-sending behavior he "
     "found reckless in other outreach tools."),
    ("product.no_autopilot", "2026-02-05", "decision", "user_stated",
     "Arjun decided against a fully autonomous mode entirely -- "
     "Coldstart is about leverage with judgment, and spam is a failure "
     "state, not a growth setting."),
    ("product.objection_log", "2026-02-12", "idea", "user_stated",
     "Arjun's idea: an objection log where objections raised in replies "
     "persist and quietly reshape every later draft in the campaign."),
    ("product.scope", "2026-02-22", "fact", "user_stated",
     "Arjun scoped Coldstart's first version to five modules: prospect "
     "research, drafting, the approval queue, reply triage, and "
     "campaign analytics."),
    ("product.seed_prompts", "2026-03-02", "fact", "user_stated",
     "Arjun wrote the first draft of Coldstart's seed prompts, "
     "establishing the plain, non-salesy voice as the product's "
     "central promise."),
    ("product.pilot1", "2026-03-18", "fact", "user_stated",
     "Arjun ran a pilot with two founders and found the onboarding "
     "empty state gave no sense of what a good campaign looked like."),
    ("product.sample_campaign", "2026-03-22", "decision", "user_stated",
     "Arjun added a sample campaign walkthrough triggered from the "
     "empty state, so founders who want guidance can ask for it without "
     "a forced tutorial."),
    ("product.warmup_structure", "2026-04-01", "decision", "user_stated",
     "Arjun restructured Coldstart's onboarding into three stages tied "
     "to the domain warmup schedule, so sending capacity unlocks as "
     "deliverability trust builds."),
    ("product.warmup_infra_link", "2026-04-08", "fact", "user_stated",
     "Arjun linked the staged onboarding directly to Marcus Webb's "
     "warmup infrastructure so pacing and deliverability reinforce each "
     "other."),
    ("product.handoff_idea", "2026-04-18", "idea", "user_stated",
     "Arjun's idea: a handoff moment where a positive reply flips the "
     "thread to the founder, with the agent drafting the meeting "
     "proposal and then stepping back."),
    ("product.prompts_rewrite", "2026-05-05", "fact", "user_stated",
     "Arjun rewrote Coldstart's seed prompts after the second pilot "
     "showed drafts drifting salesy over long campaigns."),
    ("product.pilot2", "2026-05-10", "fact", "user_stated",
     "A second pilot after the Claude API migration confirmed the "
     "staged onboarding read clearly to new founders."),
    ("product.copywriter", "2026-05-18", "fact", "user_stated",
     "Arjun brought on a part-time copywriter to pressure-test the "
     "drafting voice that Coldstart's approval queue depends on."),
    ("product.accessibility", "2026-05-30", "decision", "user_stated",
     "Arjun added a plain-text email mode and full keyboard navigation "
     "after a pilot founder using a screen reader struggled with the "
     "approval queue."),
    ("product.time_target", "2026-05-25", "fact", "user_stated",
     "Arjun settled on a target of a first booked meeting within "
     "fourteen days of signup, matching the promise on Coldstart's "
     "landing page."),
    ("product.landing_hero", "2026-06-01", "fact", "user_stated",
     "Arjun finalized Coldstart's landing hero, using a live replay of "
     "a real campaign's approval queue instead of a static screenshot."),
    ("product.beta_scope", "2026-06-15", "decision", "user_stated",
     "Arjun decided the public beta would cover only the first campaign "
     "flow, ending before multi-channel sequences, to keep the promise "
     "small and keepable."),

    # -- interface (CLI -> dashboard contradiction/supersede pivot) ------- #
    ("ui.cli_first", "2026-01-16", "decision", "user_stated",
     "Arjun initially decided Coldstart would ship as a CLI tool to "
     "keep the scope manageable for a solo engineer."),
    ("ui.cli_test", "2026-01-28", "fact", "user_stated",
     "Arjun mocked up Coldstart's campaign flow as a CLI and felt it "
     "undersold the product -- watching the agent work is the magic, "
     "and a terminal hides it."),
    ("ui.dashboard_test", "2026-02-08", "fact", "user_stated",
     "Arjun prototyped a web dashboard with a live agent-activity feed "
     "for the same campaign flow and felt it captured the magic far "
     "better than the CLI."),
    ("ui.dashboard_decision", "2026-02-15", "decision", "user_stated",
     "Arjun decided to switch Coldstart's interface to a web dashboard, "
     "keeping a thin CLI only for power users and scripting."),
    ("ui.design_reading", "2026-02-25", "fact", "user_stated",
     "Arjun read a chapter on trust in interface design that shaped the "
     "style-guide discussion he later had with Lena Petrova."),
    ("ui.designer_search", "2026-02-20", "fact", "user_stated",
     "Arjun started looking for a product designer who could make an "
     "agent's work feel legible and calm instead of magical and "
     "opaque."),
    ("ui.hire_lena", "2026-03-01", "decision", "user_stated",
     "Arjun hired Lena Petrova, a freelance product designer, to design "
     "Coldstart's dashboard."),
    ("ui.design_system", "2026-03-10", "fact", "user_stated",
     "Lena Petrova delivered a design system mapping each campaign "
     "stage to its own visual state."),
    ("ui.first_screen", "2026-03-25", "fact", "user_stated",
     "Lena Petrova finished the approval-queue screen, and Arjun wired "
     "it into the live Coldstart build."),
    ("ui.component_restraint", "2026-04-05", "fact", "user_stated",
     "Arjun kept Coldstart's marketing site deliberately plain, keeping "
     "it simple against Lena Petrova's detailed dashboard."),
    ("ui.tokens_lock", "2026-04-15", "fact", "user_stated",
     "Arjun and Lena Petrova locked the final design tokens so no "
     "dashboard screens would drift in tone after the inference "
     "migration."),
    ("ui.migration", "2026-04-28", "fact", "user_stated",
     "Arjun reused every dashboard component unchanged through the "
     "Claude API migration, since the interface never knew which model "
     "was underneath."),
    ("ui.icons", "2026-05-08", "fact", "user_stated",
     "Arjun designed Coldstart's dashboard icons to match Lena "
     "Petrova's system instead of a stock icon set."),
    ("ui.activity_feed", "2026-05-12", "fact", "user_stated",
     "Arjun added a live activity feed so founders can watch the agent "
     "research prospects, draft emails, and file replies in real time."),
    ("ui.remaining_screens", "2026-05-28", "fact", "user_stated",
     "Lena Petrova delivered the analytics and settings screens, "
     "leaving only the onboarding flow outstanding."),
    ("ui.style_guide", "2026-06-08", "idea", "user_stated",
     "Arjun and Lena Petrova wrote a short style guide documenting the "
     "design tokens so future contractors can match the look."),
    ("ui.final_screen", "2026-06-20", "fact", "user_stated",
     "Lena Petrova finished the onboarding flow, completing the design "
     "for all five of Coldstart's modules."),

    # -- health / habits (wake-time contradiction/supersede pivot) -------- #
    ("health.sleep_5am", "2026-01-08", "decision", "user_stated",
     "Arjun committed to a five a.m. wake time to get focused Coldstart "
     "work done before consulting calls started."),
    ("health.standing_desk", "2026-01-10", "fact", "user_stated",
     "Arjun switched to a standing desk after long development sessions "
     "left his lower back sore."),
    ("health.running", "2026-01-14", "fact", "user_stated",
     "Arjun started running three times a week to break up long "
     "debugging sessions on the Coldstart server."),
    ("health.wrist_pain", "2026-01-30", "fact", "user_stated",
     "Arjun started noticing wrist pain from long typing sessions and "
     "looked into ergonomic keyboards."),
    ("health.caffeine", "2026-02-10", "fact", "user_stated",
     "Arjun cut caffeine after two p.m. after noticing it was wrecking "
     "his wake-up schedule."),
    ("health.ergo_keyboard", "2026-02-05", "fact", "user_stated",
     "Arjun bought a split ergonomic keyboard and the wrist pain eased "
     "within two weeks."),
    ("health.sleep_struggle", "2026-02-20", "fact", "user_stated",
     "Arjun found the five a.m. wake time was not sticking, especially "
     "after late-night GPU debugging sessions."),
    ("health.sleep_8am", "2026-03-01", "decision", "user_stated",
     "Arjun settled on an eight a.m. wake time instead, which fit his "
     "natural rhythm and late-night debugging sessions better."),
    ("health.screen_breaks", "2026-03-05", "fact", "user_stated",
     "Arjun started a twenty-minute walk break every two hours after a "
     "full day of migration work left his eyes strained."),
    ("health.running_streak", "2026-03-15", "fact", "user_stated",
     "Arjun kept up the running habit for two months and noticed his "
     "best Coldstart product ideas kept showing up mid-run."),
    ("health.meal_prep", "2026-04-01", "fact", "user_stated",
     "Arjun began batch-cooking on Sundays to avoid skipping lunch "
     "during crunch weeks on the Claude API migration."),
    ("health.posture_reminder", "2026-04-20", "fact", "user_stated",
     "Arjun set an hourly posture reminder after a long crunch week "
     "during the API migration."),
    ("health.wrist_recheck", "2026-05-10", "fact", "user_stated",
     "Arjun's wrist pain stayed manageable through the migration thanks "
     "to the ergonomic keyboard and regular screen breaks."),
    ("health.social_time", "2026-05-18", "fact", "user_stated",
     "Arjun started scheduling one no-work evening a week with friends "
     "after realizing consulting and Coldstart had eaten every evening "
     "in March."),
    ("health.running_race", "2026-06-01", "fact", "user_stated",
     "Arjun ran a local ten-kilometer race, his longest run since "
     "starting Coldstart."),

    # -- reading list / research ------------------------------------------ #
    ("reading.ml_systems", "2026-01-11", "fact", "user_stated",
     "Arjun started reading Designing Machine Learning Systems by Chip "
     "Huyen for help structuring Coldstart's data pipeline."),
    ("reading.vllm_docs", "2026-01-20", "fact", "user_stated",
     "Arjun bookmarked the vLLM documentation's serving and structured-"
     "output sections as ongoing references during development."),
    ("reading.eval_lens", "2026-01-24", "idea", "user_stated",
     "Arjun's idea: apply Chip Huyen's data-flywheel framing to "
     "Coldstart's reply triage once pilots reveal whether it works."),
    ("reading.postmortem", "2026-02-03", "fact", "user_stated",
     "Arjun read a postmortem of a failed cold-email startup and took "
     "notes on how deliverability, not copy quality, was what killed "
     "it."),
    # Idea-resonance showcase (2026-07-21): an old, distant note -- about
    # human memory and forgetting, nothing to do with prospect databases on
    # the surface -- for `python -m memsub.resonance` to surface later
    # against Arjun's "Coldstart's prospect memory should feel alive" idea.
    # See docs/demo-2026-07-20.md and site/index.html's resonance tour step.
    ("reading.forgetting_principle", "2026-02-08", "fact", "user_stated",
     "Arjun read a cognitive-science piece arguing that useful memory "
     "depends on aggressive forgetting -- recall works because noise "
     "gets pruned, not despite it -- and noted it as a principle worth "
     "reusing beyond the article."),
    ("reading.newsletter", "2026-02-15", "fact", "user_stated",
     "Arjun subscribed to an applied-LLM engineering newsletter that "
     "regularly covers agent-product patterns."),
    ("reading.superhuman_analysis", "2026-02-27", "fact", "user_stated",
     "Arjun read an analysis of Superhuman's onboarding ritual, "
     "directly relevant to Coldstart's morning approval-queue habit."),
    ("reading.karpathy_videos", "2026-03-08", "fact", "user_stated",
     "Arjun worked back through Andrej Karpathy's neural-networks-from-"
     "scratch videos as a refresher while Coldstart's eval runs "
     "finished overnight."),
    ("reading.idea_followup_rhythm", "2026-03-14", "idea", "user_stated",
     "Arjun's idea: borrow spaced-repetition scheduling for follow-up "
     "timing, so each prospect gets their own resurfacing rhythm "
     "instead of a fixed cadence."),
    ("reading.claude_docs", "2026-04-06", "fact", "user_stated",
     "Arjun started working through the Claude API's tool-use "
     "documentation ahead of the inference migration."),
    ("reading.clay_teardown", "2026-04-14", "fact", "user_stated",
     "Arjun read a teardown of Clay's prospect-enrichment workflows, "
     "directly relevant to Coldstart's research module."),
    ("reading.deliverability_guide", "2026-05-02", "fact", "user_stated",
     "Arjun read a deep guide on email deliverability, informing the "
     "monitoring plan he and Marcus Webb set up."),
    ("reading.plg_book", "2026-05-20", "fact", "user_stated",
     "Arjun started reading a book on product-led growth for solo "
     "founders, focused on waitlist conversion before launch."),
    ("reading.pricing_podcast", "2026-06-12", "fact", "user_stated",
     "Arjun started listening to an indie-SaaS podcast episode about "
     "pricing a first commercial release."),
    ("reading.idea_meeting_wall", "2026-06-05", "idea", "user_stated",
     "Arjun's idea: a public wall of booked meetings on Coldstart's "
     "landing page, tying the product's promise to visible outcomes."),

    # -- community / launch ------------------------------------------------ #
    ("launch.devlog1", "2026-02-01", "fact", "user_stated",
     "Arjun published the first Coldstart devlog post, introducing the "
     "premise and the early CLI prototype."),
    ("launch.waitlist_page", "2026-03-01", "fact", "user_stated",
     "Arjun set up Coldstart's waitlist landing page with early "
     "screenshots from the prototype dashboard."),
    ("launch.waitlist_100", "2026-03-20", "fact", "user_stated",
     "Coldstart's waitlist crossed one hundred signups after the devlog "
     "post was shared on an indie-hackers forum."),
    ("launch.discord", "2026-04-25", "fact", "user_stated",
     "Arjun opened a small Discord server for Coldstart and answered "
     "questions directly from the first waitlisters."),
    ("launch.devlog2", "2026-04-20", "fact", "user_stated",
     "Arjun published a devlog explaining the switch from the self-"
     "hosted Qwen3 stack to the Claude API, which drew more comments "
     "than any previous post."),
    ("launch.screenshot_update", "2026-05-15", "fact", "user_stated",
     "Arjun updated Coldstart's landing page screenshots to show Lena "
     "Petrova's finished dashboard design."),
    ("launch.press_kit", "2026-05-25", "fact", "user_stated",
     "Arjun assembled a launch kit with Lena Petrova's dashboard "
     "screens and a short demo video cut from a live campaign run."),
    ("launch.waitlist_500", "2026-06-01", "fact", "user_stated",
     "Coldstart's waitlist crossed five hundred signups after the new "
     "screenshots went up."),
    ("launch.video_feedback", "2026-06-05", "fact", "user_stated",
     "Arjun shared the demo video with a small feedback Discord and "
     "revised the pacing of the opening shot."),
    ("launch.showcase_app", "2026-06-10", "fact", "user_stated",
     "Arjun applied to a fall AI-product showcase for a live demo slot "
     "for Coldstart."),
    ("launch.beta_plan", "2026-06-18", "decision", "user_stated",
     "Arjun decided to launch the public beta alongside a waitlist-"
     "focused devlog post covering only the first campaign flow."),
    ("launch.newsletter_signup", "2026-06-22", "fact", "user_stated",
     "Arjun added an email newsletter signup to the landing page ahead "
     "of the beta launch."),

    # -- quarantine exhibit (imported channel, tampered post-write) ------- #
    ("quarantine.bookmark", "2026-04-12", "fact", "imported",
     "Arjun bookmarked the launch page of a competing AI outreach "
     "agent to keep an eye on the market."),
]
# fmt: on

# -- deliberate supersede chains: (loser_tag, winner_tag, reason) --------- #
SUPERSEDE_CHAINS = [
    ("stack.decision.selfhost", "stack.decision.api", "stack_switch"),
    ("consulting.rate.v1", "consulting.rate.v2", "rate_change"),
    ("consulting.rate.v2", "consulting.rate.v3", "rate_change"),
    ("ui.cli_first", "ui.dashboard_decision", "interface_pivot"),
    ("health.sleep_5am", "health.sleep_8am", "habit_pivot"),
]

# -- topic notes: slug -> (title_seed, [member tags]) --------------------- #
TOPICS = {
    "inference-stack-and-tooling": [
        "stack.pricing_research1", "stack.pricing_research2",
        "stack.decision.selfhost", "stack.tutorial1", "stack.tutorial2",
        "stack.tutorial3", "stack.hiring_interview", "stack.hiring_decision",
        "stack.progress1", "stack.progress2", "stack.qwen_version",
        "stack.forum_thread", "stack.eval_results",
        "stack.api_eval1", "stack.api_eval2", "stack.decision.api",
        "stack.marcus_reassign", "stack.api_migration1",
        "stack.api_migration2", "stack.api_progress", "stack.retro",
        "stack.cost_tracking",
    ],
    "consulting-clients-and-rates": [
        "consulting.brightpath_intro", "consulting.brightpath_contact",
        "consulting.rate.v1", "consulting.brightpath_delivery1",
        "consulting.corvid_intro", "consulting.corvid_contact",
        "consulting.invoice_habit", "consulting.corvid_delivery1",
        "consulting.rate.v2", "consulting.taxes",
        "consulting.brightpath_delivery2", "consulting.corvid_referral",
        "consulting.corvid_delivery2", "consulting.rate.v3",
        "consulting.new_client1", "consulting.balance_note",
        "consulting.lantern_delivery", "consulting.brightpath_wrap",
        "consulting.brightpath_wrap_note", "consulting.contract_template",
        "consulting.calendar_review",
    ],
    "product-design-coldstart": [
        "product.premise", "product.core_loop", "product.approval_gate",
        "product.no_autopilot", "product.objection_log", "product.scope",
        "product.seed_prompts", "product.pilot1", "product.sample_campaign",
        "product.warmup_structure", "product.warmup_infra_link",
        "product.handoff_idea", "product.prompts_rewrite", "product.pilot2",
        "product.copywriter", "product.accessibility", "product.time_target",
        "product.landing_hero", "product.beta_scope",
    ],
    "interface-and-design": [
        "ui.cli_first", "ui.cli_test", "ui.dashboard_test",
        "ui.dashboard_decision", "ui.design_reading", "ui.designer_search",
        "ui.hire_lena", "ui.design_system", "ui.first_screen",
        "ui.component_restraint", "ui.tokens_lock", "ui.migration",
        "ui.icons", "ui.activity_feed", "ui.remaining_screens",
        "ui.style_guide", "ui.final_screen",
    ],
    "health-and-habits": [
        "health.sleep_5am", "health.standing_desk", "health.running",
        "health.wrist_pain", "health.caffeine", "health.ergo_keyboard",
        "health.sleep_struggle", "health.sleep_8am", "health.screen_breaks",
        "health.running_streak", "health.meal_prep", "health.posture_reminder",
        "health.wrist_recheck", "health.social_time", "health.running_race",
    ],
    "reading-and-research": [
        "reading.ml_systems", "reading.vllm_docs",
        "reading.eval_lens", "reading.postmortem",
        "reading.forgetting_principle", "reading.newsletter",
        "reading.superhuman_analysis", "reading.karpathy_videos",
        "reading.idea_followup_rhythm", "reading.claude_docs",
        "reading.clay_teardown", "reading.deliverability_guide",
        "reading.plg_book", "reading.pricing_podcast",
        "reading.idea_meeting_wall",
    ],
    "launch-and-community": [
        "launch.devlog1", "launch.waitlist_page", "launch.waitlist_100",
        "launch.discord", "launch.devlog2", "launch.screenshot_update",
        "launch.press_kit", "launch.waitlist_500",
        "launch.video_feedback", "launch.showcase_app",
        "launch.beta_plan", "launch.newsletter_signup",
    ],
    # The second citation for the ripple showcase: a cross-cutting "key
    # decisions" retrospective note that legitimately includes the stack
    # decision alongside every other pivotal call Arjun made -- this is what
    # gives ripple(stack.decision.selfhost) a second topic-tier edge (see
    # WORKLOG.md assumption 3: auto-clustering can never do this, since one
    # episode can only land in one disjoint cluster).
    "key-project-decisions": [
        "stack.decision.selfhost", "stack.decision.api",
        "stack.hiring_decision", "ui.cli_first", "ui.dashboard_decision",
        "health.sleep_5am", "health.sleep_8am", "consulting.rate.v2",
        "consulting.rate.v3", "product.sample_campaign", "product.beta_scope",
        "launch.beta_plan",
    ],
}

# The three planned demo questions (memsub.sdk.MemoryClient.search sanity
# check) and the ripple showcase memory tag.
DEMO_QUESTIONS = [
    "what inference stack is Arjun using and why",
    "what does Arjun charge",
    "what has Arjun decided about Coldstart's interface",
]
RIPPLE_TAG = "stack.decision.selfhost"
