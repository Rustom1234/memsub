#!/usr/bin/env bash
# One-line install for memsub: your second brain, built continuously.
#
#   bash scripts/install.sh
#
# Portable and idempotent: no hardcoded paths (everything derives from
# $HOME and this script's own location), safe to run more than once (every
# step checks "already done" before doing anything). What it does:
#
#   1. Checks python3.10+ and a local ollama daemon (warns, does not hard
#      fail, if either is missing -- see "Degraded modes" below).
#   2. Creates a virtualenv in this repo and `pip install -e .` into it.
#   3. Initializes the memory store (~/.memsub/memory.db by default) and
#      the vault directory (~/memsub-vault by default).
#   4. Installs the extraction/consolidation/vault-sync cron jobs and the
#      Claude Code recall hook, idempotently (safe to re-run).
#   5. Prints the "voila" next steps: how to start the UI and what URLs to
#      open.
#
# Overridable via env var (all optional):
#   MEMSUB_DB          SQLite store path       (default: $HOME/.memsub/memory.db)
#   MEMSUB_VAULT_DIR    vault folder            (default: $HOME/memsub-vault)
#   MEMSUB_NAMESPACE    extraction namespace    (default: claude-code)
#   CLAUDE_SETTINGS     Claude Code settings.json (default: $HOME/.claude/settings.json)
#   CRONTAB_CMD          crontab binary/wrapper  (default: crontab -- override for tests)
#
# Ported and generalized from ~/scripts/install-durable-capture.sh (a
# personal, machine-specific installer that also wires Rustom's separate
# transcript-capture layer, which lives outside this repo and is not part
# of this product install). Only the pieces that are genuinely part of
# memory-sublayer are ported here: the extraction/consolidate/vault-sync
# cron job (scripts/daily_extract.sh), the vault-apply cron job, the repo
# rollback watchdog, and the UserPromptSubmit recall hook
# (scripts/memsub-recall-hook.sh) -- all in-repo, all portable.
#
# Degraded modes (this script never hard-fails on a missing optional dep):
#   - python3 < 3.10, or missing entirely: aborts (nothing else can work).
#   - no ollama: venv/store/vault/cron/hooks are still installed. Every
#     LLM-backed feature (extract, consolidate, ask, resonance bridges)
#     will simply report "ollama not reachable" until you install it and
#     re-run this script (or nothing -- ollama coming online later needs
#     no re-install, the code checks at call time, not install time).

set -euo pipefail

# --------------------------------------------------------------------------- #
# 0. Paths (all derived, nothing hardcoded)
# --------------------------------------------------------------------------- #
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
VENV_PY="$VENV_DIR/bin/python"
VENV_MEMSUB="$VENV_DIR/bin/memsub"

MEMSUB_DB="${MEMSUB_DB:-$HOME/.memsub/memory.db}"
MEMSUB_VAULT_DIR="${MEMSUB_VAULT_DIR:-$HOME/memsub-vault}"
MEMSUB_NAMESPACE="${MEMSUB_NAMESPACE:-claude-code}"
CLAUDE_SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
CRONTAB_CMD="${CRONTAB_CMD:-crontab}"

log() { printf '%s\n' "$1"; }
step() { printf '\n== %s ==\n' "$1"; }

# --------------------------------------------------------------------------- #
# 1. Checks: python3.10+, ollama
# --------------------------------------------------------------------------- #
step "checking prerequisites"

if ! command -v python3 >/dev/null 2>&1; then
    log "ERROR: python3 not found on PATH. Install Python 3.10+ and re-run."
    exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    PY_VERSION="$(python3 --version 2>&1)"
    log "ERROR: memsub needs python3.10+, found: $PY_VERSION"
    log "Install a newer python3 and re-run this script."
    exit 1
fi
log "python3: $(python3 --version 2>&1) -- OK"

OLLAMA_OK=0
if command -v ollama >/dev/null 2>&1; then
    OLLAMA_OK=1
    log "ollama: found on PATH -- OK"
else
    log "ollama: NOT found on PATH."
    log "  memsub's extraction, consolidation, ask, and idea-resonance"
    log "  bridges all need a local LLM (default: qwen3:8b via ollama)."
    log "  Continuing without it -- everything else installs normally, and"
    log "  those features will report a clear \"ollama not reachable\""
    log "  message until you install ollama (https://ollama.com) and pull"
    log "  qwen3:8b (\`ollama pull qwen3:8b\`). No re-install needed"
    log "  afterward -- it's checked at call time, not install time."
fi

# --------------------------------------------------------------------------- #
# 2. venv + editable install
# --------------------------------------------------------------------------- #
step "python environment"

if [ -x "$VENV_PY" ]; then
    log "venv already exists at $VENV_DIR"
elif command -v uv >/dev/null 2>&1; then
    # Preferred when available: does not depend on the system's ensurepip/
    # python3-venv package being installed (stdlib `venv` fails hard without
    # it, a common minimal-machine/container gap; uv bundles its own).
    log "creating venv at $VENV_DIR (via uv)"
    uv venv "$VENV_DIR" >/dev/null
