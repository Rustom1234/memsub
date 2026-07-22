"""Codex CLI transcript parser (best-effort, schema UNVERIFIED).

Codex CLI keeps session rollouts at $CODEX_HOME/sessions/**/rollout-*.jsonl
(default CODEX_HOME: ~/.codex). This machine has neither ~/.codex nor the
Codex CLI installed (checked while building this: no such directory
exists), so this parser was built from Codex's own apply_patch text
convention -- the "*** Begin Patch" / "*** Add File:" / "*** Update File:"
/ "*** End Patch" block its apply_patch tool emits to describe file
changes -- rather than a captured real transcript. The wrapper shape (one
JSON object per line, a "timestamp" field, the patch text living somewhere
inside the record) is this parser's own guess at the smallest reasonable
envelope; the patch grammar itself is Codex's long-stable, documented
convention. Confidence: low on the envelope, medium on the patch grammar
once found.

Streamed line by line (never loads a whole file: rollout files can reach
hundreds of MB) with a byte-offset checkpoint per file, same incremental
contract as memsub.filehist.index_transcripts. Never raises on malformed
input: a bad line is skipped, not fatal.

Extraction fidelity, stated plainly:
- "Add File" patches carry full new content: indexed as a 'write' (exact).
- "Update File" patches carry only a diff: indexed as a coarse 'edit' (all
  removed lines in one hunk joined into old_string, all added lines joined
  into new_string). A hunk with more than one separate change loses the
  ordering between those changes, so it may not replay byte-exactly through
  filehist.reconstruct. Good enough to know a file was touched and roughly
  when; not guaranteed byte-exact the way the Aider or Claude Code parsers
  are.
- "Delete File" patches are not indexed: there is no content to restore to.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .. import filehist

DEFAULT_CODEX_HOME = Path.home() / ".codex"

_PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _find_patch_text(rec: dict, depth: int = 0) -> str | None:
    """Best-effort: hunt a few likely field names, one level of nesting
    ("payload" wrapping the real event), for a string containing a patch."""
    if depth > 2:
        return None
    for key in ("text", "arguments", "content", "patch"):
        val = rec.get(key)
        if isinstance(val, str) and "*** Begin Patch" in val:
            return val
        if isinstance(val, dict):
            nested = _find_patch_text(val, depth + 1)
            if nested:
                return nested
    payload = rec.get("payload")
    if isinstance(payload, dict):
        return _find_patch_text(payload, depth + 1)
    return None


def _iter_patch_files(patch_text: str):
    """Yield (action, path, content, old_string, new_string) per file
    section of one apply_patch block. content is set only for Add File
    (full new content); old_string/new_string only for Update File."""
    lines = patch_text.split("\n")
    i = 0
    while i < len(lines):
        m = _PATCH_FILE.match(lines[i])
        if not m:
            i += 1
            continue
        action, path = m.group(1), m.group(2).strip()
        i += 1
        added: list[str] = []
        removed: list[str] = []
        while i < len(lines) and not lines[i].startswith("*** "):
            ln = lines[i]
            if ln.startswith("+") and not ln.startswith("+++"):
                added.append(ln[1:])
            elif ln.startswith("-") and not ln.startswith("---"):
                removed.append(ln[1:])
            i += 1
        if action == "Add":
            content = "\n".join(added) + ("\n" if added else "")
            yield (action, path, content, None, None)
        elif action == "Update":
            yield (action, path, None, "\n".join(removed), "\n".join(added))
        # Delete: nothing to recover, skipped


def index_sessions(store, codex_home: str | Path | None = None) -> int:
    """Index $CODEX_HOME/sessions/**/rollout-*.jsonl. Returns rows added.
    Silent no-op if the directory doesn't exist (Codex CLI not present)."""
    home = Path(codex_home) if codex_home else Path(
        os.environ.get("CODEX_HOME", str(DEFAULT_CODEX_HOME))
    )
    sessions_dir = home / "sessions"
    if not sessions_dir.is_dir():
        return 0

    conn = filehist._conn(store)
    added = 0
    for tpath in sorted(sessions_dir.rglob("rollout-*.jsonl")):
        tkey = f"codex:{tpath}"
        row = conn.execute(
            "SELECT byte_offset FROM filehist_checkpoints WHERE transcript = ?", (tkey,)
        ).fetchone()
        offset = row[0] if row else 0
        size = tpath.stat().st_size
        if size <= offset:
            continue
        with open(tpath, encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                ts = filehist._parse_ts(str(rec.get("timestamp", "")))
                patch_text = _find_patch_text(rec)
                if not patch_text:
                    continue
                for _action, path, content, old, new in _iter_patch_files(patch_text):
                    if content is not None:
                        body = content[: filehist.MAX_CONTENT_BYTES]
                        conn.execute(
                            "INSERT INTO filestates (path, ts, transcript, kind, content_hash, content)"
                            " VALUES (?, ?, ?, 'write', ?, ?)",
                            (path, ts, tkey, _sha(body), body),
                        )
                        added += 1
                    elif old is not None:
                        conn.execute(
                            "INSERT INTO filestates (path, ts, transcript, kind, old_string, new_string, replace_all)"
                            " VALUES (?, ?, ?, 'edit', ?, ?, 0)",
                            (path, ts, tkey, old, new),
                        )
                        added += 1
        conn.execute(
            "INSERT INTO filehist_checkpoints (transcript, byte_offset) VALUES (?, ?)"
            " ON CONFLICT(transcript) DO UPDATE SET byte_offset = excluded.byte_offset",
            (tkey, size),
        )
    conn.commit()
    return added
