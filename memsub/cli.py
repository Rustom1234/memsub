"""memsub command line interface.

Commands:
  memsub extract <file>     extract candidate memories from a transcript

The default store is a single SQLite file at $MEMSUB_DB or ~/.memsub/memory.db.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .backends import build_backend
from .extraction import extract_file
from .store import EpisodicStore


def default_db_path() -> Path:
    env = os.environ.get("MEMSUB_DB")
    if env:
        return Path(env)
    return Path.home() / ".memsub" / "memory.db"


def _cmd_extract(args: argparse.Namespace) -> int:
    backend = build_backend(prefer=args.backend, model=args.model)
    candidates = extract_file(args.file, backend, max_chars=args.max_chars)
    if not candidates:
        print("no candidate memories extracted")
        return 0
    for c in candidates:
        print(f"[{c.kind:>10}] conf={c.confidence:.2f}  {c.content}")
    if args.dry_run:
        print(f"\n{len(candidates)} candidate(s), dry run: nothing written")
        return 0
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = EpisodicStore(db)
    session_id = args.session_id or Path(args.file).stem
    for c in candidates:
        store.append(c.to_episode(run_id=args.run_id, session_id=session_id))
    store.close()
    print(f"\n{len(candidates)} memories written to {db}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memsub", description="local-first memory for agents")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ext = sub.add_parser("extract", help="extract candidate memories from a transcript file")
    p_ext.add_argument("file", help="transcript file (.jsonl or plain text)")
    p_ext.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_ext.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "api", "cli", "mock"],
        help="LLM backend (auto: API if ANTHROPIC_API_KEY else claude CLI)",
    )
    p_ext.add_argument("--model", default=None, help="model override for the backend")
    p_ext.add_argument("--run-id", default="default", help="run id to file memories under")
    p_ext.add_argument("--session-id", default=None, help="session id (default: file stem)")
    p_ext.add_argument("--max-chars", type=int, default=12000, help="chunk size in characters")
    p_ext.add_argument("--dry-run", action="store_true", help="print candidates, write nothing")
    p_ext.set_defaults(func=_cmd_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
