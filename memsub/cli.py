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

from . import provenance
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
        # Extraction only ever produces observed claims (never kind="inference"),
        # so every episode from this path is channel user_stated (memsub.provenance).
        episodes = [
            provenance.tag_episode(
                c.to_episode(run_id=args.run_id, session_id=session_id, namespace=args.namespace),
                provenance.CHANNEL_USER_STATED,
            )
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
    if args.stage:
        # Approve-before-apply: compute actions dry-run (store untouched), then
        # write each as a pending proposal note into the vault for review.
        from .vault import stage_actions

        if not args.vault:
            print("--stage requires --vault")
            store.close()
            return 1
        actions = consolidate(
            store, backend=backend, config=cfg, run_id=args.run_id,
            namespace=args.namespace, dry_run=True, enable_graph=args.graph,
        )
        for a in actions:
            extra = f" by={a.detail['by']}" if "by" in a.detail else ""
            print(f"{a.action:>9} [{a.reason}] {a.episode_id}{extra}")
        stage_result = stage_actions(store, actions, args.vault)
        remaining = len(store.active(run_id=args.run_id, namespace=args.namespace))
        print(
            f"{stage_result.staged} proposal(s) staged, "
            f"{stage_result.existing} already staged, "
            f"{stage_result.skipped} skipped -> {args.vault}/staging; "
            f"store unchanged, {remaining} active memories remain"
        )
        store.close()
        return 0
    actions = consolidate(
        store, backend=backend, config=cfg, run_id=args.run_id,
        namespace=args.namespace, dry_run=args.dry_run, enable_graph=args.graph,
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


def _cmd_graph_backfill(args: argparse.Namespace) -> int:
    import time as _time

    from .graph import graph_store

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    backend = None if args.backend == "none" else build_backend(prefer=args.backend, model=args.model)
    store = EpisodicStore(db)
    t0 = _time.time()
    result = graph_store(
        store, backend=backend, run_id=args.run_id, namespace=args.namespace, dry_run=args.dry_run,
    )
    elapsed = _time.time() - t0
    label = "proposed (dry run)" if args.dry_run else "written"
    print(
        f"{result.episodes_processed} episode(s) processed, "
        f"{result.entities_upserted} entit{'y' if result.entities_upserted == 1 else 'ies'} "
        f"{label}, {result.links_created} link(s) {label}, "
        f"{result.llm_calls} LLM call(s) ({elapsed:.2f}s)"
    )
    print(f"index totals: {store.entity_count()} entities, {store.entity_link_count()} links")
    store.close()
    return 0


def _cmd_distill(args: argparse.Namespace) -> int:
    """Layer 1: cluster active memories into topics and write/update
    vault/topics/<slug>.md for each (idempotent: unchanged topics cost zero
    LLM calls). See memsub/distill.py's module docstring and WORKLOG.md's
    2026-07-20 "Second brain Layers 1 + 4" entry for the full design."""
    from .distill import distill

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    backend = None if args.backend == "none" else build_backend(prefer=args.backend, model=args.model)
    store = EpisodicStore(db)
    try:
        result = distill(
            store, args.vault, backend=backend, namespace=args.namespace,
            min_size=args.min_size, dry_run=args.dry_run,
        )
    finally:
        store.close()
    label = "would write" if args.dry_run else "written"
    print(
        f"{result.topics_found} topic(s) found, {result.topics_written} {label}, "
        f"{result.topics_skipped_unchanged} unchanged (skipped), "
        f"{result.topics_killed} killed (verdict: reject), "
        f"{result.llm_calls} LLM call(s) -> {args.vault}/topics/"
    )
    return 0


def _cmd_idea(args: argparse.Namespace) -> int:
    """Layer 4: instant deliberate capture, bypassing the 30-min extraction
    wait. Writes a user_stated, provenance-signed episode immediately and
    echoes which already-known entities/topics it attached to (cheap,
    LLM-free match)."""
    from .distill import match_entities_and_topics, write_idea

    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = EpisodicStore(db)
    try:
        ep = write_idea(store, args.text, namespace=args.namespace)
        entities, topics = match_entities_and_topics(
            store, args.text, args.namespace, vault_path=args.vault
        )
    finally:
        store.close()
    print(f"captured idea {ep.id[:8]}")
    print(f"  entities: {', '.join(entities) if entities else '(none matched)'}")
    print(f"  topics: {', '.join(topics) if topics else '(none matched)'}")
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
        f"{result.skipped} skipped, {result.staged_applied} staged applied, "
        f"{result.staged_rejected} staged rejected <- {args.vault}"
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


def _cmd_garden_status(args: argparse.Namespace) -> int:
    from . import garden

    vault = Path(args.vault)
    if not vault.exists():
        print(f"no vault at {vault}")
        return 1
    store = None
    db = Path(args.db) if args.db else None
    if db is not None and db.exists():
        store = EpisodicStore(db)
    try:
        notes = garden.resolve_publish_set(vault, store=store)
    finally:
        if store is not None:
            store.close()
    counts = garden.status_counts(notes)
    print(f"published: {counts[garden.STATUS_PUBLISHED]}")
    print(f"eligible, blocked by PII: {counts[garden.STATUS_BLOCKED_PII]}")
    print(f"inferred, awaiting publish_inferred flag: {counts[garden.STATUS_INFERRED_AWAITING_FLAG]}")
    for rel, note in sorted(notes.items()):
        if note.status == garden.STATUS_BLOCKED_PII:
            print(f"  blocked_pii: {rel} ({', '.join(note.pii_findings)})")
        elif note.status == garden.STATUS_INFERRED_AWAITING_FLAG:
            print(f"  inferred_awaiting_flag: {rel}")
    return 0


def _cmd_garden_review(args: argparse.Namespace) -> int:
    from . import garden

    vault = Path(args.vault)
    if not vault.exists():
        print(f"no vault at {vault}")
        return 1
    store = None
    db = Path(args.db) if args.db else None
    if db is not None and db.exists():
        store = EpisodicStore(db)
    try:
        result = garden.review(vault, store=store)
    finally:
        if store is not None:
            store.close()
    diff = result.diff
    for rel in diff.added:
        print(f"  + {rel}")
    for rel in diff.changed:
        print(f"  ~ {rel}")
    for rel in diff.removed:
        print(f"  - {rel}")
    if diff.clean:
        print("no changes since last review")
    print(f"{result.approved_count} note(s) approved for publish -> {vault}/{garden.MANIFEST_NAME}")
    return 0


def _cmd_garden_build(args: argparse.Namespace) -> int:
    from . import garden

    vault = Path(args.vault)
    if not vault.exists():
        print(f"no vault at {vault}")
        return 1
    out_dir = Path(args.out) if args.out else vault / garden.DEFAULT_SITE_DIRNAME
    store = None
    db = Path(args.db) if args.db else None
    if db is not None and db.exists():
        store = EpisodicStore(db)
    try:
        result = garden.build_site(vault, out_dir, store=store, yes=args.yes)
    finally:
        if store is not None:
            store.close()
    if result.needs_review is not None:
        diff = result.needs_review
        print("build refused: unreviewed changes to the publish set. Run `memsub garden review` first, or pass --yes.")
        for rel in diff.added:
            print(f"  + {rel} (new)")
        for rel in diff.changed:
            print(f"  ~ {rel} (content changed)")
        return 1
    print(
        f"{result.published} note(s) published, {result.assets_copied} asset(s) copied, "
        f"{result.blocked_pii} blocked by PII, {result.inferred_awaiting_flag} inferred awaiting flag "
        f"-> {result.out_dir}"
    )
    return 0


def _cmd_vault_serve(args: argparse.Namespace) -> int:
    from .vault_serve import serve_vault

    # --root and --vault point at the same viewer (see memsub/vault_serve.py
    # module docstring); --root just overrides which folder is served.
    root = Path(args.root) if args.root else Path(args.vault)
    return serve_vault(root, port=args.port, db_path=args.db, loops_dirs=args.projects_dir)


def _cmd_wiki_ingest(args: argparse.Namespace) -> int:
    """Ingest a folder of markdown wiki pages into the store as claim episodes.

    Idempotent by content hash (reuses the extraction-checkpoint mechanism): an
    unchanged page is skipped, a changed page supersedes its own prior claims
    and re-extracts. After ingesting, `memsub consolidate --stage` surfaces
    cross-page contradictions as staged proposals.
    """
    from .wiki import ingest_wiki

    pages = Path(args.pages)
    if not pages.exists():
        print(f"no wiki folder at {pages}")
        return 1
    backend = build_backend(prefer=args.backend, model=args.model)
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = EpisodicStore(db)
    try:
        result = ingest_wiki(
            store, pages, backend, namespace=args.namespace, run_id=args.run_id,
            max_chars=args.max_chars,
        )
    finally:
        store.close()
    for line in result.detail:
        print(f"  {line}")
    print(
        f"{result.pages_ingested} page(s) ingested "
        f"({result.pages_unchanged} unchanged, {result.pages_empty} empty), "
        f"{result.claims_written} claim(s) written, "
        f"{result.prior_superseded} prior claim(s) superseded "
        f"across {result.pages_scanned} page(s) -> {args.db}"
    )
    return 0


def _cmd_graph_backfill(args: argparse.Namespace) -> int:
    """Populate the graph layer's entity-episode index over an existing
    store (memsub/graph.py's graph_store, the same write path consolidate()'s
    opt-in enable_graph flag uses). Idempotent: already-graphed episodes are
    skipped on rerun. Rule-based extraction by default (--backend none,
    zero LLM cost); an LLM is only spent on the rare ambiguous entity-dedup
    pair when a real backend is given.
    """
    from .graph import graph_store

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}")
        return 1
    backend = None if args.backend == "none" else build_backend(prefer=args.backend, model=args.model)
    store = EpisodicStore(db)
    result = graph_store(
        store, backend=backend, run_id=args.run_id, namespace=args.namespace, dry_run=args.dry_run,
    )
    label = "proposed (dry run)" if args.dry_run else "written"
    print(
        f"{result.episodes_processed} episode(s) processed, "
        f"{result.entities_upserted} entit{'y' if result.entities_upserted == 1 else 'ies'} / "
        f"{result.links_created} link(s) {label}, {result.llm_calls} LLM call(s)"
    )
    store.close()
    return 0


def _cmd_loops_scan(args: argparse.Namespace) -> int:
    from .loops import render_loops_report, scan_loops

    dirs = [args.projects_dir] if args.projects_dir else None
    findings = scan_loops(dirs, days=args.since)
    print(render_loops_report(findings))
    return 0


def _cmd_loops_json(args: argparse.Namespace) -> int:
    import dataclasses
    import json as jsonmod

    from .loops import scan_loops

    dirs = [args.projects_dir] if args.projects_dir else None
    findings = scan_loops(dirs, days=args.since)
    print(jsonmod.dumps([dataclasses.asdict(f) for f in findings], indent=2))
    return 0


def _fmt_age(seconds: float) -> str:
    """Compact relative age for a recall line, e.g. '3h ago', '12d ago'."""
    seconds = max(seconds, 0.0)
    if seconds < 90:
        return f"{int(seconds)}s ago"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{int(minutes)}m ago"
    hours = minutes / 60.0
    if hours < 36:
        return f"{int(hours)}h ago"
    days = hours / 24.0
    return f"{int(days)}d ago"


def _cmd_recall(args: argparse.Namespace) -> int:
    """Read-only auto-recall for scripts/memsub-recall-hook.sh: hybrid
    retrieval (memsub.sdk.MemoryClient, no LLM call anywhere in this path),
    printing a compact plain-text block or nothing at all.

    HybridRetriever.search's returned score is a rank-derived RRF position
    score (1/(rrf_k+pos+1)) -- comparable within one query's own ranking,
    not across queries, and not a relevance measure at all (an entirely
    irrelevant query still gets a "top" hit at very nearly the same score
    as a perfectly on-topic one). So the injection gate here is a *separate*
    signal: raw cosine similarity between the query and each candidate's
    content, computed with the same embedder. Empirically tuned against the
    real ~/.memsub/memory.db claude-code namespace (4200+ episodes spanning
    many unrelated projects): 0.35 let one clearly-irrelevant query
    ("is it going to rain tomorrow in paris") through on pure hash-trigram
    coincidence; 0.38 was the lowest floor that stayed silent on every
    tested irrelevant query while still firing on clearly on-topic ones.
    Silence is the default outcome, by design: a missed weak match costs
    nothing, a false injection spams every prompt.

    Quarantined episodes (memsub.provenance: signature verification failed)
    are never injected, independent of store.active()'s own filtering --
    this is the one read path that puts memory content directly back into a
    live agent's context, so it re-checks the signature itself rather than
    trusting that a consolidate() pass already ran for this namespace.
    """
    import time as _time

    from . import provenance as _prov
    from .sdk import MemoryClient

    db = Path(args.db)
    if not db.exists():
        return 0  # nothing to recall from: silent, not an error
    client = MemoryClient(db_path=db, namespace=args.namespace, retrieval="hybrid")
    try:
        hits = client.search(args.query, k=args.k, log=False)
        if not hits:
            return 0
        qvec = client.embedder.embed(args.query)
        key = _prov.load_key()
        now = _time.time()
        lines: list[str] = []
        used = 0
        for h in hits:
            ep = h.episode
            status, channel = _prov.channel_status(ep, key)
            if status == _prov.STATUS_QUARANTINED:
                continue
            cvec = client.embedder.embed(ep.content)
            cos = float(qvec @ cvec)
            if cos < args.min_relevance:
                continue
            tag = channel if status == _prov.STATUS_VERIFIED else "legacy"
            line = f"- {ep.content} [{tag}, {_fmt_age(now - ep.ts)}]"
            cost = len(line) + 1  # +1 for the newline joining lines
            if used + cost > args.max_chars:
                break
            lines.append(line)
            used += cost
        if lines:
            print("\n".join(lines))
        return 0
    finally:
        client.close()


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
    p_con.add_argument(
        "--stage",
        action="store_true",
        help="write actions as pending proposals into <vault>/staging for "
        "review instead of applying them; requires --vault. Approve with "
        "verdict: accept/reject in the note, then run `memsub vault apply`",
    )
    p_con.add_argument(
        "--vault", default=None, help="vault folder for --stage proposals"
    )
    p_con.add_argument(
        "--graph", action="store_true",
        help="also populate the entity graph index (memsub graph backfill's "
        "incremental write-path hook); off by default",
    )
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

    p_graph = sub.add_parser("graph", help="bipartite entity-episode graph index")
    graph_sub = p_graph.add_subparsers(dest="graph_command", required=True)
    g_backfill = graph_sub.add_parser(
        "backfill",
        help="rule-based entity extraction over all active episodes, populating "
        "the entities/entity_links index (idempotent: already-indexed episodes "
        "are skipped on rerun)",
    )
    g_backfill.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    g_backfill.add_argument(
        "--backend", default="none", choices=["none", "auto", "api", "ollama", "cli", "mock"],
        help="LLM backend for ambiguous entity-dedup pairs only (default: none, "
        "rule-based extraction with zero LLM calls; near-misses become new "
        "entities rather than being guessed at)",
    )
    g_backfill.add_argument("--model", default=None, help="model override for the backend")
    g_backfill.add_argument("--namespace", default=None, help="restrict to one namespace (default: all)")
    g_backfill.add_argument("--run-id", default=None, help="restrict to one run id")
    g_backfill.add_argument("--dry-run", action="store_true", help="report what would be indexed, write nothing")
    g_backfill.set_defaults(func=_cmd_graph_backfill)

    p_distill = sub.add_parser(
        "distill",
        help="Layer 1: cluster active memories into topics, write/update "
        "vault/topics/<slug>.md for each (idempotent, local qwen3:8b only). "
        "Run `memsub graph backfill` first so entity_links exist to cluster from.",
    )
    p_distill.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_distill.add_argument(
        "--vault", default=str(Path.home() / "memsub-vault"), help="vault folder (topics/ written under it)"
    )
    p_distill.add_argument(
        "--backend", default="auto", choices=["none", "auto", "api", "ollama", "cli", "mock"],
        help="LLM backend for topic title/overview/facts/open-questions (default: auto, "
        "prefers a local ollama qwen3:8b daemon; --backend none skips the LLM call "
        "entirely and falls back to deterministic content for every topic)",
    )
    p_distill.add_argument("--model", default=None, help="model override for the backend")
    p_distill.add_argument("--namespace", default=None, help="restrict to one namespace (default: all)")
    p_distill.add_argument(
        "--min-size", type=int, default=3, help="minimum members for a cluster to become a topic (default 3)"
    )
    p_distill.add_argument("--dry-run", action="store_true", help="report what would be written, write nothing")
    p_distill.set_defaults(func=_cmd_distill)

    p_idea = sub.add_parser(
        "idea",
        help="Layer 4: instant deliberate capture -- writes a user_stated, "
        "provenance-signed episode immediately, no LLM call, echoes matched entities/topics",
    )
    p_idea.add_argument("text", help="the idea, in your own words")
    p_idea.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_idea.add_argument(
        "--vault", default=str(Path.home() / "memsub-vault"),
        help="vault folder to check for matching topic notes (read-only; skipped if missing)",
    )
    p_idea.add_argument("--namespace", default=os.environ.get("MEMSUB_NAMESPACE", "default"))
    p_idea.set_defaults(func=_cmd_idea)

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

    v_serve = vault_sub.add_parser(
        "serve",
        help="read-only browser viewer for the vault (or any markdown folder "
        "via --root), with [[wikilinks]] and mermaid diagrams rendered",
    )
    v_serve.add_argument(
        "--vault", default=str(Path.home() / "memsub-vault"), help="vault folder to serve"
    )
    v_serve.add_argument(
        "--root", default=None,
        help="serve this markdown folder instead of --vault (same viewer, "
        "e.g. a project's docs/ folder)",
    )
    v_serve.add_argument("--port", type=int, default=8766, help="port to bind (127.0.0.1 only)")
    v_serve.add_argument(
        "--db", default=str(default_db_path()),
        help="SQLite store for the dashboard's real counts (best-effort: falls back to "
        "vault-only mode, derived from note frontmatter, if this path does not exist)",
    )
    v_serve.add_argument(
        "--projects-dir", action="append", default=None,
        help="transcripts dir(s) to scan for the /loops page (repeatable); off by "
        "default (no loops scan, /loops shows 0 findings) since a scan takes a few "
        "seconds against a real ~/.claude*/projects corpus",
    )
    v_serve.set_defaults(func=_cmd_vault_serve)

    p_garden = sub.add_parser(
        "garden",
        help="publishable digital-garden static export: allowlist "
        "(publish: true only), PII-blocked, no silent publish",
    )
    garden_sub = p_garden.add_subparsers(dest="garden_command", required=True)

    g_status = garden_sub.add_parser(
        "status", help="counts: published, blocked by PII, inferred awaiting publish_inferred flag"
    )
    g_status.add_argument("--vault", required=True, help="vault folder to scan")
    g_status.add_argument(
        "--db", default=None,
        help="SQLite store for planted/last-tended dates (optional; falls back "
        "to file frontmatter when omitted or the path does not exist)",
    )
    g_status.set_defaults(func=_cmd_garden_status)

    g_review = garden_sub.add_parser(
        "review",
        help="print the pending publish-set diff and record it as approved "
        "(<vault>/garden.json); `garden build` refuses unreviewed changes",
    )
    g_review.add_argument("--vault", required=True, help="vault folder to scan")
    g_review.add_argument("--db", default=None, help="SQLite store for planted/last-tended dates (optional)")
    g_review.set_defaults(func=_cmd_garden_review)

    g_build = garden_sub.add_parser(
        "build",
        help="write the static garden to <vault>/garden-site/ (or --out); "
        "refuses unreviewed additions/changes unless --yes",
    )
    g_build.add_argument("--vault", required=True, help="vault folder to scan")
    g_build.add_argument("--out", default=None, help="output folder (default: <vault>/garden-site)")
    g_build.add_argument("--db", default=None, help="SQLite store for planted/last-tended dates (optional)")
    g_build.add_argument(
        "--yes", action="store_true",
        help="publish the current set even if garden review has not approved "
        "it (also records this build's set as approved)",
    )
    g_build.set_defaults(func=_cmd_garden_build)

    p_stats = sub.add_parser(
        "stats", help="store summary: episode/namespace counts, checkpoints, db size"
    )
    p_stats.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_stats.set_defaults(func=_cmd_stats)

    p_wiki = sub.add_parser("wiki", help="ingest markdown wiki pages as a memory source")
    wiki_sub = p_wiki.add_subparsers(dest="wiki_command", required=True)
    w_ingest = wiki_sub.add_parser(
        "ingest",
        help="chunk each page in a wiki folder into claim memories; unchanged "
        "pages are skipped, changed pages supersede their own prior claims",
    )
    w_ingest.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    w_ingest.add_argument("--pages", required=True, help="wiki folder to ingest (*.md scanned recursively)")
    w_ingest.add_argument("--backend", default="auto", choices=["auto", "api", "ollama", "cli", "mock"])
    w_ingest.add_argument("--model", default=None, help="model override for the backend")
    w_ingest.add_argument("--namespace", default="default", help="namespace to file claims under")
    w_ingest.add_argument("--run-id", default="wiki", help="run id to file claims under")
    w_ingest.add_argument("--max-chars", type=int, default=12000, help="chunk size in characters")
    w_ingest.set_defaults(func=_cmd_wiki_ingest)

    p_recall = sub.add_parser(
        "recall",
        help="read-only auto-recall block for scripts/memsub-recall-hook.sh: "
        "hybrid retrieval, zero LLM calls, prints nothing when no hit clears "
        "--min-relevance",
    )
    p_recall.add_argument("--db", default=str(default_db_path()), help="SQLite store path")
    p_recall.add_argument("--namespace", default=os.environ.get("MEMSUB_NAMESPACE", "default"))
    p_recall.add_argument("--query", required=True, help="the prompt/query text to recall against")
    p_recall.add_argument("--k", type=int, default=3, help="max memories to consider (default 3)")
    p_recall.add_argument(
        "--max-chars", type=int, default=700, help="hard cap on the printed block's size"
    )
    p_recall.add_argument(
        "--min-relevance", type=float, default=0.38,
        help="cosine-similarity floor (query vs. content, same embedder) a hit must "
        "clear to be printed; see _cmd_recall's docstring for how this was tuned",
    )
    p_recall.set_defaults(func=_cmd_recall)

    p_loops = sub.add_parser(
        "loops",
        help="dropped-work detection: sessions that died with work outstanding "
        "(dangling tool calls, silent wakeups, auth/limit deaths, unanswered asks)",
    )
    loops_sub = p_loops.add_subparsers(dest="loops_command", required=True)
    l_scan = loops_sub.add_parser(
        "scan", help="triage-ordered report of findings (interrupt tier first)"
    )
    l_scan.add_argument(
        "--projects-dir", default=None,
        help="transcripts dir to scan (default: every ~/.claude*/projects that exists)",
    )
    l_scan.add_argument(
        "--since", type=int, default=7, help="only sessions touched in the last N days (default 7)"
    )
    l_scan.set_defaults(func=_cmd_loops_scan)

    l_json = loops_sub.add_parser("json", help="the same findings as machine-readable JSON")
    l_json.add_argument("--projects-dir", default=None)
    l_json.add_argument("--since", type=int, default=7)
    l_json.set_defaults(func=_cmd_loops_json)

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
