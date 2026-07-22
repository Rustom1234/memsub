#!/usr/bin/env bash
# Flight-recorder background sweep (every 30 min via cron): incrementally
# index new transcript file-states, then check watched repos for disk
# content that has rolled back vs transcript-known truth. Incidents append
# to a log AND drop a note into the memsub inbox so the capture layer
# carries them into the vault (they then surface in review/brief).
set -u
MEMSUB_BIN="${MEMSUB_BIN:-/home/user/projects/memory-sublayer/.venv/bin/memsub}"
REPOS="${FLIGHT_RECORDER_REPOS:-$HOME/projects/memory-sublayer $HOME/projects/book-dna}"
LOG="${FLIGHT_RECORDER_LOG:-$HOME/.memsub/flight-incidents.log}"
INBOX="${MEMSUB_INBOX:-$HOME/.memsub/inbox}"
[ -x "$MEMSUB_BIN" ] || exit 0
"$MEMSUB_BIN" filehist index >/dev/null 2>&1 || true
status=0
for repo in $REPOS; do
    [ -d "$repo" ] || continue
    out="$("$MEMSUB_BIN" filehist check --repo "$repo" 2>/dev/null)" || {
        status=1
        printf '%s ROLLBACK DETECTED in %s\n%s\n' "$(date -Is)" "$repo" "$out" >> "$LOG"
        if [ -d "$INBOX" ]; then
            printf 'Flight recorder: disk rollback detected in %s at %s. Run `memsub filehist check --repo %s` for recovery commands.\n' \
                "$repo" "$(date -Is)" "$repo" > "$INBOX/flight-rollback-$(date +%s).txt"
        fi
    }
done
exit $status
