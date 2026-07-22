#!/usr/bin/env bash
# Arm the memsub governor: adds the PreToolUse hook to ~/.claude/settings.json
# (with a timestamped backup). Run BY THE USER, deliberately: the agent that
# built the governor was correctly blocked from installing its own leash.
# Takes effect in the NEXT Claude Code session. Disarm: restore the backup
# or delete the PreToolUse entry containing memsub-governor-hook.
set -euo pipefail
S="$HOME/.claude/settings.json"
cp "$S" "$S.bak-$(date +%s)"
jq '.hooks.PreToolUse = ((.hooks.PreToolUse // []) + (if ((.hooks.PreToolUse // []) | map(tostring) | any(contains("memsub-governor"))) then [] else [{"matcher":"Bash|Agent|Workflow","hooks":[{"type":"command","command":"/home/user/projects/memory-sublayer/scripts/memsub-governor-hook.sh","timeout":5}]}] end))' "$S" > "$S.tmp" && mv "$S.tmp" "$S"
echo "governor armed (next session). Rules: ~/.memsub/governor/rules.json  Ledger: ~/.memsub/governor/hits.jsonl"
