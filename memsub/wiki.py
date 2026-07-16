"""Wiki mode: markdown pages as a memory source.

memsub's first extraction source was Claude Code transcripts. Wiki mode adds a
second: a folder of agent-written markdown pages (the "LLM wiki" pattern, whose
documented failure mode is contradictions and staleness piling up as the wiki
grows past ~100 pages). `ingest_wiki` chunks each page into atomic claims via
the LLM backend and stores them as episodes, so the wiki's facts flow into the
same store consolidation already curates: `memsub consolidate --stage` then
surfaces cross-page contradictions as staged proposals.

Append-only semantics are preserved. A page's claims carry provenance
`origin: wiki`, `source_page` (the page path relative to the wiki root), and
`source_content_hash`. Re-ingesting an unchanged page is a no-op (the
extraction checkpoint, keyed on the page path, matches its content hash). A
changed page re-extracts: the page's prior active claims are superseded (a
`superseded` event, reason `wiki_page_updated`) and the fresh claims appended,
exactly the supersede-then-append pattern `memsub vault promote` uses for human
notes. Nothing is ever deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .backends import LLMBackend
from .extraction import (
    WIKI_SYSTEM,
    extract_memories,
    file_content_hash,
    load_transcript,
)
from .store import Episode, EpisodicStore


@dataclass
class WikiIngestResult:
    pages_scanned: int = 0
    pages_ingested: int = 0  # new or changed page, (re)extracted
    pages_unchanged: int = 0  # checkpoint hit, skipped
    pages_empty: int = 0  # ingested but no claims extracted
    claims_written: int = 0
    prior_superseded: int = 0  # stale claims retired when a page changed
    detail: list[str] = field(default_factory=list)


def _page_provenance(candidate_provenance: dict, rel: str, page_hash: str) -> dict:
    return {
        **candidate_provenance,
        "origin": "wiki",
        "source_page": rel,
        "source_content_hash": page_hash,
    }


def ingest_wiki(
    store: EpisodicStore,
    pages_dir: str | Path,
    backend: LLMBackend,
    namespace: str = "default",
    run_id: str = "wiki",
    max_chars: int = 12000,
) -> WikiIngestResult:
    """Ingest every `.md` page under `pages_dir` into `store` as claim episodes.

    Idempotent by content hash: an unchanged page is skipped. A changed page
    supersedes its own prior claims (matched on `source_page`) before appending
    the new ones, so the store never accumulates two generations of the same
    page's facts.
    """
    root = Path(pages_dir)
    result = WikiIngestResult()
    if not root.exists():
        return result

    # Prior active wiki claims for this namespace, grouped by source page, so a
    # re-ingest of a changed page can retire exactly that page's stale claims.
    prior_by_page: dict[str, list[Episode]] = {}
    for ep in store.active(namespace=namespace):
        if ep.provenance.get("origin") == "wiki":
            page = ep.provenance.get("source_page")
            if page:
                prior_by_page.setdefault(page, []).append(ep)

    for page_path in sorted(root.rglob("*.md")):
        result.pages_scanned += 1
        rel = page_path.relative_to(root).as_posix()
        checkpoint_key = str(page_path)
        page_hash = file_content_hash(page_path)

        if store.get_extraction_checkpoint(checkpoint_key) == page_hash:
            result.pages_unchanged += 1
            continue

        candidates = extract_memories(
            load_transcript(page_path),
            backend,
            source=rel,
            max_chars=max_chars,
            system=WIKI_SYSTEM,
        )

        # Retire this page's prior claims first, so a crash before the append
        # heals on rerun (the checkpoint is only written with the append) rather
        # than leaving two live generations. superseded, not deleted: append-only.
        for old in prior_by_page.get(rel, []):
            store.log_event(
                old.id,
                "superseded",
                {"reason": "wiki_page_updated", "source_page": rel},
            )
            result.prior_superseded += 1

        episodes = [
            Episode(
                content=c.content,
                run_id=run_id,
                session_id=rel,
                kind=c.kind,
                importance=max(c.confidence, 0.05),
                namespace=namespace,
                provenance=_page_provenance(c.provenance, rel, page_hash),
            )
            for c in candidates
        ]
        written = store.append_batch_with_checkpoint(episodes, checkpoint_key, page_hash)
        result.pages_ingested += 1
        result.claims_written += len(written)
        if not written:
            result.pages_empty += 1
            result.detail.append(f"ingest {rel}: 0 claims")
        else:
            result.detail.append(f"ingest {rel}: {len(written)} claim(s)")

    return result
