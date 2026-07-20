#!/usr/bin/env bash
# Rollback watchdog: detects a repo's HEAD landing somewhere that is not a
# descendant of the last-seen HEAD (a hard reset, a forced ref rewrite, or
# an external rollback of the git storage itself -- lab infra has silently
# rolled refs back on this project's repos before, twice). Local-only, no
# network, fast enough to run every few minutes via cron.
#
# For each watched repo: compare current HEAD against
# ~/.repo-watchdog/expected/<repo>.head.
#   - no expected file yet         -> seed it with current HEAD, no warning.
#   - current == expected          -> clean, no-op.
#   - expected is an ancestor of current (git merge-base --is-ancestor)
#                                   -> normal advance, expected file updated.
#   - anything else                -> ROLLBACK: loud warning block naming
#     the repo/expected/actual, one line appended to
#     ~/.repo-watchdog/incidents.log, and the expected file is left
#     UNTOUCHED -- overwriting it here would destroy the only evidence of
#     what HEAD was supposed to be.
#
# Also verifies the recovery-jul17-18 branch exists in each repo and that
# ~/repo-bundles holds at least one bundle, warning (not failing the whole
# run) if either is missing.
#
# Exit code: 0 if every repo was clean/advanced and both checks passed,
# 1 if anything above warned or was incident-logged (useful for a future
# cron-mail/alerting wrapper; this script itself never sends anything).

set -u

REPOS="${REPO_WATCHDOG_REPOS:-$HOME/projects/book-dna $HOME/projects/memory-sublayer}"
STATE_DIR="${REPO_WATCHDOG_STATE_DIR:-$HOME/.repo-watchdog}"
EXPECTED_DIR="$STATE_DIR/expected"
INCIDENTS="$STATE_DIR/incidents.log"
BUNDLE_DIR="${REPO_WATCHDOG_BUNDLE_DIR:-$HOME/repo-bundles}"
RECOVERY_REF="${REPO_WATCHDOG_RECOVERY_REF:-recovery-jul17-18}"

mkdir -p "$EXPECTED_DIR"

ISSUES=0

_now() { date '+%Y-%m-%dT%H:%M:%S'; }

_log_incident() {
    printf '%s %s\n' "$(_now)" "$1" >> "$INCIDENTS"
}

check_repo() {
    local repo="$1" name expected_file current expected
    name="$(basename "$repo")"
    expected_file="$EXPECTED_DIR/$name.head"

    if [ ! -d "$repo/.git" ]; then
        echo "SKIP $name: not a git repo ($repo)"
        return
    fi

    current="$(git -C "$repo" rev-parse HEAD 2>/dev/null)"
    if [ -z "$current" ]; then
        echo "SKIP $name: git rev-parse HEAD failed ($repo)"
        return
    fi

    if [ ! -f "$expected_file" ]; then
        echo "$current" > "$expected_file"
        echo "SEED $name: expected head initialized to $current"
    else
        expected="$(cat "$expected_file")"
        if [ "$current" = "$expected" ]; then
            echo "OK $name: HEAD unchanged ($current)"
        elif git -C "$repo" merge-base --is-ancestor "$expected" "$current" 2>/dev/null; then
            echo "$current" > "$expected_file"
            echo "ADVANCE $name: $expected -> $current"
        else
            ISSUES=1
            cat <<WARN

########################################################################
# ROLLBACK DETECTED: $name
# repo:     $repo
# expected: $expected
# actual:   $current
# expected HEAD is NOT an ancestor of the current HEAD -- this looks like
# a hard reset, a forced ref rewrite, or an external rollback of the git
# storage. The expected-head file was left untouched (preserving evidence,
# not overwritten by this run).
########################################################################

WARN
            _log_incident "ROLLBACK repo=$name path=$repo expected=$expected actual=$current"
        fi
    fi

    if git -C "$repo" show-ref --verify --quiet "refs/heads/$RECOVERY_REF"; then
        :
    else
        ISSUES=1
        echo "WARN $name: recovery branch '$RECOVERY_REF' not found"
        _log_incident "MISSING_RECOVERY_BRANCH repo=$name path=$repo ref=$RECOVERY_REF"
    fi
}

check_bundles() {
    local count
    count="$(find "$BUNDLE_DIR" -maxdepth 1 -name '*.bundle' 2>/dev/null | wc -l | tr -d ' ')"
    if [ "${count:-0}" -eq 0 ]; then
        ISSUES=1
        echo "WARN: no bundles found in $BUNDLE_DIR"
        _log_incident "MISSING_BUNDLES dir=$BUNDLE_DIR count=0"
    else
        echo "OK: $count bundle(s) in $BUNDLE_DIR"
    fi
}

for repo in $REPOS; do
    check_repo "$repo"
done
check_bundles

exit "$ISSUES"
