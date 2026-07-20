"""Build the public-safe build-week demo vault: Maya Chen, indie game
developer building Lighthouse Keeper while freelancing.

Fully synthetic persona, zero real-person data. Every memory is written
through the real pipeline's public write paths (memsub.store.Episode +
memsub.provenance.tag_episode + store.append -- the same primitives
memsub.sdk.MemoryClient.add uses internally, just with explicit historical
timestamps a live SDK call cannot produce), then run through the real
memsub.graph / memsub.consolidate passes (local qwen3:8b only) and
hand-curated into vault/topics/*.md using memsub.distill's own rendering
functions. See WORKLOG.md 2026-07-20 "Build-week demo vault" for the full
assumption list and success criteria this script is built against.

Idempotent by construction: every episode id is a deterministic hash of its
data tag, so re-running this script without --fresh only fills in whatever
is missing (a crash or Ctrl-C mid-run costs nothing). --fresh wipes
demo/memory.db and demo/vault/ first for a clean rebuild.

Run:
    python3 scripts/build_demo_vault.py [--fresh]
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _demo_persona_data import (  # noqa: E402
    DEMO_QUESTIONS,
    RECORDS,
    RIPPLE_TAG,
    SUPERSEDE_CHAINS,
    TOPICS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from memsub import distill, provenance, ripple as ripple_mod, vault  # noqa: E402
from memsub.backends import OllamaBackend  # noqa: E402
from memsub.consolidate import ConsolidationConfig, consolidate  # noqa: E402
from memsub.graph import graph_store  # noqa: E402
from memsub.sdk import MemoryClient  # noqa: E402
from memsub.store import Episode, EpisodicStore  # noqa: E402

DEMO_DIR = REPO_ROOT / "demo"
DB_PATH = DEMO_DIR / "memory.db"
VAULT_PATH = DEMO_DIR / "vault"
NAMESPACE = "maya-chen"
RUN_ID = "demo-build-2026-07-20"
IMPORTANCE = 100.0  # clears decay's value threshold with a real margin even
# ~200 days old with zero retrievals (0.5**(197/30) ~= 0.0106, so 100 *
# that ~= 1.06, five times the 0.2 threshold) -- see WORKLOG.md assumption
# 2. A real consolidate() pass runs against this store; without a wide
# margin the January memories get forgotten on the very first pass, since
# consolidate()'s decay clock is wall-clock "now", not the persona's story
# time. 15.0 was tried first and measured too close to the threshold
# (0.159-0.196 for the earliest dates, decayed some of them -- see
# WORKLOG.md 2026-07-20 "Build-week demo vault" iteration notes).

CONTENT_SYSTEM = """You write short, warm, personal-memory-style notes about a person named Maya Chen, an indie game developer.

You will be given one factual note. Rewrite it as ONE natural sentence (or, only if the note has two clauses, up to two short sentences) in plain English, third person, as if journaling a memory about Maya.

Rules:
- Keep every proper noun, name, number, and date exactly as given. Never invent people, places, tools, or numbers not present in the note.
- Do not add commentary, opinions, or details beyond what the note states.
- Do not use the words "note", "bullet", "episode", "memory", or any pipeline jargon.
- Output ONLY the rewritten sentence(s). No quotes, no preamble, no markdown."""


def _ts(date_str: str, tag: str) -> float:
    """Deterministic timestamp: the story date at a stable per-tag hour/
    minute offset (spreads same-day entries without colliding, and without
    depending on wall-clock 'now')."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    offset_minutes = int(hashlib.sha1(tag.encode()).hexdigest()[:4], 16) % (12 * 60)
    return dt.timestamp() + 8 * 3600 + offset_minutes * 60


def _episode_id(tag: str) -> str:
    return hashlib.sha1(f"maya-chen-demo|{tag}".encode()).hexdigest()


