"""memsub command line interface.

Commands:
  memsub extract <file(s)>  extract candidate memories from one or more transcripts
  memsub vault sync         export the store to an Obsidian-compatible markdown vault
  memsub vault apply        read human edits/verdicts in the vault back into the store

The default store is a single SQLite file at $MEMSUB_DB or ~/.memsub/memory.db.
"""

from __future__ import annotations

import argparse
import glob as globmod
import os
import sys
from pathlib import Path

from .backends import build_backend
from .consolidate import ConsolidationConfig, consolidate
from .extraction import extract_file, file_content_hash, load_transcript
from .store import EpisodicStore


def default_db_path() -> Path:
    env = os.environ.get("MEMSUB_DB")
    if env:
        return Path(env)
    return Path.home() / ".memsub" / "memory.db"


def _expand_files(patterns: list[str]) -> list[str]:
    """Expand glob patterns to files, in sorted order, de-duplicated.

    Shells already expand unquoted globs, but a quoted pattern (needed to
    ingest more files than the shell's argv limit, or to keep the pattern
    itself in shell history) reaches us literally, so patterns containing
    glob metacharacters are expanded here too. Plain paths pass through
    unchanged.
    """
    files: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        matches = (
            sorted(globmod.glob(pattern, recursive=True))
            if any(c in pattern for c in "*?[")
            else [pattern]
        )
        for m in matches:
            if m in seen or not Path(m).is_file():
                continue
            seen.add(m)
            files.append(m)
    return files


def _real_turn_count(path: str) -> int:
    """Turns with an actual user/assistant speaker, the same filter the
    round-1 dogfood run used by hand to find substantive sessions among a
    corpus mostly made of single-shot automation transcripts."""
    return sum(1 for t in load_transcript(path) if t.role in ("user", "assistant"))


def _cmd_extract(args: argparse.Namespace) -> int:
    """Batch extract is resumable by default: each successfully processed file
    is checkpointed in the store keyed on (source path, content hash of the
    file), so rerunning the same command after a crash or timeout skips files
    already extracted and only processes the remainder. A changed file (hash
    differs) is re-extracted. --dry-run never checkpoints, matching its
    existing "nothing written" contract.
    """
    backend = build_backend(prefer=args.backend, model=args.model)
    files = _expand_files(args.files)
    if not files:
        print("no files matched")
        return 1

    store: EpisodicStore | None = None
    total_written = 0
    skipped_thin = 0
    skipped_checkpoint = 0
    for f in files:
        turns = _real_turn_count(f)
        if turns < args.min_turns:
            skipped_thin += 1
            print(f"{f}: skipped ({turns} real turn(s) < --min-turns {args.min_turns})")
            continue

        if store is None and not args.dry_run:
            db = Path(args.db)
            db.parent.mkdir(parents=True, exist_ok=True)
            store = EpisodicStore(db)

        content_hash = None
        if store is not None:
            content_hash = file_content_hash(f)
            if store.get_extraction_checkpoint(f) == content_hash:
                skipped_checkpoint += 1
                print(f"{f}: skipped (already extracted, unchanged since last checkpoint)")
                continue

        candidates = extract_file(f, backend, max_chars=args.max_chars)
        if not candidates:
            print(f"{f}: no candidate memories extracted")
            if store is not None:
                store.set_extraction_checkpoint(f, content_hash, 0)
            continue
        for c in candidates:
            print(f"[{c.kind:>10}] conf={c.confidence:.2f}  {c.content}")
        if args.dry_run:
            print(f"{f}: {len(candidates)} candidate(s), dry run: nothing written")
            continue
        session_id = args.session_id or Path(f).stem
        episodes = [
            c.to_episode(run_id=args.run_id, session_id=session_id, namespace=args.namespace)
            for c in candidates
        ]
        written = store.append_batch_with_checkpoint(episodes, f, content_hash)
        total_written += len(written)
        print(f"{f}: {len(written)} memories written")

    if store is not None:
        store.close()
    scanned = len(files) - skipped_thin
    print(
        f"\n{total_written} memor{'y' if total_written == 1 else 'ies'} written across "
        f"{scanned}/{len(files)} file(s) ({skipped_thin} skipped by --min-turns, "
        f"{skipped_checkpoint} skipped by checkpoint) to {args.db}"
    )
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


def _cmd_sleep(args: argparse.Namespace) -> int:
    from .sleep import sleep_store

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    backend = build_backend(prefer=args.backend, model=args.model)
    store = EpisodicStore(db)
    result = sleep_store(
        store, backend, run_id=args.run_id, namespace=args.namespace, dry_run=args.dry_run,
    )
    print(f"summaries: {len(result.summaries)}, inferences: {len(result.inferences)}, "
          f"links: {len(result.links)}")
    for ep in result.inferences:
        conf = ep.provenance.get("confidence", ep.importance)
        print(f"  [inference conf={conf:.2f}] {ep.content}")
    label = "proposed (dry run)" if args.dry_run else "written"
    print(f"{len(result.all_memories)} memor{'y' if len(result.all_memories) == 1 else 'ies'} {label}")
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


