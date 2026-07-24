#!/usr/bin/env bash
# 10-minute durability rail WITH ROLLBACK GUARD (hardened 2026-07-23).
#
# Why the guard: the NFS home has silently rolled memory-sublayer's git
# history back to an older state at least three times (Jul 18-20, Jul 22
# morning, Jul 23). The old autosave then committed the rolled-back working
# tree on top, making the bad state the branch tip and clobbering the good
# bundle. This version REFUSES to commit or overwrite bundles when it
# detects history went backward, and writes an alert, so a rollback can be
# recovered (git objects usually survive: git fsck --lost-found, or a
# ~/repo-bundles/*GOOD*.bundle) instead of being buried.
#
# Never pushes (no remote; creating one needs Rustom's explicit go-ahead).
set -u
REPO=/home/user/projects/memory-sublayer
STATE="$HOME/.memsub/autosave-last-good"
ALERT="$HOME/.memsub/ROLLBACK-ALERT.txt"
cd "$REPO" || exit 0
HEAD=$(git rev-parse HEAD 2>/dev/null) || exit 0

# Rollback guard: if the last recorded good commit is no longer an ancestor
# of HEAD, the history diverged/rolled back. Do NOT commit, do NOT bundle
# (that would overwrite a good bundle with the bad state). Alert and stop.
if [ -f "$STATE" ]; then
    LAST=$(cat "$STATE" 2>/dev/null)
    if [ -n "$LAST" ] && git cat-file -e "$LAST" 2>/dev/null \
       && ! git merge-base --is-ancestor "$LAST" HEAD 2>/dev/null; then
        echo "$(date '+%F %T'): ROLLBACK DETECTED. HEAD=$HEAD no longer contains last-good=$LAST. autosave refused to commit or bundle. Recover with: git fsck --lost-found (find the good tip), git reset --hard <tip>; or fetch ~/repo-bundles/memory-sublayer-GOOD-*.bundle. NFS home rollback recurrence: an off-NFS backup is needed." >> "$ALERT"
        exit 0
    fi
fi

if [ -n "$(git status --porcelain)" ]; then
    git add -A >/dev/null 2>&1
    git commit -q -m "autosave: WIP $(date +%H:%M)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" >/dev/null 2>&1 || true
    HEAD=$(git rev-parse HEAD 2>/dev/null)
fi

git bundle create ~/repo-bundles/memory-sublayer-overnight.bundle master >/dev/null 2>&1 || true
cp ~/repo-bundles/memory-sublayer-overnight.bundle /var/tmp/user-memsub/ 2>/dev/null || true
cp ~/repo-bundles/memory-sublayer-overnight.bundle /tmp/memsub-latest.bundle 2>/dev/null || true
echo "$HEAD" > "$STATE"