def _clean(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()
    return " ".join(text.split())


def generate_content(bullet: str, backend) -> str:
    raw = backend.complete(CONTENT_SYSTEM, [{"role": "user", "content": bullet}])
    cleaned = _clean(raw)
    return cleaned or bullet  # never write an empty episode


def write_raw_episodes(store: EpisodicStore, backend) -> dict[str, str]:
    """Write every persona record not already present. Returns tag -> id."""
    tag_to_id: dict[str, str] = {}
    written = skipped = 0
    for record in RECORDS:
        tag, date, kind, channel, bullet = record[:5]
        valid_from = record[5] if len(record) > 5 else None
        valid_to = record[6] if len(record) > 6 else None
        ep_id = _episode_id(tag)
        tag_to_id[tag] = ep_id
        if store.get(ep_id) is not None:
            skipped += 1
            continue
        content = generate_content(bullet, backend)
        ep = Episode(
            content=content,
            run_id=RUN_ID,
            session_id=f"maya-{date}",
            kind=kind,
            importance=IMPORTANCE,
            provenance={"origin": "demo_build", "persona": "maya-chen", "tag": tag},
            namespace=NAMESPACE,
            ts=_ts(date, tag),
            id=ep_id,
            valid_from=datetime.strptime(valid_from, "%Y-%m-%d").timestamp() if valid_from else None,
            valid_to=datetime.strptime(valid_to, "%Y-%m-%d").timestamp() if valid_to else None,
        )
        ep = provenance.tag_episode(ep, channel)
        store.append(ep)
        written += 1
        print(f"  [{written + skipped}/{len(RECORDS)}] wrote {tag}", flush=True)
    print(f"raw episodes: {written} written, {skipped} already present")
    return tag_to_id


def tamper_quarantine_exhibit(tag_to_id: dict[str, str]) -> None:
    """Flip a character directly in the DB's content column for the
    imported-channel exhibit episode -- bypassing store.append entirely, the
    same way a real tamper would. Idempotent via a 'demo_tampered' marker
    event (memsub.store.log_event is the public audit-trail API)."""
    ep_id = tag_to_id["quarantine.bookmark"]
    store = EpisodicStore(DB_PATH)
    try:
        if store.events(episode_id=ep_id, event="demo_tampered"):
            print("quarantine exhibit: already tampered, skipping")
            return
        ep = store.get(ep_id)
        assert ep is not None
        conn = sqlite3.connect(DB_PATH)
        try:
            i = len(ep.content) // 2
            flipped = chr((ord(ep.content[i]) + 1) % 0x110000)
            corrupted = ep.content[:i] + flipped + ep.content[i + 1 :]
            conn.execute("UPDATE episodes SET content = ? WHERE id = ?", (corrupted, ep_id))
            conn.commit()
        finally:
            conn.close()
        store.log_event(ep_id, "demo_tampered", {"reason": "quarantine_showcase"})
        print(f"quarantine exhibit: tampered content of {ep_id[:8]}")
    finally:
        store.close()


def apply_supersede_chains(store: EpisodicStore, tag_to_id: dict[str, str]) -> None:
    applied = skipped = 0
    for loser_tag, winner_tag, reason in SUPERSEDE_CHAINS:
        loser_id, winner_id = tag_to_id[loser_tag], tag_to_id[winner_tag]
        if store.status(loser_id) != "active":
            skipped += 1
            continue
        winner = store.get(winner_id)
        detail = {"reason": reason, "by": winner_id}
        if winner is not None and winner.valid_from is not None:
            detail["valid_to"] = winner.valid_from
        store.log_event(loser_id, "superseded", detail)
        if "valid_to" in detail:
            store.set_valid_to(loser_id, detail["valid_to"])
        applied += 1
    print(f"supersede chains: {applied} applied, {skipped} already applied")


def build_topics(store: EpisodicStore, tag_to_id: dict[str, str], backend, fresh: bool) -> list[str]:
    topics_dir = VAULT_PATH / "topics"
    slugs = []
    llm_calls = 0
    for slug, member_tags in TOPICS.items():
        path = topics_dir / f"{slug}.md"
        if path.exists() and not fresh:
            slugs.append(slug)
            continue
        members = [store.get(tag_to_id[t]) for t in member_tags]
        members = [m for m in members if m is not None]
        if slug == "engine-choice-and-tooling":
            # if the real fusion pass produced a fused episode citing the
            # Godot decision, include it as a member too -- the topic note
            # then cites the synthesized fact by wikilink, same as any raw
            # source (memsub.ripple._topic_edges only cares whether the
            # episode's id shows up as a member wikilink in the note text).
            fused_events = store.events(episode_id=tag_to_id[RIPPLE_TAG], event="fused_into")
            for ev in fused_events:
                fused = store.get(ev.detail.get("by", ""))
                if fused is not None and fused not in members:
                    members.append(fused)
        members.sort(key=lambda ep: ep.ts)
        topic = distill.Topic(members=members)
        content = distill.generate_topic_content(topic, backend)
        llm_calls += content["llm_calls"]
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
        fields = distill.topic_frontmatter(topic, slug, now_iso)
        machine_block = distill.render_machine_block(topic, content)
        full_text = f"{distill._render_frontmatter(fields)}\n\n{machine_block}\n"
        distill._write_if_changed(path, full_text)
        slugs.append(slug)
        print(f"  topic '{slug}': {len(members)} members")
    print(f"topics: {len(slugs)} written/present, {llm_calls} LLM calls this run")
    return slugs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh", action="store_true", help="wipe demo/ and rebuild from scratch")
    args = parser.parse_args()

    if args.fresh:
        if DB_PATH.exists():
            DB_PATH.unlink()
        for suffix in ("-wal", "-shm"):
            p = Path(str(DB_PATH) + suffix)
            if p.exists():
                p.unlink()
        if VAULT_PATH.exists():
            shutil.rmtree(VAULT_PATH)
        print("--fresh: wiped demo/memory.db and demo/vault/")

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    # think=False: ~25x faster for bounded, structured-output tasks (this
    # module's own docstring, backends.py) -- content rewrites, fusion, and
    # contradiction judgments are all short direct answers, not the kind of
    # task that benefits from qwen3's reasoning pass. Local-only, per the
    # brief ("local qwen3:8b only for content generation").
    backend = OllamaBackend(model="qwen3:8b", think=False)
    print(f"backend: {backend.name}/{backend.model} (think=False)")

    store = EpisodicStore(DB_PATH)
    try:
        print("\n== phase 1: raw episodes ==")
        tag_to_id = write_raw_episodes(store, backend)

        print("\n== phase 2: quarantine exhibit ==")
        tamper_quarantine_exhibit(tag_to_id)

        # Graph backfill and consolidate (fusion in particular) only ever
        # look at *currently active* episodes -- store.active() at call
        # time. They MUST run before the deliberate supersede chains below,
        # or the engine-choice decision (about to be superseded by the
        # Unity switch) would already be inactive and invisible to both the
        # entity-link graph (associative tier) and the fusion pass (direct
        # tier), gutting the ripple showcase. Found live: a first run with
        # the old phase order produced an empty associative tier and no
        # fusion citing the decision at all -- see WORKLOG.md 2026-07-20
        # "Build-week demo vault" iteration note.
        print("\n== phase 3: graph backfill ==")
        gresult = graph_store(store, backend=backend, namespace=NAMESPACE)
        print(f"  entities +{gresult.entities_upserted}, links +{gresult.links_created}, "
              f"episodes processed {gresult.episodes_processed}, llm_calls {gresult.llm_calls}")

        print("\n== phase 4: consolidate (dedup/contradiction/fusion/decay) ==")
        # fusion_max_group_size=20: 17+ episodes mention "Godot" by the time
        # qwen has rewritten every bullet (more than the ~10 planned, since
        # rewrites of Unity-switch bullets naturally still say "from Godot
        # to Unity") -- 20 is comfortably above that count, so
        # _select_group_members's `len(members) <= max_size` skips
        # coherence trimming entirely for that group, guaranteeing the
        # engine decision itself survives as a fusion source instead of
        # being trimmed out by chance.
        cfg = ConsolidationConfig(fusion_max_group_size=20, max_fusion_groups=25)
        actions = consolidate(store, backend=backend, config=cfg, namespace=NAMESPACE, enable_graph=False)
        by_action: dict[str, int] = {}
        for a in actions:
            by_action[a.action] = by_action.get(a.action, 0) + 1
        print(f"  actions: {by_action or '(none)'}")
        godot_id = tag_to_id[RIPPLE_TAG]
        fused = store.events(episode_id=godot_id, event="fused_into")
        print(f"  engine.decision.godot fused_into: {[e.detail.get('by') for e in fused] or '(none yet)'}")

        print("\n== phase 5: deliberate supersede chains ==")
        apply_supersede_chains(store, tag_to_id)

        print("\n== phase 6: topic notes ==")
        build_topics(store, tag_to_id, backend, fresh=args.fresh)

        print("\n== phase 7: vault sync (per-episode notes) ==")
        sync_result = vault.sync_vault(store, VAULT_PATH, namespace=NAMESPACE)
        print(f"  {sync_result}")

        print("\n== phase 8: ripple showcase ==")
        report = ripple_mod.ripple(store, godot_id, max_hops=2, vault_path=VAULT_PATH)
        print(ripple_mod.render_report(report))

        print("\n== phase 9: retrieval sanity ==")
        # embedder="auto": real nomic-embed-text embeddings via the local
        # ollama daemon (falls back to the crude hashing embedder if ollama
        # is unreachable) -- meaningfully better dense-signal ranking for
        # short natural-language questions than the zero-dependency default.
        client = MemoryClient(db_path=DB_PATH, namespace=NAMESPACE, embedder="auto")
        for q in DEMO_QUESTIONS:
            hits = client.search(q, k=4)
            print(f"  Q: {q}")
            for h in hits:
                print(f"     [{h.score:.3f}] {h.episode.content[:110]}")
        client.close()

        print("\n== summary ==")
        active = store.active(namespace=NAMESPACE)
        all_eps = store.all(namespace=NAMESPACE)
        quarantined = [e for e in all_eps if store.status(e.id) == "quarantined"]
        print(f"  episodes: {len(all_eps)} total, {len(active)} active, {len(quarantined)} quarantined")
    finally:
        store.close()


if __name__ == "__main__":
    main()