def _cmd_search(args: argparse.Namespace) -> int:
    from .sdk import MemoryClient

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    client = MemoryClient(
        db_path=db, namespace=args.namespace, retrieval=args.retrieval, embedder=args.embedder
    )
    try:
        hits = client.search(args.query, k=args.k, budget_tokens=args.budget)
        if not hits:
            print("no matching memories")
            return 0
        for h in hits:
            print(f"[{h.episode.id[:12]}] ({h.episode.kind}) {h.episode.content}")
        return 0
    finally:
        client.close()


def _cmd_vault_sync(args: argparse.Namespace) -> int:
    from .vault import sync_vault

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    store = EpisodicStore(db)
    try:
        result = sync_vault(store, args.vault, namespace=args.namespace)
    finally:
        store.close()
    print(
        f"{result.written} written, {result.updated} updated, "
        f"{result.unchanged} unchanged, {result.removed} removed "
        f"across {len(result.namespaces)} namespace(s) -> {args.vault}"
    )
    return 0


def _cmd_vault_apply(args: argparse.Namespace) -> int:
    from .vault import apply_vault

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    store = EpisodicStore(db)
    try:
        result = apply_vault(store, args.vault, namespace=args.namespace, run_id=args.run_id)
    finally:
        store.close()
    for line in result.detail:
        print(f"  {line}")
    print(
        f"{result.corrected} corrected, {result.rejected} rejected, "
        f"{result.confirmed} confirmed, {result.unchanged} unchanged, "
        f"{result.skipped} skipped <- {args.vault}"
    )
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    import time as _time

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    store = EpisodicStore(db)
    try:
        total = store.count()
        active_ids = {ep.id for ep in store.active()}
        by_namespace: dict[str, dict[str, int]] = {}
        for ep in store.all():
            row = by_namespace.setdefault(ep.namespace, {"total": 0, "active": 0})
            row["total"] += 1
            if ep.id in active_ids:
                row["active"] += 1

        print(f"episodes: {total} total, {len(active_ids)} active")
        print("by namespace:")
        for ns in sorted(by_namespace):
            row = by_namespace[ns]
            print(f"  {ns}: {row['active']} active / {row['total']} total")

        print(f"extraction checkpoints: {store.extraction_checkpoint_count()}")
        print(f"db file size: {db.stat().st_size} bytes")

        last_ts = store.last_lifecycle_event_ts()
        if last_ts is None:
            print("last consolidate/audit event: none")
        else:
            when = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.localtime(last_ts))
            print(f"last consolidate/audit event: {when}")
    finally:
        store.close()
    return 0


def _cmd_vault_promote(args: argparse.Namespace) -> int:
    from .vault import promote_notes

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    store = EpisodicStore(db)
    try:
        result = promote_notes(
            store, args.vault, namespace=args.namespace, run_id=args.run_id
        )
    finally:
        store.close()
    for line in result.detail:
        print(f"  {line}")
    print(
        f"{result.promoted} promoted, {result.updated} updated, "
        f"{result.unchanged} unchanged, {result.skipped} skipped <- {args.vault}/notes"
    )
    return 0