else
    log "creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

log "installing memsub (editable) into the venv"
if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV_PY" --quiet -e "$REPO_ROOT"
else
    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install --quiet -e "$REPO_ROOT"
fi
log "installed: $("$VENV_MEMSUB" --help >/dev/null 2>&1 && echo OK || echo FAILED)"

# --------------------------------------------------------------------------- #
# 3. store + vault init
# --------------------------------------------------------------------------- #
step "initializing store and vault"

mkdir -p "$(dirname "$MEMSUB_DB")"
MEMSUB_DB="$MEMSUB_DB" "$VENV_PY" -c "
from memsub.sdk import MemoryClient
c = MemoryClient()
c.close()
print('store ready:', c.store.path if hasattr(c, 'store') else '')
" || { log "ERROR: failed to initialize the store at $MEMSUB_DB"; exit 1; }
log "store: $MEMSUB_DB"

mkdir -p "$MEMSUB_VAULT_DIR"
log "vault: $MEMSUB_VAULT_DIR"

# --------------------------------------------------------------------------- #
# 4. cron jobs (idempotent)
# --------------------------------------------------------------------------- #
step "installing cron jobs"

add_cron() {
    local line="$1"
    if "$CRONTAB_CMD" -l 2>/dev/null | grep -qF "$line"; then
        log "cron already present: $line"
    else
        ("$CRONTAB_CMD" -l 2>/dev/null; echo "$line") | "$CRONTAB_CMD" -
        log "cron added: $line"
    fi
}

mkdir -p "$(dirname "$MEMSUB_DB")"
add_cron "*/30 * * * * MEMSUB_DB=\"$MEMSUB_DB\" MEMSUB_VAULT=\"$MEMSUB_VAULT_DIR\" MEMSUB_NAMESPACE=\"$MEMSUB_NAMESPACE\" $SCRIPT_DIR/daily_extract.sh"
add_cron "2-59/10 * * * * MEMSUB_DB=\"$MEMSUB_DB\" $VENV_MEMSUB vault --db \"$MEMSUB_DB\" apply --vault \"$MEMSUB_VAULT_DIR\" --run-id cron-apply >> $HOME/.memsub/vault-apply.log 2>&1"
add_cron "*/5 * * * * REPO_WATCHDOG_REPOS=\"$REPO_ROOT\" $SCRIPT_DIR/repo_watchdog.sh >> $HOME/.repo-watchdog/cron.log 2>&1"

# --------------------------------------------------------------------------- #
# 5. Claude Code recall hook (idempotent)
# --------------------------------------------------------------------------- #
step "installing the Claude Code recall hook"

if ! command -v jq >/dev/null 2>&1; then
    log "jq not found on PATH -- skipping the recall hook (install jq and re-run to add it)."
else
    mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
    if [ ! -f "$CLAUDE_SETTINGS" ]; then
        echo '{}' > "$CLAUDE_SETTINGS"
        log "created $CLAUDE_SETTINGS"
    fi

    RECALL_HOOK_CMD="MEMSUB_BIN=\"$VENV_MEMSUB\" MEMSUB_DB=\"$MEMSUB_DB\" bash \"$SCRIPT_DIR/memsub-recall-hook.sh\""

    if jq -e --arg cmd "$RECALL_HOOK_CMD" \
        '.hooks.UserPromptSubmit[]?.hooks[]? | select(.command == $cmd)' \
        "$CLAUDE_SETTINGS" >/dev/null 2>&1; then
        log "recall hook already present in $CLAUDE_SETTINGS"
    else
        cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.bak"
        jq --arg cmd "$RECALL_HOOK_CMD" '
            .hooks.UserPromptSubmit = ((.hooks.UserPromptSubmit // []) + [{hooks: [{type: "command", command: $cmd, timeout: 5}]}])
        ' "$CLAUDE_SETTINGS.bak" > "$CLAUDE_SETTINGS"
        log "recall hook added to $CLAUDE_SETTINGS (backup: $CLAUDE_SETTINGS.bak)"
    fi
fi

# --------------------------------------------------------------------------- #
# 6. Voila
# --------------------------------------------------------------------------- #
step "voila"

cat <<EOF

Your knowledge graph starts here. It keeps building itself from now on:
every 30 minutes it extracts new sessions, every 10 minutes it consolidates
(dedups, prunes stale memories, resolves contradictions) and syncs the
vault, and every prompt gets a silent recall check against what's already
there. Your AI will always be able to reach for a memory it would
otherwise have lost.

Start the UI:
  $VENV_MEMSUB vault --db "$MEMSUB_DB" serve --vault "$MEMSUB_VAULT_DIR" --port 8766

Then open:
  http://127.0.0.1:8766/           dashboard
  http://127.0.0.1:8766/ask        ask your brain, with citations
  http://127.0.0.1:8766/resonate   drop an idea, find what it connects to
  http://127.0.0.1:8766/graph      the knowledge graph, laid out by meaning

EOF
if [ "$OLLAMA_OK" -eq 0 ]; then
    log "Note: ollama is not installed yet -- ask/resonate/extract will say so until it is."
fi
