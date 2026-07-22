"""Aider parser: git commits authored "(aider)".

Aider's own commits carry an author name that includes the literal
substring "(aider)" (its default git identity convention). That makes git
history itself the structured source of truth: no chat-log markdown to
parse, no schema to guess at. For each matching commit this reads every
changed file's exact content at that commit via `git show <hash>:<path>`,
so every state indexed here is byte-exact, the highest-confidence of the
three new parsers.

Idempotent via memsub.filehist's own filehist_checkpoints table, keyed by
"aider:<repo>:<commit hash>": a commit already indexed is never re-read.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .. import filehist


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def index_commits(store, repo: str | Path) -> int:
    """Index every "(aider)"-authored commit in repo. Returns rows added.
    Silent no-op if repo isn't a git repo or has no aider-authored history."""
    repo = Path(repo).resolve()
    if not (repo / ".git").exists():
        return 0
    log = _git(repo, "log", "--all", "--format=%H\x1f%aI\x1f%an")
    if log.returncode != 0:
        return 0

    conn = filehist._conn(store)
    added = 0
    for line in log.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        commit_hash, iso_date, author = parts
        if "(aider)" not in author:
            continue
        tkey = f"aider:{repo}:{commit_hash}"
        already = conn.execute(
            "SELECT 1 FROM filehist_checkpoints WHERE transcript = ?", (tkey,)
        ).fetchone()
        if already:
            continue
        ts = filehist._parse_ts(iso_date)
        names = _git(repo, "show", "--name-only", "--pretty=format:", commit_hash)
        for fname in names.stdout.splitlines():
            fname = fname.strip()
            if not fname:
                continue
            blob = _git(repo, "show", f"{commit_hash}:{fname}")
            if blob.returncode != 0:
                continue  # file deleted in this commit: nothing to restore to
            content = blob.stdout[: filehist.MAX_CONTENT_BYTES]
            abspath = str((repo / fname).resolve())
            conn.execute(
                "INSERT INTO filestates (path, ts, transcript, kind, content_hash, content)"
                " VALUES (?, ?, ?, 'write', ?, ?)",
                (abspath, ts, tkey, _sha(content), content),
            )
            added += 1
        conn.execute(
            "INSERT INTO filehist_checkpoints (transcript, byte_offset) VALUES (?, 1)"
            " ON CONFLICT(transcript) DO UPDATE SET byte_offset = 1",
            (tkey,),
        )
    conn.commit()
    return added
