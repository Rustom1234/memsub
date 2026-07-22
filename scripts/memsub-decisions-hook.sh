#!/usr/bin/env bash
# SessionStart preflight: inject the user's standing decisions relevant to
# this project so a new session starts knowing what has already been
# decided (companion to memsub-recall-hook.sh, which is per-prompt).
# Fails silently; never blocks a session.
set -u
MEMSUB_BIN="${MEMSUB_BIN:-/home/user/projects/memory-sublayer/.venv/bin/memsub}"
DB="${MEMSUB_DB:-$HOME/.memsub/memory.db}"
NAMESPACE="${MEMSUB_RECALL_NAMESPACE:-claude-code}"
K="${MEMSUB_DECISIONS_K:-6}"
[ -x "$MEMSUB_BIN" ] || exit 0
[ -f "$DB" ] || exit 0
ctx="$(basename "$PWD")"
block="$(timeout 3s "$MEMSUB_BIN" decisions --db "$DB" --namespace "$NAMESPACE" \
    --context "$ctx" --k "$K" 2>/dev/null)"
[ -n "$block" ] && [ "$block" != "no standing decisions" ] || exit 0
jq -n --arg ctx "STANDING DECISIONS (from memsub; the user already decided these, do not re-litigate):
$block" '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
exit 0
