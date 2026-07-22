#!/usr/bin/env bash
# Nightly vault-db snapshot: consistent sqlite .backup of the machine-local
# live db (single-writer architecture, docs/eng-review-2026-07-21.md I2) to
# the NFS home as disaster recovery, keeping the last 7. The event-log JSONL
# mirror is copied alongside. NFS is the BACKUP tier only; the live db never
# runs there again.
set -euo pipefail

LIVE_DB="${MEMSUB_LIVE_DB:-/var/tmp/user-memsub/memory.db}"
SNAP_DIR="${MEMSUB_SNAP_DIR:-$HOME/.memsub/snapshots}"
STAMP="$(date +%Y-%m-%d)"

mkdir -p "$SNAP_DIR"
sqlite3 "$LIVE_DB" ".backup '$SNAP_DIR/memory-$STAMP.db'"
[ -f "$LIVE_DB.events.jsonl" ] && cp "$LIVE_DB.events.jsonl" "$SNAP_DIR/events-$STAMP.jsonl"
ls -1t "$SNAP_DIR"/memory-*.db 2>/dev/null | tail -n +8 | xargs -r rm -f
ls -1t "$SNAP_DIR"/events-*.jsonl 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "$(date -Is) snapshot ok -> $SNAP_DIR/memory-$STAMP.db" >> "$SNAP_DIR/snapshot.log"
