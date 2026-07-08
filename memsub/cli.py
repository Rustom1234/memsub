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
        store.append(c.to_episode(run_id=args.run_id, session_id=session_id, namespace=args.namespace))
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
        store, backend=backend, config=cfg, run_id=args.run_id,
        namespace=args.namespace, dry_run=args.dry_run,
    )
    for a in actions:
        extra = f" by={a.detail['by']}" if "by" in a.detail else ""
        print(f"{a.action:>9} [{a.reason}] {a.episode_id}{extra}")
    label = "proposed (dry run)" if args.dry_run else "applied"
    remaining = len(store.active(run_id=args.run_id, namespace=args.namespace))
    print(f"{len(actions)} action(s) {label}; {remaining} active memories remain")
    store.close()
    return 0


def _cmd_ns(args: argparse.Namespace) -> int:
    from .namespaces import NamespaceManager

    store = EpisodicStore(Path(args.db))
    mgr = NamespaceManager(store)
    try:
        if args.ns_command == "create":
            mgr.create(args.name, owner=args.owner)
            print(f"namespace {args.name} created, owner {args.owner}")
        elif args.ns_command == "grant":
            mgr.grant(args.name, args.agent, read=not args.no_read, write=args.write)
            mode = ("read" if not args.no_read else "") + ("+write" if args.write else "")
            print(f"granted {mode or 'nothing'} on {args.name} to {args.agent}")
        elif args.ns_command == "revoke":
            mgr.revoke(args.name, args.agent)
            print(f"revoked {args.agent} from {args.name}")
        elif args.ns_command == "list":
            for name in mgr.list():
                gs = ", ".join(
                    f"{g.agent}({'r' if g.can_read else ''}{'w' if g.can_write else ''})"
                    for g in mgr.grants(name)
                )
                print(f"{name}  owner={mgr.owner(name)}  grants: {gs or '(none)'}")
            if not mgr.list():
                print("no registered namespaces (unregistered namespaces are open)")
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    finally:
        store.close()
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    from .audit import audit_episode, recent_activity, render_audit, render_recent

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    store = EpisodicStore(db)
    try:
        if args.recent or args.memory_id is None:
            print(render_recent(recent_activity(store, limit=args.limit)))
            return 0
        record = audit_episode(store, args.memory_id)
        if record is None:
            # allow unambiguous id prefixes for convenience
            matches = [ep for ep in store.all() if ep.id.startswith(args.memory_id)]
            if len(matches) == 1:
                record = audit_episode(store, matches[0].id)
        if record is None:
            print(f"no memory with id {args.memory_id}")
            return 1
        print(render_audit(record))
        return 0
    finally:
        store.close()


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
    p_ext.add_argument("--namespace", default="default", help="namespace to file memories under")
    p_ext.add_argument("--run-id", default="default", help="run id to file memories under")
    p_ext.add_argument("--session-id", default=None, help="session id (default: file stem)")
    p_ext.add_argument("--max-chars", type=int, default=12000, help="chunk size in characters")
    p_ext.add_argument("--dry-run", action="store_true", help="print candidates, write nothing")
    p_ext.set_defaults(func=_cmd_extract)

    p_con = sub.add_parser("consolidate", help="dedup, resolve contradictions, decay low-value memories")
    p_con.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_con.add_argument("--backend", default="auto", choices=["auto", "api", "cli", "mock"])
    p_con.add_argument("--model", default=None, help="model override for the backend")
    p_con.add_argument("--namespace", default=None, help="restrict to one namespace (default: each separately)")
    p_con.add_argument("--run-id", default=None, help="restrict to one run id")
    p_con.add_argument("--no-llm", action="store_true", help="skip the LLM contradiction pass")
    p_con.add_argument("--dedup-threshold", type=float, default=0.92)
    p_con.add_argument("--decay-threshold", type=float, default=0.2)
    p_con.add_argument("--half-life-days", type=float, default=30.0)
    p_con.add_argument("--min-age-days", type=float, default=14.0)
    p_con.add_argument("--dry-run", action="store_true", help="print actions, apply nothing")
    p_con.set_defaults(func=_cmd_consolidate)

    p_ns = sub.add_parser("ns", help="manage namespaces and per-agent permissions")
    p_ns.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    ns_sub = p_ns.add_subparsers(dest="ns_command", required=True)
    ns_create = ns_sub.add_parser("create", help="register a namespace (registered = closed by default)")
    ns_create.add_argument("name")
    ns_create.add_argument("--owner", required=True, help="owning agent (gets read+write)")
    ns_grant = ns_sub.add_parser("grant", help="grant an agent access to a registered namespace")
    ns_grant.add_argument("name")
    ns_grant.add_argument("agent")
    ns_grant.add_argument("--write", action="store_true", help="also grant write")
    ns_grant.add_argument("--no-read", action="store_true", help="grant write without read")
    ns_revoke = ns_sub.add_parser("revoke", help="remove an agent's grant")
    ns_revoke.add_argument("name")
    ns_revoke.add_argument("agent")
    ns_sub.add_parser("list", help="list registered namespaces and grants")
    p_ns.set_defaults(func=_cmd_ns)

    p_aud = sub.add_parser("audit", help="why a memory exists and every time it was used")
    p_aud.add_argument("memory_id", nargs="?", default=None, help="memory id (or unambiguous prefix)")
    p_aud.add_argument("--recent", action="store_true", help="list recent memory activity instead")
    p_aud.add_argument("--limit", type=int, default=20, help="events to show with --recent")
    p_aud.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_aud.set_defaults(func=_cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