def _cmd_vault_check(args: argparse.Namespace) -> int:
    from .vault import check_vault

    result = check_vault(args.vault)
    if result.broken:
        print(f"{result.broken_count} broken link(s) found ({result.checked} link(s) checked):")
        for source, target in result.broken:
            print(f"  {source}: [[{target}]]")
        return 1
    print(f"0 broken links ({result.checked} link(s) checked)")
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import serve
    from .sdk import MemoryClient

    kwargs = {"retrieval": args.retrieval, "embedder": args.embedder}
    if args.db:
        kwargs["db_path"] = args.db
    client = MemoryClient(**kwargs)
    return serve(client)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memsub", description="local-first memory for agents")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ext = sub.add_parser(
        "extract", help="extract candidate memories from one or more transcript files"
    )
    p_ext.add_argument(
        "files",
        nargs="+",
        help="transcript file(s) (.jsonl or plain text); shell globs work, "
        "or pass a quoted glob pattern directly",
    )
    p_ext.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_ext.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "api", "ollama", "cli", "mock"],
        help="LLM backend (auto: API key, else local ollama, else claude CLI)",
    )
    p_ext.add_argument("--model", default=None, help="model override for the backend")
    p_ext.add_argument("--namespace", default="default", help="namespace to file memories under")
    p_ext.add_argument("--run-id", default="default", help="run id to file memories under")
    p_ext.add_argument(
        "--session-id", default=None, help="session id (default: each file's stem)"
    )
    p_ext.add_argument("--max-chars", type=int, default=12000, help="chunk size in characters")
    p_ext.add_argument(
        "--min-turns",
        type=int,
        default=3,
        help="skip files with fewer real (user/assistant) turns than this "
        "(default 3: a real ~/.claude/projects/ corpus sits almost entirely "
        "at exactly 2 turns, one user message plus one automated reply, so "
        "the useful floor for 'an actual back-and-forth happened' is 3, not "
        "2; measured on 500 sampled sessions during round-2 dogfooding, see "
        "docs/dogfood-2-2026-07-12.md)",
    )
    p_ext.add_argument("--dry-run", action="store_true", help="print candidates, write nothing")
    p_ext.set_defaults(func=_cmd_extract)

    p_con = sub.add_parser("consolidate", help="dedup, resolve contradictions, decay low-value memories")
    p_con.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_con.add_argument("--backend", default="auto", choices=["auto", "api", "ollama", "cli", "mock"])
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

    p_sleep = sub.add_parser(
        "sleep", help="offline consolidation: session summaries, inferences, entity links"
    )
    p_sleep.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_sleep.add_argument("--backend", default="auto", choices=["auto", "api", "ollama", "cli", "mock"])
    p_sleep.add_argument("--model", default=None, help="model override for the backend")
    p_sleep.add_argument("--namespace", default=None, help="restrict to one namespace")
    p_sleep.add_argument("--run-id", default=None, help="restrict to one run id")
    p_sleep.add_argument("--dry-run", action="store_true", help="print what would be written, write nothing")
    p_sleep.set_defaults(func=_cmd_sleep)

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

    p_srch = sub.add_parser("search", help="search memories (benchmarked hybrid retrieval by default)")
    p_srch.add_argument("query", help="what you want to remember")
    p_srch.add_argument("--k", type=int, default=6, help="max results (default 6)")
    p_srch.add_argument("--budget", type=int, default=None, help="optional hard token cap on results")
    p_srch.add_argument("--namespace", default=None, help="namespace to search (default: $MEMSUB_NAMESPACE or 'default')")
    p_srch.add_argument("--retrieval", default="hybrid", choices=["hybrid", "dense"],
                        help="hybrid (v2 default) or dense (v1 embedding-only)")
    p_srch.add_argument("--embedder", default="hashing", choices=["hashing", "ollama", "auto"],
                        help="hashing (default, zero-dependency) or ollama (real local embeddings)")
    p_srch.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_srch.set_defaults(func=_cmd_search)

    p_vault = sub.add_parser("vault", help="export the store to an Obsidian-compatible markdown vault")
    p_vault.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    vault_sub = p_vault.add_subparsers(dest="vault_command", required=True)
    v_sync = vault_sub.add_parser("sync", help="write/update/remove notes so the vault matches the store")
    v_sync.add_argument("--vault", required=True, help="vault folder (created if missing)")
    v_sync.add_argument("--namespace", default=None, help="restrict to one namespace (default: all)")
    v_sync.set_defaults(func=_cmd_vault_sync)

    v_apply = vault_sub.add_parser(
        "apply",
        help="read human edits and verdict: reject/confirm flags in the vault back into the store",
    )
    v_apply.add_argument("--vault", required=True, help="vault folder to read")
    v_apply.add_argument("--namespace", default=None, help="restrict to one namespace (default: all)")
    v_apply.add_argument(
        "--run-id", default="vault-apply", help="run id attached to any new (corrected) episode"
    )
    v_apply.set_defaults(func=_cmd_vault_apply)

    v_promote = vault_sub.add_parser(
        "promote",
        help="turn new/changed notes under <vault>/notes/ into human_authored memories",
    )
    v_promote.add_argument("--vault", required=True, help="vault folder (notes/ subfolder is scanned)")
    v_promote.add_argument(
        "--namespace", default="default", help="namespace to file promoted memories under"
    )
    v_promote.add_argument(
        "--run-id", default="vault-promote", help="run id attached to promoted episodes"
    )
    v_promote.set_defaults(func=_cmd_vault_promote)

    v_check = vault_sub.add_parser(
        "check", help="scan a vault for unresolved [[wikilinks]] (broken links)"
    )
    v_check.add_argument("--vault", required=True, help="vault folder to scan")
    v_check.set_defaults(func=_cmd_vault_check)

    p_stats = sub.add_parser(
        "stats", help="store summary: episode/namespace counts, checkpoints, db size"
    )
    p_stats.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_stats.set_defaults(func=_cmd_stats)

    p_mcp = sub.add_parser("mcp", help="run the MCP stdio server (for claude mcp add)")
    p_mcp.add_argument("--db", default=None, help="SQLite store path (default: $MEMSUB_DB or ~/.memsub/memory.db)")
    p_mcp.add_argument("--retrieval", default="hybrid", choices=["hybrid", "dense"],
                       help="retrieval stack for search_memory (default: hybrid v2)")
    p_mcp.add_argument("--embedder", default="hashing", choices=["hashing", "ollama", "auto"],
                       help="hashing (default, zero-dependency) or ollama (real local embeddings)")
    p_mcp.set_defaults(func=_cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
