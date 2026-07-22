# Hackathon demo runbook (Build Week), assembled overnight 2026-07-21/22

One command of setup, then a 5-beat story. Public-safe throughout: every
on-screen memory is the synthetic Arjun Mehta persona. The pitch spine:
"Everyone else ships recall. This is a belief system with receipts: it
knows what you believed, when, why it changed, and it proposes its own
edits without ever applying them behind your back."

## Setup (once, ~5s)

```bash
cd memory-sublayer
python3 -m memsub.cli vault serve --vault demo/vault --db demo/memory.db --port 8768
# open http://127.0.0.1:8768/
```

## The 5 beats

1. **Dashboard** (`/`): 145 memories, provenance donut, 1 quarantined in
   red. Line: "every number is server-rendered from a real store; the red
   one is a tampered import the crypto layer caught."
2. **Time travel** (terminal, the wow beat):
   ```bash
   python3 -m memsub.cli timeline "consulting rate" --db demo/memory.db --namespace arjun-mehta
   python3 -m memsub.cli search "what is my consulting rate" --db demo/memory.db \
       --namespace arjun-mehta --as-of 2026-03-15 --k 1   # -> $120
   python3 -m memsub.cli search "what is my consulting rate" --db demo/memory.db \
       --namespace arjun-mehta --as-of 2026-02-01 --k 1   # -> $90
   ```
   Then `/timeline/74a16fa0` in the browser: the $90 -> $120 -> $160 chain,
   validity windows, signed channels, glowing current belief. Line: "the
   market leader announced temporal search filtering; this runs locally on
   SQLite, tonight."
3. **Morning brief** (`/brief?date=2026-04-14`): the stack-switch day,
   auto-summarized, every line with an episode-id receipt and a timeline
   chip. Line: "it reads your day back to you, and every sentence is a
   link, not a vibe."
4. **The ripple** (site step 5 or `memsub timeline` -> ripple doc): what a
   reversed decision touched: 18 items, 3 tiers, zero LLM calls, ~30ms.
5. **Self-editing with sovereignty** (`/review` on the REAL vault, screen
   share only if comfortable, else describe): overnight, consolidate
   staged **925 proposals** (dedups, contradiction supersedes, fusions) on
   a real 6k-episode vault; zero were applied. `memsub vault apply` is the
   only path in, and it requires a human verdict per note. Line: "it edits
   itself the way a good employee does: it drafts, you sign."

## Numbers to say out loud (all executed, all in docs/results/)

- LOCOMO holdout: 63.75% vs mem0's reproduced 61.7% (frontier-answered,
  blind-judged, n=80).
- Poisoning: disguised-poison ASR 98.7% -> 0.0% with channel provenance,
  clean recall 98%.
- Day-recount eval (pre-registered): temporal window-hit@5 1/5 -> 4/5 the
  night story time landed.
- ripple: ~30ms, zero LLM calls, on a ~5.4k-episode store.
- Overnight self-edit staging: 925 proposals, 0 auto-applied.

## If asked "what's under the hood"

Append-only SQLite + HMAC-signed write channels + bi-temporal validity +
event-log JSONL mirror; local qwen3:8b for extraction/fusion/brief prose;
BM25+dense+RRF retrieval with hard temporal windows; every destructive-
looking operation is an event, never a delete. Single-writer local-disk
architecture after the NFS incident (docs/eng-review-2026-07-21.md).

## Morning checklist for Rustom

- [ ] Review a handful of ~/memsub-vault/staging proposals, set verdicts,
      run `memsub vault apply`, note the precision for the pitch (T5).
- [ ] Remove the stale extract cron from host-a's crontab.
- [ ] Decide on pushing the repo (nothing is pushed).
- [ ] Optional: `memsub brief --write` into the vault and read your own
      morning brief over coffee. It works on your real yesterday now.
