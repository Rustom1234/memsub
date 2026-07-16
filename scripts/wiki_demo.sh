#!/usr/bin/env bash
# Wiki mode demo: the "LLM wiki that doesn't rot" loop, start to finish, against
# throwaway /tmp dirs (never the live store ~/.memsub/memory.db or the live
# vault ~/memsub-vault). Safe to run repeatedly: it wipes and rebuilds its own
# demo dir each time.
#
# What it shows:
#   1. ingest a small agent-written wiki (5 pages, two of them contradict)
#   2. re-ingest unchanged  -> no-op (checkpointed)
#   3. edit a page + re-ingest -> the page's stale claim is superseded
#   4. consolidate --stage  -> cross-page contradictions become pending
#      proposals in the vault, the store is NOT touched
#   5. accept a proposal + vault apply -> the accepted supersede executes
#
# Needs a working LLM backend for extraction and contradiction judging. Uses
# local ollama by default (see docs/wiki-mode.md for the honest limits on
# extraction quality). Override with BACKEND=/MODEL= env vars.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MEMSUB="${MEMSUB_BIN:-$ROOT/.venv/bin/python -m memsub.cli}"
BACKEND="${BACKEND:-ollama}"
MODEL="${MODEL:-qwen3:8b}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

DEMO="${WIKI_DEMO_DIR:-/tmp/memsub-wiki-demo}"
PAGES="$DEMO/wiki"
DB="$DEMO/mem.db"
VAULT="$DEMO/vault"

hr()  { printf '\n=== %s ===\n' "$1"; }
run() { printf '$ %s\n' "$*"; "$@"; }

if [ "$BACKEND" = "ollama" ] && ! curl -s --max-time 3 "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
    echo "ABORT: ollama not reachable at $OLLAMA_HOST (set BACKEND=mock will not extract claims)."
    echo "Start ollama, or point BACKEND/MODEL at another backend."
    exit 1
fi

rm -rf "$DEMO"
mkdir -p "$PAGES"

hr "1. an agent writes a wiki (5 pages, two pairs contradict)"
cat >"$PAGES/db-arch.md"  <<'EOF'
# Architecture

The production database is PostgreSQL.
EOF
cat >"$PAGES/db-ops.md"   <<'EOF'
# Operations runbook

The production database is SQLite.
EOF
cat >"$PAGES/auth-a.md"   <<'EOF'
# Authentication

The API authentication uses OAuth2.
EOF
cat >"$PAGES/auth-b.md"   <<'EOF'
# Auth notes

The API authentication uses API keys.
EOF
cat >"$PAGES/deploy.md"   <<'EOF'
# Deploy schedule

Deploys run on Fridays.
EOF
ls -1 "$PAGES"

hr "2. ingest the wiki -> claims"
run $MEMSUB wiki ingest --db "$DB" --pages "$PAGES" --backend "$BACKEND" --model "$MODEL"

hr "3. re-ingest unchanged -> no-op (checkpointed)"
run $MEMSUB wiki ingest --db "$DB" --pages "$PAGES" --backend "$BACKEND" --model "$MODEL"

hr "4. an agent edits a page; re-ingest supersedes its stale claim"
cat >"$PAGES/deploy.md" <<'EOF'
# Deploy schedule

Deploys run on Mondays.
EOF
run $MEMSUB wiki ingest --db "$DB" --pages "$PAGES" --backend "$BACKEND" --model "$MODEL"

hr "5. consolidate --stage: contradictions become pending proposals (store untouched)"
run $MEMSUB consolidate --db "$DB" --backend "$BACKEND" --model "$MODEL" \
    --stage --vault "$VAULT"

STAGED=$(find "$VAULT/staging" -name '*.md' 2>/dev/null | sort || true)
if [ -z "$STAGED" ]; then
    echo "No proposals were staged. The backend judged no contradiction this run"
    echo "(local models are inconsistent judges; see docs/wiki-mode.md). Stopping."
    exit 0
fi
echo "Staged proposals:"
for f in $STAGED; do echo "  $f"; done
echo
echo "--- first proposal note ---"
FIRST=$(echo "$STAGED" | head -1)
cat "$FIRST"

hr "6. the human accepts a proposal, then vault apply executes it"
# Set verdict: accept on the first proposal (a human would do this in Obsidian).
sed -i 's/^verdict: ""/verdict: "accept"/' "$FIRST"
run $MEMSUB vault --db "$DB" apply --vault "$VAULT"

hr "done"
echo "Demo store:  $DB"
echo "Demo vault:  $VAULT   (staging/ holds the resolved proposal notes)"
echo "The live store (~/.memsub/memory.db) and vault (~/memsub-vault) were never touched."
