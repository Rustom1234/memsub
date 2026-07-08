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
from .consolidate import ConsolidationConfig, consolidate
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


def _cmd_consolidate(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    backend = None if args.no_llm else build_backend(prefer=args.backend, model=args.model)
    cfg = ConsolidationConfig(
        dedup_threshold=args.dedup_threshold,
        decay_half_life_days=args.half_life_days,
        decay_min_age_days=args.min_age_days,
        decay_value_threshold=args.decay_threshold,
    )
    store = EpisodicStore(db)
    actions = consolidate(
        store, backend=backend, config=cfg, run_id=args.run_id, dry_run=args.dry_run
    )
    for a in actions:
        extra = f" by={a.detail['by']}" if "by" in a.detail else ""
        print(f"{a.action:>9} [{a.reason}] {a.episode_id}{extra}")
    label = "proposed (dry run)" if args.dry_run else "applied"
    print(f"{len(actions)} action(s) {label}; {len(store.active(run_id=args.run_id))} active memories remain")
    store.close()
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

    p_con = sub.add_parser("consolidate", help="dedup, resolve contradictions, decay low-value memories")
    p_con.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_con.add_argument("--backend", default="auto", choices=["auto", "api", "cli", "mock"])
    p_con.add_argument("--model", default=None, help="model override for the backend")
    p_con.add_argument("--run-id", default=None, help="restrict to one run id")
    p_con.add_argument("--no-llm", action="store_true", help="skip the LLM contradiction pass")
    p_con.add_argument("--dedup-threshold", type=float, default=0.92)
    p_con.add_argument("--decay-threshold", type=float, default=0.2)
    p_con.add_argument("--half-life-days", type=float, default=30.0)
    p_con.add_argument("--min-age-days", type=float, default=14.0)
    p_con.add_argument("--dry-run", action="store_true", help="print actions, apply nothing")
    p_con.set_defaults(func=_cmd_consolidate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
