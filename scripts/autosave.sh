#!/usr/bin/env bash
# 10-minute durability rail (2026-07-22 overnight): commit any WIP, refresh
# an off-tree git bundle, keep PROGRESS ticker fresh. Survives cutoffs and
# NFS incidents; replaces push (no remote exists; creating one needs
# Rustom's explicit go-ahead per his global rules).
set -u
cd /home/user/projects/memory-sublayer || exit 0
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git status --porcelain)" ]; then
    git add -A >/dev/null 2>&1
    git commit -q -m "autosave: overnight WIP $(date +%H:%M)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" >/dev/null 2>&1 || true
fi
git bundle create ~/repo-bundles/memory-sublayer-overnight.bundle master >/dev/null 2>&1 || true
cp ~/repo-bundles/memory-sublayer-overnight.bundle /var/tmp/user-memsub/ 2>/dev/null || true
