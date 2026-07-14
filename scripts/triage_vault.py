#!/usr/bin/env python3
"""One-shot AI triage pass over the memsub store, writing a review queue to
`review-queue.md` at the vault root.

This flags CANDIDATES for Rustom's review only. It never writes a verdict:
only he can confirm/reject/edit a memory (via Obsidian + `memsub vault
apply`, see docs/obsidian-layer.md). The queue lives at the vault root, not
inside a namespace folder, so `memsub vault apply` (which only scans
directories that carry an `index.md` namespace index) never reads it.

Heuristic only (difflib + keyword regex), no LLM call, no new dependency.
Three categories:

  - **corrected_reassertion**: an active memory that closely restates the
    text of an episode already superseded by a human correction elsewhere
    (`memsub vault apply` body edit) -- the same wrong claim resurfacing
    from an independent extraction. This is the general form of the
    OpenClaw-attribution case: the correction is looked up from the store's
    own audit trail (superseded events with reason "human_correction"), not
    hardcoded to any one fact.
  - **stale_transient**: an active memory describing transient system/
    process state (pid, port, service status, "currently") framed as a
    durable fact.
  - **near_duplicate**: two active memories with high lexical similarity
    that consolidation's embedding-based dedup pass did not merge.

Usage: scripts/triage_vault.py [--db PATH] [--vault DIR] [--cap N] [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memsub.cli import default_db_path  # noqa: E402
from memsub.store import Episode, EpisodicStore  # noqa: E402
from memsub.vault import note_wikilink  # noqa: E402

CORRECTED_JACCARD_THRESHOLD = 0.35
NEAR_DUP_SIM_THRESHOLD = 0.72
STOPWORDS = {
    "this", "that", "with", "from", "have", "will", "your", "their", "about",
    "into", "over", "under", "when", "then", "than", "were", "been", "being",
    "which", "while", "there", "these", "those", "using", "used", "uses",
}

TRANSIENT_PATTERNS = [
    re.compile(r"\bpid\s*\d+\b", re.I),
    re.compile(r"\bport\s*\d+\b", re.I),
    re.compile(r"\bcurrently\b", re.I),
    re.compile(r"\b(is|was|are|were)\s+(currently\s+)?(running|disabled|inactive|active)\b", re.I),
    re.compile(r"\bright now\b", re.I),
    re.compile(r"\btoday\b", re.I),
    re.compile(r"\bat the moment\b", re.I),
]


@dataclass
class QueueEntry:
    category: str
    score: float
    wikilink: str
    reason: str
    suggested_verdict: str


@dataclass
class ReviewQueue:
    entries: list[QueueEntry] = field(default_factory=list)
    counts_found: dict[str, int] = field(default_factory=dict)


def _significant_words(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _find_corrected_reassertions(store: EpisodicStore, active: list[Episode]) -> list[QueueEntry]:
    """Active memories that restate the same claim as text a human already
    corrected away elsewhere in the store (superseded with reason
    human_correction). Compared on significant-word overlap (Jaccard), not
    raw string similarity: a rephrased reassertion ("Rustom builds ... using
    ... OpenClaw ..." vs the corrected-away "Rustom has experience ...
    OpenClaw ...") shares the claim-bearing words even though the sentences
    read quite differently."""
    corrections = [
        ev for ev in store.events(event="superseded")
        if ev.detail.get("reason") == "human_correction"
    ]
    if not corrections:
        return []

    corrected_ids = {ev.episode_id for ev in corrections}
    active_words = [_significant_words(ep.content) for ep in active]
    entries: list[QueueEntry] = []
    for ev in corrections:
        old_ep = store.get(ev.episode_id)
        if old_ep is None:
            continue
        new_id = ev.detail.get("by")
        old_link = note_wikilink(old_ep)
        old_words = _significant_words(old_ep.content)
        for ep, ep_words in zip(active, active_words):
            if ep.id == new_id or ep.id in corrected_ids or not old_words:
                continue
            jaccard = len(old_words & ep_words) / len(old_words | ep_words)
            if jaccard >= CORRECTED_JACCARD_THRESHOLD:
                new_ep = store.get(new_id) if new_id else None
                new_link = note_wikilink(new_ep) if new_ep else None
                reason = (
                    f"restates content already corrected away as [[{old_link}]]"
                    + (f" (corrected to [[{new_link}]])" if new_link else "")
                    + f" -- shared-word overlap {jaccard:.2f}"
                )
                entries.append(
                    QueueEntry(
                        category="corrected_reassertion",
                        score=100.0 + jaccard,
                        wikilink=note_wikilink(ep),
                        reason=reason,
                        suggested_verdict="reject, or correct to match the human-verified note",
                    )
                )
    return entries


def _find_stale_transient(active: list[Episode]) -> list[QueueEntry]:
    entries: list[QueueEntry] = []
    for ep in active:
        hits = [p.pattern for p in TRANSIENT_PATTERNS if p.search(ep.content)]
        if hits:
            entries.append(
                QueueEntry(
                    category="stale_transient",
                    score=10.0 + len(hits),
                    wikilink=note_wikilink(ep),
                    reason="describes transient system/process state as a durable fact, likely stale",
                    suggested_verdict="confirm if still true, else reject",
                )
            )
    return entries


def _find_near_duplicates(active: list[Episode]) -> list[QueueEntry]:
    """Active/active pairs with high lexical similarity that consolidation's
    embedding-based dedup pass (memsub/consolidate.py, cosine >= 0.92) did
    not merge. Candidate pairs are narrowed with a word-overlap inverted
    index first so this stays roughly linear in corpus size instead of a
    full O(n^2) SequenceMatcher scan."""
    word_index: dict[str, list[int]] = defaultdict(list)
    sig_words = [_significant_words(ep.content) for ep in active]
    for i, words in enumerate(sig_words):
        for w in words:
            word_index[w].append(i)

    candidate_pairs: set[tuple[int, int]] = set()
    for ids in word_index.values():
        if len(ids) < 2 or len(ids) > 50:  # skip very common words: too many pairs, low signal
            continue
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                i, j = ids[a], ids[b]
                shared = sig_words[i] & sig_words[j]
                if len(shared) >= 2:
                    candidate_pairs.add((min(i, j), max(i, j)))

    seen_notes: set[int] = set()
    entries: list[QueueEntry] = []
    for i, j in sorted(candidate_pairs):
        a, b = active[i], active[j]
        sim = SequenceMatcher(None, a.content, b.content).ratio()
        if sim < NEAR_DUP_SIM_THRESHOLD:
            continue
        # one entry per pair; point at the newer of the two (higher ts)
        newer, older = (b, a) if b.ts >= a.ts else (a, b)
        if newer.id in seen_notes:
            continue
        seen_notes.add(newer.id)
        entries.append(
            QueueEntry(
                category="near_duplicate",
                score=50.0 + sim * 10,
                wikilink=note_wikilink(newer),
                reason=f"near-duplicate of [[{note_wikilink(older)}]] -- lexical similarity {sim:.2f}",
                suggested_verdict="reject (duplicate), or manually merge into the other note",
            )
        )
    return entries


def build_review_queue(store: EpisodicStore) -> ReviewQueue:
    active = store.active()
    corrected = _find_corrected_reassertions(store, active)
    stale = _find_stale_transient(active)
    near_dup = _find_near_duplicates(active)
    queue = ReviewQueue(
        counts_found={
            "corrected_reassertion": len(corrected),
            "stale_transient": len(stale),
            "near_duplicate": len(near_dup),
        }
    )
    queue.entries = corrected + near_dup + stale
    return queue


def render_review_queue(queue: ReviewQueue, cap: int = 100) -> str:
    ranked = sorted(queue.entries, key=lambda e: e.score, reverse=True)
    kept = ranked[:cap]
    total_found = len(ranked)

    lines = [
        "# Review queue",
        "",
        "AI-generated triage candidates for Rustom's review. **No verdicts are",
        "applied here** -- this note only suggests; only a human `verdict:",
        "confirm|reject` or a body edit in a memory note (`memsub vault apply`)",
        "is a real verdict. See `docs/obsidian-layer.md` for the annotate loop.",
        "",
        f"Capped at the {cap} most review-worthy candidates by heuristic score"
        f" ({total_found} found in total). Categories: `corrected_reassertion`"
        " (restates a claim already corrected elsewhere), `near_duplicate`"
        " (consolidation's dedup pass missed it), `stale_transient`"
        " (transient system state stored as a durable fact).",
        "",
        f"Generated by `scripts/triage_vault.py`. {len(kept)} of {total_found} shown.",
        "",
    ]

    by_cat: dict[str, list[QueueEntry]] = defaultdict(list)
    for e in kept:
        by_cat[e.category].append(e)

    for cat in ("corrected_reassertion", "near_duplicate", "stale_transient"):
        entries = by_cat.get(cat, [])
        lines.append(f"## {cat} ({len(entries)} shown / {queue.counts_found.get(cat, 0)} found)")
        lines.append("")
        if not entries:
            lines.append("_none in the top cap._")
            lines.append("")
            continue
        for e in entries:
            lines.append(f"- [[{e.wikilink}]] -- {e.reason}. Suggested: {e.suggested_verdict}")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(default_db_path()))
    parser.add_argument("--vault", default=str(Path.home() / "memsub-vault"))
    parser.add_argument("--cap", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = parser.parse_args(argv)

    store = EpisodicStore(args.db)
    queue = build_review_queue(store)
    text = render_review_queue(queue, cap=args.cap)
    store.close()

    if args.dry_run:
        print(text)
        return 0

    out_path = Path(args.vault) / "review-queue.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(f"wrote {out_path} ({sum(queue.counts_found.values())} candidates found, "
          f"{min(args.cap, len(queue.entries))} shown)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
