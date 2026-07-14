#!/usr/bin/env bash
# Daily incremental memory extraction: finds Claude Code transcripts, extracts
# new/changed ones into the live memsub store, consolidates, and syncs the
# Obsidian vault. Safe to run repeatedly: lock-protected against overlap and
# checkpointed (memsub's extraction_checkpoints table) so a rerun over an
# unchanged corpus does almost no work. See docs/daily-extract.md for the
# crontab line (not installed by this script).
#
# All paths are overridable by env var, mainly so tests can point this at a
# throwaway store/vault/log instead of the real ones.

set -euo pipefail

MEMSUB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MEMSUB_BIN="${MEMSUB_BIN:-$MEMSUB_ROOT/.venv/bin/memsub}"
DB="${MEMSUB_DB:-$HOME/.memsub/memory.db}"
VAULT="${MEMSUB_VAULT:-$HOME/memsub-vault}"
NAMESPACE="${MEMSUB_NAMESPACE:-claude-code}"
TRANSCRIPTS_DIR="${MEMSUB_TRANSCRIPTS_DIR:-$HOME/.claude/projects}"
LOCK_FILE="${MEMSUB_DAILY_LOCK:-$HOME/.memsub/daily_extract.lock}"
LOG_FILE="${MEMSUB_DAILY_LOG:-$HOME/.memsub/daily_extract.log}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

mkdir -p "$(dirname "$LOCK_FILE")" "$(dirname "$LOG_FILE")"

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >>"$LOG_FILE"
}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "SKIP: another run is already in progress (lock held on $LOCK_FILE)"
    exit 0
fi

log "START daily_extract"

# Local-only by design: this pipeline never falls back to a cloud API, so a
# down ollama daemon is a clean, logged no-op rather than a silent API bill.
if ! curl -s --max-time 3 "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
    log "ABORT: ollama not reachable at $OLLAMA_HOST"
    exit 1
fi

mapfile -t FILES < <(find "$TRANSCRIPTS_DIR" -name '*.jsonl' \
    -not -path '*subagents*' -not -path '*tool-results*' 2>/dev/null | sort)

if [ "${#FILES[@]}" -eq 0 ]; then
    log "ABORT: no transcript files found under $TRANSCRIPTS_DIR"
    exit 1
fi

log "extract: ${#FILES[@]} candidate transcript file(s) under $TRANSCRIPTS_DIR"
if ! EXTRACT_OUT=$("$MEMSUB_BIN" extract "${FILES[@]}" \
    --db "$DB" --namespace "$NAMESPACE" --backend ollama \
    --min-turns 3 --run-id "daily-$(date '+%Y%m%d')" 2>&1); then
    log "FAIL: extract step failed"
    printf '%s\n' "$EXTRACT_OUT" >>"$LOG_FILE"
    exit 1
fi
log "extract: $(printf '%s' "$EXTRACT_OUT" | tail -1)"

if ! CONSOLIDATE_OUT=$("$MEMSUB_BIN" consolidate \
    --db "$DB" --namespace "$NAMESPACE" --backend ollama 2>&1); then
    log "FAIL: consolidate step failed"
    printf '%s\n' "$CONSOLIDATE_OUT" >>"$LOG_FILE"
    exit 1
fi
log "consolidate: $(printf '%s' "$CONSOLIDATE_OUT" | tail -1)"

if ! VAULT_OUT=$("$MEMSUB_BIN" vault --db "$DB" sync --vault "$VAULT" 2>&1); then
    log "FAIL: vault sync step failed"
    printf '%s\n' "$VAULT_OUT" >>"$LOG_FILE"
    exit 1
fi
log "vault sync: $(printf '%s' "$VAULT_OUT" | tail -1)"

log "DONE daily_extract"
