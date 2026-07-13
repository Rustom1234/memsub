"""Obsidian vault export: a read-only, browsable view of the episodic store.

`sync_vault` writes one plain markdown note per memory into a folder of
folders (one per namespace), plus a per-namespace index note and a
top-level map-of-content note. No Obsidian plugin is required: it is just
markdown files with YAML frontmatter and `[[wiki-links]]`, which Obsidian
(and plain grep) read natively.

This is a one-way export. There is no vault-to-store editing: the store
stays the single source of truth, and re-running sync is the only supported
way to move data. See docs/obsidian-layer.md for what is deliberately not
built here.

Idempotency: filenames are derived only from the (stable, append-only)
memory id, so a note's path never changes across runs. A note is rewritten
only when its rendered content differs from what is on disk, and notes for
memories no longer in the synced scope are deleted, so a second run with no
store changes touches no files.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .sleep import _extract_entities
from .store import Episode, EpisodicStore

_SLUG_RE = re.compile(r"[^a-z0-9]+")
MIN_ENTITY_SHARERS = 2  # an entity needs to show up in >=2 memories to link them
MAX_ENTITY_LINKS = 8  # per note, keep the Links section readable


@dataclass
class VaultSyncResult:
    written: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    namespaces: list[str] = field(default_factory=list)

    @property
    def notes(self) -> int:
        return self.written + self.updated + self.unchanged


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #
def slugify(text: str, max_len: int = 50) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "memory"


def namespace_dirname(namespace: str) -> str:
    return slugify(namespace, max_len=80)


def note_filename(episode: Episode) -> str:
    return f"{slugify(episode.content)}--{episode.id[:8]}.md"


def note_wikilink(episode: Episode) -> str:
    """The `[[...]]` target for an episode: filename without extension."""
    return note_filename(episode)[:-3]


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #
def _iso(ts: float) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _yaml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


def _frontmatter(fields: dict) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _memory_type(ep: Episode) -> str:
    return "inferred" if (ep.kind == "inference" or ep.provenance.get("inferred")) else "observed"


# --------------------------------------------------------------------------- #
# Cross-note relationships
# --------------------------------------------------------------------------- #
def _entity_index(episodes: list[Episode]) -> dict[str, list[str]]:
    """entity -> episode ids that mention it, for entities in >=2 episodes."""
    hits: dict[str, list[str]] = defaultdict(list)
    for ep in episodes:
        for entity in _extract_entities(ep.content):
            hits[entity].append(ep.id)
    return {e: ids for e, ids in hits.items() if len(ids) >= MIN_ENTITY_SHARERS}


def _supersede_maps(store: EpisodicStore, episodes: list[Episode]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """episode_id -> id of the memory that superseded it, and the reverse map."""
    superseded_by: dict[str, str] = {}
    supersedes: dict[str, list[str]] = defaultdict(list)
    for ep in episodes:
        for ev in store.events(episode_id=ep.id, event="superseded"):
            by = ev.detail.get("by")
            if by:
                superseded_by[ep.id] = by
                supersedes[by].append(ep.id)
            break  # newest first; one terminal event is enough
    return superseded_by, supersedes


# --------------------------------------------------------------------------- #
# Note rendering
# --------------------------------------------------------------------------- #
def render_note(
    ep: Episode,
    store: EpisodicStore,
    status: str,
    by_id: dict[str, Episode],
    entity_index: dict[str, list[str]],
    superseded_by: dict[str, str],
    supersedes: dict[str, list[str]],
) -> str:
    events = store.events(episode_id=ep.id)
    lifecycle_events = [e for e in events if e.event in ("superseded", "forgotten")]
    usage_events = [e for e in events if e.event in ("retrieved", "injected")]
    last_updated = max([ep.ts] + [e.ts for e in lifecycle_events]) if lifecycle_events else ep.ts
    last_used = max((e.ts for e in usage_events), default=None)

    frontmatter = _frontmatter(
        {
            "id": ep.id,
            "namespace": ep.namespace,
            "kind": ep.kind,
            "type": _memory_type(ep),
            "status": status,
            "created": _iso(ep.ts),
            "last_updated": _iso(last_updated),
            "last_used": _iso(last_used) if last_used else "",
            "confidence": ep.provenance.get("confidence"),
        }
    )

    lines = [frontmatter, "", f"# {ep.content[:80]}", "", "## Content", "", ep.content, ""]

    lines += ["## Provenance", ""]
    lines.append(f"- Run: `{ep.run_id}`")
    lines.append(f"- Session: `{ep.session_id}`")
    source = ep.provenance.get("source") or ep.provenance.get("source_session")
    if source:
        lines.append(f"- Source: `{source}`")
    generated_at = (
        ep.provenance.get("extracted_at")
        or ep.provenance.get("summarized_at")
        or ep.provenance.get("generated_at")
    )
    if generated_at:
        lines.append(f"- Generated at: {_iso(float(generated_at))}")
    shown_keys = {
        "source", "source_session", "extracted_at", "summarized_at",
        "generated_at", "confidence",
    }
    for key, value in sorted(ep.provenance.items()):
        if key in shown_keys:
            continue
        lines.append(f"- {key}: {value}")

    lines += ["", "### Audit events", ""]
    if events:
        for ev in events:
            detail = " ".join(f"{k}={v}" for k, v in sorted(ev.detail.items()))
            lines.append(f"- {_iso(ev.ts)}  **{ev.event}**  {detail}".rstrip())
    else:
        lines.append("- none recorded")

    lines += ["", "## Links", ""]
    link_lines: list[str] = []
    if ep.id in superseded_by and superseded_by[ep.id] in by_id:
        link_lines.append(f"- superseded by [[{note_wikilink(by_id[superseded_by[ep.id]])}]]")
    for old_id in supersedes.get(ep.id, []):
        if old_id in by_id:
            link_lines.append(f"- supersedes [[{note_wikilink(by_id[old_id])}]]")
    entity_links = 0
    for entity in sorted(_extract_entities(ep.content)):
        sharers = entity_index.get(entity, [])
        if len(sharers) < MIN_ENTITY_SHARERS:
            continue
        for other_id in sharers:
            if other_id == ep.id or other_id not in by_id:
                continue
            if entity_links >= MAX_ENTITY_LINKS:
                break
            link_lines.append(f"- shares entity **{entity}** with [[{note_wikilink(by_id[other_id])}]]")
            entity_links += 1
    if link_lines:
        lines += link_lines
    else:
        lines.append("- (none)")

    return "\n".join(lines) + "\n"


def render_namespace_index(namespace: str, episodes: list[Episode], statuses: dict[str, str]) -> str:
    lines = [f"# {namespace}", "", f"{len(episodes)} memories.", ""]
    by_kind: dict[str, list[Episode]] = defaultdict(list)
    for ep in episodes:
        by_kind[ep.kind].append(ep)
    for kind in sorted(by_kind):
        group = sorted(by_kind[kind], key=lambda e: e.ts, reverse=True)
        lines.append(f"## {kind} ({len(group)})")
        lines.append("")
        for ep in group:
            status = statuses[ep.id]
            marker = "" if status == "active" else f" *({status})*"
            excerpt = ep.content[:80]
            lines.append(f"- [[{note_wikilink(ep)}]]{marker}: {excerpt}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_moc(namespaces: list[tuple[str, str, int]]) -> str:
    """namespaces: list of (dirname, display_name, note_count)."""
    total = sum(count for _, _, count in namespaces)
    lines = ["# Memory Vault", "", f"{total} memories across {len(namespaces)} namespace(s).", ""]
    for dirname, display_name, count in sorted(namespaces, key=lambda t: t[1]):
        lines.append(f"- [[{dirname}/index|{display_name}]] ({count})")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #
def _write_if_changed(path: Path, content: str) -> str:
    """Returns 'written' | 'updated' | 'unchanged'."""
    if path.exists():
        if path.read_text(encoding="utf-8") == content:
            return "unchanged"
        path.write_text(content, encoding="utf-8")
        return "updated"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "written"


def sync_vault(store: EpisodicStore, vault_path: str | Path, namespace: str | None = None) -> VaultSyncResult:
    vault_root = Path(vault_path)
    vault_root.mkdir(parents=True, exist_ok=True)

    if namespace is not None:
        namespaces = [namespace]
    else:
        namespaces = sorted({ep.namespace for ep in store.all()})

    result = VaultSyncResult(namespaces=namespaces)

    for ns in namespaces:
        episodes = store.all(namespace=ns)
        by_id = {ep.id: ep for ep in episodes}
        statuses = {ep.id: store.status(ep.id) for ep in episodes}
        entity_index = _entity_index(episodes)
        superseded_by, supersedes = _supersede_maps(store, episodes)

        ns_dir = vault_root / namespace_dirname(ns)
        expected_files = set()
        for ep in episodes:
            filename = note_filename(ep)
            expected_files.add(filename)
            content = render_note(
                ep, store, statuses[ep.id], by_id, entity_index, superseded_by, supersedes
            )
            outcome = _write_if_changed(ns_dir / filename, content)
            setattr(result, outcome, getattr(result, outcome) + 1)

        index_content = render_namespace_index(ns, episodes, statuses)
        _write_if_changed(ns_dir / "index.md", index_content)
        expected_files.add("index.md")

        if ns_dir.exists():
            for existing in ns_dir.glob("*.md"):
                if existing.name not in expected_files:
                    existing.unlink()
                    result.removed += 1

    # MOC reflects every namespace folder present in the vault, not just the
    # ones touched this run, so a --namespace-scoped sync does not erase the
    # rest of the map. Display name comes from the index note's own heading
    # ("# <namespace>"), since the directory name is a filesystem-safe slug.
    moc_namespaces: list[tuple[str, str, int]] = []
    for entry in sorted(vault_root.iterdir()):
        index_path = entry / "index.md"
        if not (entry.is_dir() and index_path.exists()):
            continue
        n_notes = sum(1 for f in entry.glob("*.md") if f.name != "index.md")
        first_line = index_path.read_text(encoding="utf-8").splitlines()[0]
        display_name = first_line.lstrip("#").strip() or entry.name
        moc_namespaces.append((entry.name, display_name, n_notes))
    _write_if_changed(vault_root / "index.md", render_moc(moc_namespaces))

    return result
