# Daily incremental extraction

`scripts/daily_extract.sh` runs the three-stage pipeline (extract -> consolidate
-> vault sync) over Rustom's real Claude Code transcripts, into the live store
and vault used by the MCP server (`~/.memsub/memory.db`, `~/memsub-vault`,
namespace `claude-code`).

It is safe to run repeatedly and safe to run daily:

- **Resumable / idempotent.** It always scans the full transcript corpus
  under `~/.claude/projects/` (excluding `subagents/` and `tool-results/`
  subpaths, matching the sampling convention in `docs/dogfood-2026-07-12.md`
  and `docs/dogfood-2-2026-07-12.md`), but `memsub extract`'s own
  `extraction_checkpoints` table (keyed on source path + content hash) skips
  every file already extracted and unchanged, so a rerun over an unchanged
  corpus does almost no work. New/changed transcripts are the only ones that
  cost an LLM call.
- **Lock-protected.** `flock` on a fixed lock file
  (`~/.memsub/daily_extract.lock`) means a second invocation while one is
  still running exits 0 immediately (logged as `SKIP`) instead of racing it.
- **Local-only, fails clean.** The pipeline always forces `--backend ollama`
  (never falls back to a cloud API); if ollama is not reachable at
  `$OLLAMA_HOST` (default `http://localhost:11434`), the script logs `ABORT`
  with the reason and exits 1 without touching the store.
- **Logged.** Every run appends timestamped lines to
  `~/.memsub/daily_extract.log` (start, file count, per-stage one-line
  summary, and DONE/FAIL/ABORT/SKIP).

All of the paths above are overridable by environment variable
(`MEMSUB_DB`, `MEMSUB_VAULT`, `MEMSUB_NAMESPACE`, `MEMSUB_TRANSCRIPTS_DIR`,
`MEMSUB_DAILY_LOCK`, `MEMSUB_DAILY_LOG`, `OLLAMA_HOST`), mainly so tests
(`tests/test_daily_extract.py`) can point the script at a throwaway
environment instead of the real store/vault/log.

## Scheduling (not installed — do this yourself)

This script does **not** install any crontab entry. To run it daily at
05:00, add this line yourself:

```
0 5 * * * /home/user/projects/memory-sublayer/scripts/daily_extract.sh
```

Install:

```bash
(crontab -l 2>/dev/null; echo "0 5 * * * /home/user/projects/memory-sublayer/scripts/daily_extract.sh") | crontab -
```

Remove:

```bash
crontab -l | grep -v 'daily_extract.sh' | crontab -
```

Verify installed/removed:

```bash
crontab -l | grep daily_extract.sh   # prints the line if installed, nothing if not
```

Cron runs with a minimal environment (no `$OLLAMA_HOST`, no login-shell
`PATH`), but the script hardcodes its own defaults for every path it needs,
so no extra `crontab` environment setup is required beyond the line above.
