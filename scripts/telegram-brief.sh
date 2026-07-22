#!/usr/bin/env bash
# Morning brief -> Telegram, dormant until a bot token exists. Activates the
# moment /telegram:configure has stored credentials; silent no-op otherwise.
# Khoj-automations pattern: push beats pull (see research doc addendum 2).
set -u
CRED="${TELEGRAM_CRED:-$HOME/.claude/plugins/data/telegram-claude-plugins-official/config.json}"
[ -f "$CRED" ] || exit 0
TOKEN=$(jq -r '.bot_token // .token // empty' "$CRED" 2>/dev/null)
CHAT=$(jq -r '.chat_id // .default_chat_id // empty' "$CRED" 2>/dev/null)
[ -n "$TOKEN" ] && [ -n "$CHAT" ] || exit 0
MEMSUB="${MEMSUB_BIN:-/home/user/projects/memory-sublayer/.venv/bin/memsub}"
BODY="$("$MEMSUB" standup --namespace claude-code 2>/dev/null | head -c 3800)"
[ -n "$BODY" ] || exit 0
curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  -d chat_id="${CHAT}" --data-urlencode text="${BODY}" >/dev/null 2>&1 || true
