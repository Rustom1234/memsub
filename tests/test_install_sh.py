"""Sandboxed test for scripts/install.sh: HOME points at a throwaway temp
dir and `crontab` is replaced with a stub that logs to a file instead of
touching the real user crontab, so this test makes zero real system
changes. Runs against a temp copy of the repo (excluding .venv/.git/large
data dirs) so venv creation and `pip install -e .` never touch the real
checked-out repo's working .venv. See docs/install-2026-07-21.md for the
full manual verification (idempotency, no-ollama degraded mode, and a
byte-for-byte check that the real crontab/settings.json/.venv were
untouched).

Skips (does not fail) if the sandbox lacks what a fresh install needs
(network access for pip/uv to resolve dependencies, or python3 itself) --
this test verifies install.sh's own logic, not the availability of a
package index in whatever environment pytest happens to run in.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_FAKE_CRONTAB = """#!/usr/bin/env bash
# See tests/test_install_sh.py: logs to a state file instead of touching
# the real crontab. Writes via temp-file + mv (atomic rename) so a
# concurrent `-l` read (install.sh's `(crontab -l; echo new) | crontab -`
# pipeline starts both sides at once) never observes a truncated file.
set -e
STATE="${FAKE_CRONTAB_STATE:-/tmp/fake_crontab_state.txt}"
touch "$STATE"
if [ "$1" = "-l" ]; then
    cat "$STATE"
    exit 0
fi
if [ "$1" = "-" ]; then
    TMP="$(mktemp)"
    cat > "$TMP"
    mv "$TMP" "$STATE"
    exit 0
fi
exit 1
"""


def _network_available() -> bool:
    try:
        urllib.request.urlopen("https://pypi.org", timeout=3)
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    if not _network_available():
        pytest.skip("no network access to resolve dependencies for a fresh venv")

    root = tmp_path_factory.mktemp("memsub_install_sandbox")
    repo_copy = root / "repo"
    fake_home = root / "fakehome"
    fake_bin = root / "fakebin"
    fake_home.mkdir()
    fake_bin.mkdir()

    shutil.copytree(
        REPO_ROOT,
        repo_copy,
        ignore=shutil.ignore_patterns(
            ".venv", ".git", "__pycache__", ".pytest_cache", "memory_sublayer.egg-info",
            ".serena", "demo", "data", "results", ".beads",
        ),
    )

    crontab_path = fake_bin / "crontab"
    crontab_path.write_text(_FAKE_CRONTAB)
    crontab_path.chmod(crontab_path.stat().st_mode | stat.S_IEXEC)
    cron_state = root / "cron_state.txt"

    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["FAKE_CRONTAB_STATE"] = str(cron_state)

    return {"repo": repo_copy, "home": fake_home, "env": env, "cron_state": cron_state}


def _run_install(sandbox) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "scripts/install.sh"],
        cwd=sandbox["repo"],
        env=sandbox["env"],
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_fresh_install_creates_venv_store_vault_cron_and_hook(sandbox):
    result = _run_install(sandbox)
    assert result.returncode == 0, result.stdout + result.stderr

    assert (sandbox["repo"] / ".venv" / "bin" / "memsub").exists()
    assert (sandbox["home"] / ".memsub" / "memory.db").exists()
    assert (sandbox["home"] / "memsub-vault").is_dir()
    assert sandbox["cron_state"].exists()
    cron_lines = sandbox["cron_state"].read_text()
    assert "daily_extract.sh" in cron_lines
    assert "vault --db" in cron_lines and "apply" in cron_lines
    assert "repo_watchdog.sh" in cron_lines

    settings = (sandbox["home"] / ".claude" / "settings.json").read_text()
    assert "memsub-recall-hook.sh" in settings
    assert "voila" in result.stdout.lower() or "second brain" in result.stdout.lower()


def test_rerun_is_idempotent_no_new_cron_lines_or_hook_entries(sandbox):
    before = sandbox["cron_state"].read_text()
    result = _run_install(sandbox)
    assert result.returncode == 0, result.stdout + result.stderr
    after = sandbox["cron_state"].read_text()
    assert before == after
    assert "already present" in result.stdout
    assert "already exists" in result.stdout


def test_real_system_is_never_touched(sandbox):
    """The sandbox's own throwaway path must never appear anywhere in the
    real crontab or the real Claude Code settings.json -- if it does, the
    fake overrides leaked and this test setup itself is broken."""
    _run_install(sandbox)
    real_settings = Path.home() / ".claude" / "settings.json"
    if real_settings.exists():
        assert str(sandbox["repo"]) not in real_settings.read_text()

    real_crontab = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True
    )
    assert str(sandbox["repo"]) not in (real_crontab.stdout or "")
