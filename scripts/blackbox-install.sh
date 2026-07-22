#!/usr/bin/env bash
# Blackbox: the flight recorder for AI coding agents. Standalone one-command
# install, then a first-run scan that finds work your agents silently lost.
#
# Usage (from a clone of this repo):
#   bash scripts/blackbox-install.sh
#
# Or point it at a package spec (a path, a git URL, or a PyPI name once
# published):
#   bash scripts/blackbox-install.sh /path/to/memory-sublayer
#
# What it does, in order:
#   1. Finds an isolated-tool installer: uv (preferred), else pipx, else a
#      pip --user venv. Installs uv via the official script only if none of
#      the three exist (opt-in prompt, skipped when BLACKBOX_ASSUME_YES=1).
#   2. Installs the package so `blackbox` is on your PATH, isolated from your
#      system Python (no repo, no venv activation, no config needed after).
#   3. Runs `blackbox scan` immediately: within a minute you either see
#      "CLEAN" or a list of files your agents lost, with restore commands.
#
# It never touches your code, never writes outside the tool's own install
# dir and ~/.local/state (the scan's index), and never sends anything over
# the network beyond fetching the installer/package itself.
set -euo pipefail

SPEC="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ASSUME_YES="${BLACKBOX_ASSUME_YES:-0}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }

install_with_uv() {
    say "Installing blackbox with uv (isolated tool)..."
    uv tool install --force "$SPEC"
}

install_with_pipx() {
    say "Installing blackbox with pipx (isolated tool)..."
    pipx install --force "$SPEC"
}

install_with_pip_user() {
    say "Installing blackbox into a pip --user venv (fallback)..."
    local venv="$HOME/.local/share/blackbox-venv"
    python3 -m venv "$venv"
    "$venv/bin/pip" install --quiet --upgrade pip
    "$venv/bin/pip" install --quiet "$SPEC"
    mkdir -p "$HOME/.local/bin"
    ln -sf "$venv/bin/blackbox" "$HOME/.local/bin/blackbox"
    info "Symlinked $HOME/.local/bin/blackbox (ensure ~/.local/bin is on your PATH)."
}

maybe_bootstrap_uv() {
    if [ "$ASSUME_YES" = "1" ]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # shellcheck disable=SC1090
        export PATH="$HOME/.local/bin:$PATH"
        return 0
    fi
    printf 'No isolated-tool installer found (uv/pipx). Install uv now? [y/N] '
    read -r reply
    case "$reply" in
        y|Y)
            curl -LsSf https://astral.sh/uv/install.sh | sh
            export PATH="$HOME/.local/bin:$PATH"
            ;;
        *)
            return 1
            ;;
    esac
}

command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 1; }

if command -v uv >/dev/null 2>&1; then
    install_with_uv
elif command -v pipx >/dev/null 2>&1; then
    install_with_pipx
elif maybe_bootstrap_uv && command -v uv >/dev/null 2>&1; then
    install_with_uv
else
    install_with_pip_user
fi

echo
if ! command -v blackbox >/dev/null 2>&1; then
    say "Installed, but 'blackbox' is not on your PATH yet."
    info "Add the tool bin dir to PATH (uv: 'uv tool update-shell', pipx: 'pipx ensurepath'),"
    info "open a new shell, then run: blackbox scan"
    exit 0
fi

say "Installed. Running your first scan (this is the whole pitch)..."
echo
blackbox scan || true
echo
say "That is the flight recorder. From now on:"
info "blackbox scan               recheck for silently lost work"
info "blackbox restore <file>     write a lost file back to disk"
info "blackbox status             index stats and last sweep"
