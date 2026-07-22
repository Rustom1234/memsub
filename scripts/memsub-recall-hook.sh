#!/usr/bin/env bash
# memsub auto-recall hook (UserPromptSubmit): before every prompt, ask
# `memsub recall` (read-only hybrid retrieval, zero LLM calls) whether
# anything in the claude-code namespace is relevant enough to inject, and if
# so, hand it back to Claude Code as additionalContext. Silence is the
# default outcome: most prompts print nothing at all (see the CLI's
# --min-relevance floor, memsub/cli.py:_cmd_recall), and this script itself
# only ever emits the hookSpecificOutput JSON when memsub recall printed a
# non-empty block.
#
# Input: hook JSON on stdin (needs .prompt). Output: nothing (silence), or
# {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
#   "additionalContext": "<the recall block>"}} on stdout.
#
# Companion to ~/scripts/memsub-inbox.sh/memsub-inbox-sweep.sh (durable
# capture layer): this is the read side, those are the write side. Lives in
# this repo's scripts/ (like scripts/daily_extract.sh), not ~/scripts/,
# because it is memsub product code, not personal machine glue -- only
# ~/scripts/install-durable-capture.sh (the one-shot hook/cron installer,
# which cannot live in this repo since it edits ~/.claude/settings.json and
# the user's crontab) references it, by absolute path.
#
# Same conventions as the other capture-layer scripts: no network, env-var
# overridable, fails silently (never blocks a prompt) on any error.
# `timeout` is a safety net only, not the real latency control -- real
# budget compliance is memsub recall staying fast on its own (measured
# 0.65-0.83s wall against the live 4200+-episode store, see WORKLOG.md for
# the timed runs); if that regresses (e.g. the store grows another 5-10x, or
# a caller passes --embedder ollama), the fix is a warm daemon holding a
# pre-built index across invocations, not a longer timeout here.

set -u

MEMSUB_BIN="${MEMSUB_BIN:-/home/user/projects/memory-sublayer/.venv/bin/memsub}"
DB="${MEMSUB_DB:-$HOME/.memsub/memory.db}"
NAMESPACE="${MEMSUB_RECALL_NAMESPACE:-claude-code}"
K="${MEMSUB_RECALL_K:-3}"
MAX_CHARS="${MEMSUB_RECALL_MAX_CHARS:-700}"
BUDGET_SECS="${MEMSUB_RECALL_TIMEOUT:-2}"

payload="$(cat)"
prompt="$(printf '%s' "$payload" | jq -r '.prompt // empty' 2>/dev/null)"

[ -n "$prompt" ] || exit 0
[ -x "$MEMSUB_BIN" ] || exit 0
[ -f "$DB" ] || exit 0

block="$(timeout "${BUDGET_SECS}s" "$MEMSUB_BIN" recall \
    --db "$DB" --namespace "$NAMESPACE" --query "$prompt" \
    --k "$K" --max-chars "$MAX_CHARS" 2>/dev/null)"

[ -n "$block" ] || exit 0

jq -n --arg ctx "$block" \
    '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}'
exit 0
