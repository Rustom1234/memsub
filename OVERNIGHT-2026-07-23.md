# Overnight plan 2026-07-23 (Rustom asleep, autonomous iterate/review/improve)

Durability: every track commits checkpoints + git bundles to ~/repo-bundles/;
progress files updated per round; state mirrored to Claude memory. Autosave
cron (*/10) runs on memsub. Never push, never create a GitHub remote.

## Track A - memsub: unify + polish (sonnet, worktrees, review loops)
- One "giant memsub UI" in vault_serve: top-nav shell + tabs (Showcase,
  Dashboard, Garden, Bridges, Blackbox, Loops, Graph, Ask).
- Showcase landing that shows off all products with live stats.
- Bridges as a TAB inside the memsub UI (route /bridges), not just the
  standalone 8768 server.
- Garden as a UI tab (served preview of published notes + status/review),
  privacy model preserved.
- Keep finding issues, improving, reviewing, repeating. Professional, clean.
- Tests stay green (681+), no em dashes, never touch the live ~/.memsub store
  (use the consolidated copy / demo vault).

## Track B - NIGHTSHIFT (Fable overseer, ~/projects/nightshift)
Open-source overnight autonomous harden+advance daemon.
- Builds a SANDBOX (copy/worktree of the target repo) it works out of, so it
  never touches the real codebase.
- HARDEN loop: edit/attack/review/strengthen non-stop; findings carry a
  reproducing failing test.
- ADVANCE loop: 3 local-model instances as subagents trying ideas forward,
  keep only what passes tests + improves a metric.
- Uses the best open-weight model the machine can run (auto-detect ollama;
  qwen3:8b now, better coder model pulling in background).
- GATING (all required, least-prioritized work, runs only when there is
  space): on AC power (not battery), morning trigger (for now), no other
  heavy overnight processes running, load/GPU headroom available.
- Lessons/code borrowed from "open claude" + OpenHands/SWE-agent/Aider.
- Nice UI. Fable subagent overseer plans and directs the whole build.

## Track C - THE SERIAL (Fable overseer, ~/projects/serial)
Endless fantasy saga written to Rustom's taste, a chapter at a time to his
phone (Telegram), a persistent self-remembering world. Fable overseer plans,
directs, and keeps improving independently.

## Loop mechanism
Background agents; on each completion I review + merge/checkpoint + relaunch an
improvement round. Continue through the night. Honest morning report: what is
real vs stubbed.
