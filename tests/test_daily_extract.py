"""scripts/daily_extract.sh: lock and ollama-down failure paths.

These are the two cheaply-testable behaviors: the script must not run two
overlapping extractions, and it must exit cleanly (nonzero, logged reason)
rather than silently falling back to a cloud API when ollama is down. Both
tests run the real script as a subprocess against a throwaway HOME so
nothing touches the real store/vault/log.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "daily_extract.sh"


def _env(tmp_path: Path, **overrides: str) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "MEMSUB_DB": str(tmp_path / "memory.db"),
            "MEMSUB_VAULT": str(tmp_path / "vault"),
            "MEMSUB_TRANSCRIPTS_DIR": str(tmp_path / "transcripts"),
            "MEMSUB_DAILY_LOCK": str(tmp_path / "daily.lock"),
            "MEMSUB_DAILY_LOG": str(tmp_path / "daily.log"),
            # unreachable by construction: nothing listens on port 1
            "OLLAMA_HOST": "http://127.0.0.1:1",
        }
    )
    env.update(overrides)
    return env


def test_ollama_down_exits_nonzero_with_logged_reason(tmp_path):
    (tmp_path / "transcripts").mkdir()
    env = _env(tmp_path)

    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode == 1
    log = (tmp_path / "daily.log").read_text()
    assert "ABORT" in log
    assert "ollama not reachable" in log


def test_concurrent_run_is_skipped_not_run_twice(tmp_path):
    (tmp_path / "transcripts").mkdir()
    env = _env(tmp_path)
    lock_file = tmp_path / "daily.lock"
    lock_file.touch()

    # Hold the lock the way the script itself would (fd 9, flock), then run
    # the script and confirm it backs off instead of racing the holder.
    holder = subprocess.Popen(
        ["bash", "-c", f'exec 9>"{lock_file}"; flock 9; sleep 5'],
    )
    try:
        import time

        time.sleep(0.3)  # let the holder actually acquire the lock
        result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
        assert result.returncode == 0
        log = (tmp_path / "daily.log").read_text()
        assert "SKIP" in log
        assert "already in progress" in log
    finally:
        holder.terminate()
        holder.wait()


def test_no_transcripts_found_exits_nonzero(tmp_path):
    """Ollama check happens before the file scan, so this needs a reachable
    daemon; skip if this machine doesn't have one running."""
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=0.5)
    except Exception:
        import pytest

        pytest.skip("no local ollama daemon reachable")

    (tmp_path / "transcripts").mkdir()  # empty
    env = _env(tmp_path, OLLAMA_HOST="http://localhost:11434")

    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)

    assert result.returncode == 1
    log = (tmp_path / "daily.log").read_text()
    assert "ABORT" in log
    assert "no transcript files found" in log
