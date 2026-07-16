"""Obsidian vault export and annotate loop.

`sync_vault` writes one plain markdown note per memory into a folder of
folders (one per namespace), plus a per-namespace index note and a
top-level map-of-content note. No Obsidian plugin is required: it is just
markdown files with YAML frontmatter and `[[wiki-links]]`, which Obsidian
(and plain grep) read natively.

`apply_vault` is the other direction: it reads a human's edits to notes
sync wrote and turns them into store writes (correction, rejection,
confirm). See docs/obsidian-layer.md for the exact semantics and what apply
deliberately does not do.

Idempotency: filenames are derived only from the (stable, append-only)
memory id, so a note's path never changes across runs. A note is rewritten
only when its rendered content differs from what is on disk, and notes for
memories no longer in the synced scope are deleted, so a second run with no
store changes touches no files.
"""

from __future__ import annotations

import hashlib
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

# Reserved: the human notes promotion folder (see promote_notes below).
# sync_vault must never write or delete files under this folder, so a
# namespace cannot collide with it.
NOTES_DIRNAME = "notes"

# Reserved: the staging folder for pending consolidation proposals (see
# stage_actions below). Same reasoning as notes/: sync must never treat it as
# a namespace, so a namespace cannot slugify to it.
STAGING_DIRNAME = "staging"


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


def content_hash(content: str) -> str:
    """Stable fingerprint of a memory's body text, written to frontmatter at
    sync time and re-checked at apply time to detect a human edit. Content
    never changes for a given episode id (the store is append-only), so this
    hash is deterministic for as long as a note's `id` field points at the
    same episode."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


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
    confirmed = any(e.event == "confirmed" for e in events)
    last_updated = max([ep.ts] + [e.ts for e in lifecycle_events]) if lifecycle_events else ep.ts
    last_used = max((e.ts for e in usage_events), default=None)

    frontmatter_fields = {
        "id": ep.id,
        "namespace": ep.namespace,
        "kind": ep.kind,
        "type": _memory_type(ep),
        "status": status,
        "created": _iso(ep.ts),
        "last_updated": _iso(last_updated),
        "last_used": _iso(last_used) if last_used else "",
        "confidence": ep.provenance.get("confidence"),
        "content_hash": content_hash(ep.content),
    }
    if confirmed:
        frontmatter_fields["human_confirmed"] = True
    frontmatter = _frontmatter(frontmatter_fields)

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
    source_note = ep.provenance.get("source_note")
    if source_note:
        # human notes promotion (memsub vault promote): link back to the
        # notes/ file this memory was promoted from. A bare wikilink (no
        # directory) so Obsidian resolves it by basename across the vault,
        # same as every other cross-note link here.
        link_lines.append(f"- promoted from [[{Path(source_note).stem}]]")
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


TEMPLATE_NOTE_ID = "memsub-template-do-not-edit"


def render_template_note() -> str:
    """A reference note shipped into the vault on every sync, documenting the
    frontmatter fields on a memory note and the verdict values `memsub vault
    apply` understands. Carries a fake id (never a real store id) so apply
    skips it as an orphan if it is ever pointed at the templates/ folder;
    see docs/obsidian-layer.md."""
    return (
        f'---\nid: "{TEMPLATE_NOTE_ID}"\nverdict: ""\n---\n\n'
        "# Memory note template & verdict guide\n\n"
        "This note documents the fields on every memory note in this vault "
        "and the verdict values `memsub vault apply` understands. It is not "
        "itself a memory: its `id` matches no episode in the store, so "
        "`vault apply` skips it as an orphan.\n\n"
        "## Frontmatter fields (written by `memsub vault sync`, read-only)\n\n"
        "- `id` - the memory's stable id in the store.\n"
        "- `namespace` - which namespace this memory belongs to.\n"
        "- `kind` - `fact` | `preference` | `decision` | `event`.\n"
        "- `type` - `observed` (from a transcript or a human correction) or "
        "`inferred` (agent-derived, e.g. by `memsub sleep`).\n"
        "- `status` - `active` | `superseded` | `forgotten`.\n"
        "- `created`, `last_updated`, `last_used` - ISO timestamps.\n"
        "- `confidence` - extractor confidence, 0.0-1.0, if the memory "
        "carries one.\n"
        "- `content_hash` - fingerprint of the body text, used to detect a "
        "human edit; do not hand-edit this field.\n\n"
        "## Reviewing a memory: the `verdict` field\n\n"
        "Set `verdict` in a note's frontmatter, or edit the text under "
        "`## Content`, to tell `memsub vault apply` what to do. Only two "
        "verdict values are understood; anything else is ignored:\n\n"
        "- `verdict: confirm` - pin this memory. It stays active and is "
        "never aged out by consolidation's decay pass.\n"
        "- `verdict: reject` - forget this memory. It is marked forgotten "
        "(kept in the store for audit, no longer active or retrievable).\n\n"
        "A third gesture needs no `verdict` field at all: edit the text "
        "under `## Content` to something else. That is a correction - the "
        "old memory is superseded by a new, human-verified one carrying "
        "your edited text.\n\n"
        "**Precedence if a note carries more than one signal:** reject "
        "beats a body edit beats confirm beats no-op. Full mechanics: "
        "docs/obsidian-layer.md.\n"
    )


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
        if namespace_dirname(ns) in (NOTES_DIRNAME, STAGING_DIRNAME):
            # notes/ and staging/ are reserved (human-authored content and
            # pending consolidation proposals): sync must never write into or
            # delete from them.
            reserved = namespace_dirname(ns)
            raise ValueError(
                f"namespace {ns!r} slugifies to the reserved '{reserved}/' "
                "folder name; rename the namespace"
            )
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

    # Reference note, not tied to any namespace: written/refreshed every
    # sync so an annotator always has the verdict guide at hand.
    _write_if_changed(vault_root / "templates" / "verdict-guide.md", render_template_note())

    return result


# --------------------------------------------------------------------------- #
# Annotate/override loop: read human edits in the vault back into the store
# --------------------------------------------------------------------------- #
# Semantics (see docs/obsidian-layer.md for the full writeup):
#   - A note's body (the text between "## Content" and "## Provenance") edited
#     to something other than what sync wrote is a CORRECTION: the old episode
#     is superseded by a new one carrying the edited text, marked
#     human-verified (never an inference, so it already outranks any
#     agent-inferred memory under the observed-beats-inferred consolidation
#     rule with no extra logic needed).
#   - `verdict: reject` in frontmatter is a REJECTION: the memory is forgotten
#     (a "forgotten" event, same terminal state consolidation's decay phase
#     produces). Rejection wins over a simultaneous body edit: dropping a
#     memory is a stronger, simpler signal than correcting text that is about
#     to be discarded anyway.
#   - `verdict: confirm` in frontmatter (with no body edit) is a PIN: a
#     "confirmed" event is logged. The episode stays active; consolidate's
#     decay phase skips any episode with a confirmed event, forever.
#   - A note with no body edit and no verdict is exactly what sync would
#     still write today: apply does nothing to it. This is the safety
#     property that makes round-tripping sync -> apply -> sync loss-free.
#   - Human edits are detected by comparing the body text against the
#     content_hash sync wrote into frontmatter (falling back to hashing the
#     episode's current content when a note predates content_hash). Because
#     episode content never changes in place, this is equivalent to comparing
#     against the store directly, but matches the note as sync actually wrote
#     it rather than requiring a second store read to decide "did this
#     change".
#   - Notes whose episode is no longer active (already superseded or
#     forgotten, by a previous apply run or by consolidate) are skipped, not
#     reprocessed: this is what makes re-running apply without an
#     intervening sync safe, and it is also why apply never invents a new
#     memory from a note whose id it does not recognize (the shared-notes /
#     staging-vault ideas are explicitly out of scope, see
#     docs/obsidian-layer.md).
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_FM_FIELD_RE = re.compile(r'^([A-Za-z_]+):\s*(.*)$')
_BODY_RE = re.compile(r"## Content\n\n(.*?)\n\n## Provenance", re.DOTALL)


@dataclass
class VaultApplyResult:
    corrected: int = 0
    rejected: int = 0
    confirmed: int = 0
    unchanged: int = 0
    skipped: int = 0
    # staging proposals (memsub consolidate --stage): kept as their own
    # counters, not folded into corrected/rejected, because a staged proposal
    # is a consolidation action (supersede/forget) the human accepted, not a
    # human edit to a memory note -- conflating the two would confuse the
    # `vault apply` summary line.
    staged_applied: int = 0
    staged_rejected: int = 0
    detail: list[str] = field(default_factory=list)  # ids only, never content


def _parse_frontmatter(raw: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        m = _FM_FIELD_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1].replace('\\"', '"')
        fields[key] = value
    return fields


def _parse_note(text: str) -> tuple[dict[str, str], str] | None:
    """Returns (frontmatter fields, body text) or None if not a memory note
    (e.g. an index note, which carries no frontmatter block)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    fields = _parse_frontmatter(m.group(1))
    body_m = _BODY_RE.search(m.group(2))
    body = body_m.group(1) if body_m else ""
    return fields, body


def _parse_note_frontmatter(text: str) -> dict[str, str] | None:
    """Returns just the frontmatter fields of a note, or None if it carries no
    frontmatter block. Used for staging proposal notes, which do not have the
    memory-note '## Content ... ## Provenance' body structure."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    return _parse_frontmatter(m.group(1))


def apply_vault(
    store: EpisodicStore,
    vault_path: str | Path,
    namespace: str | None = None,
    run_id: str = "vault-apply",
) -> VaultApplyResult:
    vault_root = Path(vault_path)
    result = VaultApplyResult()

    if namespace is not None:
        ns_dirs = [vault_root / namespace_dirname(namespace)]
    else:
        ns_dirs = sorted(
            d for d in vault_root.iterdir() if d.is_dir() and (d / "index.md").exists()
        ) if vault_root.exists() else []

    for ns_dir in ns_dirs:
        if not ns_dir.exists():
            continue
        for note_path in sorted(ns_dir.glob("*.md")):
            if note_path.name == "index.md":
                continue
            parsed = _parse_note(note_path.read_text(encoding="utf-8"))
            if parsed is None:
                result.skipped += 1
                result.detail.append(f"skip malformed {note_path.name}")
                continue
            fields, body = parsed
            ep_id = fields.get("id")
            if not ep_id:
                result.skipped += 1
                result.detail.append(f"skip no-id {note_path.name}")
                continue
            ep = store.get(ep_id)
            if ep is None:
                result.skipped += 1
                result.detail.append(f"skip orphan {ep_id[:8]}")
                continue
            if store.status(ep_id) != "active":
                result.skipped += 1
                result.detail.append(f"skip inactive {ep_id[:8]}")
                continue

            verdict = fields.get("verdict", "").strip().lower()
            baseline_hash = fields.get("content_hash") or content_hash(ep.content)
            edited = body != "" and content_hash(body) != baseline_hash

            if verdict == "reject":
                store.log_event(ep_id, "forgotten", {"reason": "human_reject"})
                result.rejected += 1
                result.detail.append(f"reject {ep_id[:8]}")
            elif edited:
                new_ep = store.append(
                    Episode(
                        content=body,
                        run_id=run_id,
                        session_id=ep.session_id,
                        kind=ep.kind,
                        importance=1.0,
                        namespace=ep.namespace,
                        provenance={
                            "origin": "human_correction",
                            "corrected_from": ep_id,
                            "human_verified": True,
                            "confidence": 1.0,
                        },
                    )
                )
                store.log_event(
                    ep_id, "superseded", {"reason": "human_correction", "by": new_ep.id}
                )
                result.corrected += 1
                result.detail.append(f"correct {ep_id[:8]} -> {new_ep.id[:8]}")
            elif verdict == "confirm":
                if store.events(episode_id=ep_id, event="confirmed"):
                    # already pinned (e.g. apply rerun before a resync removed
                    # the verdict from the note file): do not double-log
                    result.unchanged += 1
                else:
                    store.log_event(ep_id, "confirmed", {"reason": "human_confirm"})
                    result.confirmed += 1
                    result.detail.append(f"confirm {ep_id[:8]}")
            else:
                result.unchanged += 1

    # staging proposals (memsub consolidate --stage): execute accepted, drop
    # rejected. Kept separate from the memory-note loop above because a
    # proposal note has a different shape and semantics (an action to run, not
    # a memory to edit).
    _apply_staging(store, vault_root, namespace, result)

    return result


# --------------------------------------------------------------------------- #
# Staging: approve-before-apply for consolidation (memsub consolidate --stage)
# --------------------------------------------------------------------------- #
# Semantics (see docs/obsidian-layer.md second-layer idea #3, bead
# memory-sublayer-eie):
#   - `memsub consolidate --stage` computes the same actions dry-run would
#     (supersede/forget), but instead of applying them writes one pending
#     proposal note per action into <vault>/staging/<namespace>/. The store is
#     not touched. stage_actions reuses consolidate(dry_run=True)'s action
#     computation verbatim -- it adds no new action logic.
#   - A proposal note carries the target episode id, the action, the reason,
#     the action's detail fields (by/similarity/judge_model/value/age_days/
#     retrieved_count as applicable), status: pending, and an empty verdict
#     field for the human to fill in.
#   - Idempotency: a proposal's filename is derived from action+episode id, so
#     re-running --stage never writes a second note for an action already
#     staged. An existing proposal note (whatever its status) is left as-is,
#     so a prior human rejection is respected across re-stages.
#   - `memsub vault apply` scans staging/ in addition to memory notes, reusing
#     the same frontmatter parsing. verdict: accept executes the action for
#     real (logs exactly the "superseded"/"forgotten" event non-staged
#     consolidate would have logged) and marks the note applied; verdict:
#     reject drops it (marks the note rejected, never touches the episode); no
#     verdict leaves it pending. This generalizes apply's reject-wins
#     precedence to accept/reject on proposals.

# Action.detail keys and the types to restore them to when reconstructing the
# event detail on apply, so a staged-then-accepted action logs a detail dict
# with the same value types non-staged consolidate would have logged.
_STAGING_DETAIL_KEYS: dict[str, type] = {
    "by": str,
    "similarity": float,
    "judge_model": str,
    "value": float,
    "age_days": float,
    "retrieved_count": int,
}


@dataclass
class VaultStageResult:
    staged: int = 0  # new proposal notes written
    existing: int = 0  # proposal already staged (idempotent skip)
    skipped: int = 0  # action whose target episode is no longer active
    detail: list[str] = field(default_factory=list)


def staging_note_filename(action: str, episode_id: str) -> str:
    return f"{action}--{episode_id[:8]}.md"


def render_staging_note(action, store: EpisodicStore) -> str:
    """Render one pending proposal note for a consolidation Action. Includes
    the target memory's text (and, for supersedes, the winner's text) so the
    proposal can be judged in Obsidian without opening the store."""
    ep = store.get(action.episode_id)
    fields: dict = {
        "id": action.episode_id,
        "action": action.action,
        "reason": action.reason,
    }
    for key in _STAGING_DETAIL_KEYS:
        if key in action.detail:
            fields[key] = action.detail[key]
    fields["status"] = "pending"
    # _frontmatter drops empty values, but we want an explicit blank verdict
    # line so the human sees where to write accept/reject. Build it in: render
    # the populated fields, then append the empty verdict before the closing
    # fence.
    body_fm = _frontmatter(fields)
    frontmatter = body_fm[: -len("---")] + 'verdict: ""\n---'

    verb = "supersede" if action.action == "supersede" else "forget"
    lines = [
        frontmatter,
        "",
        f"# Proposal: {verb} {action.episode_id[:8]} ({action.reason})",
        "",
        "Consolidation proposes this action. To decide, set `verdict` in the "
        "frontmatter above to `accept` or `reject`, then run `memsub vault "
        "apply`. No verdict leaves the proposal pending (nothing happens).",
        "",
        "## Target memory",
        "",
        ep.content if ep else "(episode not found)",
    ]
    winner_id = action.detail.get("by")
    if winner_id:
        winner = store.get(winner_id)
        lines += [
            "",
            "## Superseding memory",
            "",
            winner.content if winner else f"(episode {winner_id[:8]} not found)",
        ]
    lines += [
        "",
        "## Details",
        "",
    ]
    for key in _STAGING_DETAIL_KEYS:
        if key in action.detail:
            lines.append(f"- {key}: {action.detail[key]}")
    return "\n".join(lines) + "\n"


def stage_actions(
    store: EpisodicStore, actions: list, vault_path: str | Path
) -> VaultStageResult:
    """Write each consolidation action as a pending proposal note under
    <vault>/staging/<namespace>/. Idempotent: an action already staged (same
    action+episode) is not re-written, and an action whose target episode is
    no longer active is skipped."""
    vault_root = Path(vault_path)
    result = VaultStageResult()
    for action in actions:
        ep = store.get(action.episode_id)
        if ep is None or store.status(action.episode_id) != "active":
            result.skipped += 1
            result.detail.append(f"skip inactive {action.episode_id[:8]}")
            continue
        ns_dir = vault_root / STAGING_DIRNAME / namespace_dirname(ep.namespace)
        note_path = ns_dir / staging_note_filename(action.action, action.episode_id)
        if note_path.exists():
            # A proposal for this action already exists (pending, accepted, or
            # rejected by a prior run). Leave it: re-staging must not clobber a
            # human's earlier verdict, and must not duplicate a pending note.
            result.existing += 1
            continue
        ns_dir.mkdir(parents=True, exist_ok=True)
        note_path.write_text(render_staging_note(action, store), encoding="utf-8")
        result.staged += 1
        result.detail.append(f"stage {action.action} {action.episode_id[:8]}")
    return result


def _reconstruct_detail(fields: dict[str, str]) -> dict:
    """Rebuild a consolidation Action.detail dict from a proposal note's parsed
    frontmatter, restoring the value types non-staged consolidate logged."""
    detail: dict = {}
    for key, caster in _STAGING_DETAIL_KEYS.items():
        if key in fields:
            try:
                detail[key] = caster(fields[key])
            except (TypeError, ValueError):
                detail[key] = fields[key]
    return detail


def _set_staging_status(path: Path, text: str, new_status: str) -> None:
    """Rewrite a proposal note's `status:` frontmatter line in place."""
    updated = re.sub(
        r'^status:\s*.*$', f'status: "{new_status}"', text, count=1, flags=re.MULTILINE
    )
    path.write_text(updated, encoding="utf-8")


def _apply_staging(
    store: EpisodicStore,
    vault_root: Path,
    namespace: str | None,
    result: VaultApplyResult,
) -> None:
    """Execute accepted proposals and drop rejected ones under staging/."""
    staging_root = vault_root / STAGING_DIRNAME
    if not staging_root.exists():
        return
    if namespace is not None:
        stage_dirs = [staging_root / namespace_dirname(namespace)]
    else:
        stage_dirs = sorted(d for d in staging_root.iterdir() if d.is_dir())

    for stage_dir in stage_dirs:
        if not stage_dir.exists():
            continue
        for note_path in sorted(stage_dir.glob("*.md")):
            text = note_path.read_text(encoding="utf-8")
            parsed = _parse_note_frontmatter(text)
            if parsed is None:
                result.skipped += 1
                result.detail.append(f"skip malformed staging {note_path.name}")
                continue
            if parsed.get("status", "").strip().lower() != "pending":
                # already applied or rejected by a prior run: inert
                continue
            ep_id = parsed.get("id")
            verdict = parsed.get("verdict", "").strip().lower()
            if not ep_id or verdict not in ("accept", "reject"):
                # pending with no decision yet: leave it
                continue
            if verdict == "reject":
                _set_staging_status(note_path, text, "rejected")
                result.staged_rejected += 1
                result.detail.append(f"staged reject {ep_id[:8]}")
                continue
            # accept
            if store.get(ep_id) is None or store.status(ep_id) != "active":
                # target already resolved elsewhere: mark done, do not re-log
                _set_staging_status(note_path, text, "applied")
                result.skipped += 1
                result.detail.append(f"skip inactive staged {ep_id[:8]}")
                continue
            action = parsed.get("action", "")
            event = "superseded" if action == "supersede" else "forgotten"
            detail = {"reason": parsed.get("reason", "consolidation")}
            detail.update(_reconstruct_detail(parsed))
            store.log_event(ep_id, event, detail)
            _set_staging_status(note_path, text, "applied")
            result.staged_applied += 1
            result.detail.append(f"staged apply {action} {ep_id[:8]}")


# --------------------------------------------------------------------------- #
# Human notes promotion: notes/*.md -> first-class memories
# --------------------------------------------------------------------------- #
# See docs/obsidian-layer.md, second-layer idea #1. `notes/` at the vault
# root is the human-authored side of the vault: free-form markdown the owner
# writes directly, never generated or rewritten by sync_vault. `promote_notes`
# is the explicit "absorb this into memory" action idea #1 called for --
# a note's id is never inferred, so nothing under notes/ becomes a memory
# without this being run.


@dataclass
class VaultPromoteResult:
    promoted: int = 0  # new note -> new episode
    updated: int = 0  # changed note -> old episode superseded, new one appended
    unchanged: int = 0  # note content matches what was already promoted
    skipped: int = 0  # empty note
    detail: list[str] = field(default_factory=list)


def promote_notes(
    store: EpisodicStore,
    vault_path: str | Path,
    namespace: str = "default",
    run_id: str = "vault-promote",
) -> VaultPromoteResult:
    """Turn every new or changed note under `<vault>/notes/` into a
    first-class memory episode, provenance `origin: human_authored`.

    Idempotent by content hash, the same mechanism `vault apply` uses to
    detect a human edit: a note whose text hasn't changed since its last
    promotion is skipped, so running promote repeatedly does not duplicate
    memories. A changed note supersedes its prior promoted episode with a
    new one carrying the updated text (append-only, same pattern as an
    apply correction).

    A promoted episode is never `kind="inference"` and never sets
    `provenance.inferred`, so `consolidate._is_inferred` already treats it
    as observed and the existing observed-beats-inferred rule ranks it
    above any agent-inferred memory with no new logic (mirrors how `vault
    apply` corrections outrank inferences).
    """
    vault_root = Path(vault_path)
    notes_dir = vault_root / NOTES_DIRNAME
    result = VaultPromoteResult()
    if not notes_dir.exists():
        return result

    # Most recent human_authored episode per source note, so a re-promote
    # can tell new / unchanged / changed apart.
    latest_by_note: dict[str, Episode] = {}
    for ep in store.active(namespace=namespace):
        source_note = ep.provenance.get("source_note")
        if source_note and ep.provenance.get("origin") == "human_authored":
            latest_by_note[source_note] = ep

    for note_path in sorted(notes_dir.rglob("*.md")):
        rel = note_path.relative_to(vault_root).as_posix()
        text = note_path.read_text(encoding="utf-8").strip()
        if not text:
            result.skipped += 1
            result.detail.append(f"skip empty {rel}")
            continue

        note_hash = content_hash(text)
        prior = latest_by_note.get(rel)
        if prior is not None and prior.provenance.get("source_content_hash") == note_hash:
            result.unchanged += 1
            continue

        new_ep = store.append(
            Episode(
                content=text,
                run_id=run_id,
                session_id=rel,
                kind="fact",
                importance=1.0,
                namespace=namespace,
                provenance={
                    "origin": "human_authored",
                    "source_note": rel,
                    "source_content_hash": note_hash,
                    "human_verified": True,
                },
            )
        )
        if prior is not None:
            store.log_event(
                prior.id, "superseded", {"reason": "human_note_updated", "by": new_ep.id}
            )
            result.updated += 1
            result.detail.append(f"update {rel} -> {new_ep.id[:8]}")
        else:
            result.promoted += 1
            result.detail.append(f"promote {rel} -> {new_ep.id[:8]}")

    return result


# --------------------------------------------------------------------------- #
# vault check: unresolved [[wikilinks]]
# --------------------------------------------------------------------------- #
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


@dataclass
class VaultCheckResult:
    checked: int = 0
    broken: list[tuple[str, str]] = field(default_factory=list)  # (source note relpath, target)

    @property
    def broken_count(self) -> int:
        return len(self.broken)


def check_vault(vault_path: str | Path) -> VaultCheckResult:
    """Scan every `.md` note in a vault for `[[wikilinks]]` whose target note
    does not exist.

    A link target resolves if it matches (a) a full relative path (as
    written by `sync_vault`'s namespace-prefixed MOC links, e.g.
    `work/index`), or (b) a bare filename found anywhere in the vault (as
    written by same-namespace note links, which carry no directory prefix
    and rely on Obsidian's basename search).

    Judgment call: a link whose target matches an existing *directory* name
    (with no matching note) is also treated as resolved, not broken. This is
    Obsidian's folder-note convention (a link to a folder, e.g. `[[work]]`,
    is valid even without a `work/work.md` note). memsub's own generated
    vault never produces this case (every namespace folder gets an
    `index.md`), but a human's own `notes/` folder could plausibly use it,
    and flagging it would be a false positive.
    """
    vault_root = Path(vault_path)
    result = VaultCheckResult()
    if not vault_root.exists():
        return result

    md_files = sorted(vault_root.rglob("*.md"))
    by_relpath: set[str] = set()
    by_basename: dict[str, list[str]] = defaultdict(list)
    for f in md_files:
        rel = f.relative_to(vault_root).with_suffix("").as_posix()
        by_relpath.add(rel)
        by_basename[f.stem].append(rel)
    dirnames = {
        d.relative_to(vault_root).as_posix() for d in vault_root.rglob("*") if d.is_dir()
    }

    for f in md_files:
        rel_source = f.relative_to(vault_root).as_posix()
        text = f.read_text(encoding="utf-8")
        for m in _WIKILINK_RE.finditer(text):
            target = m.group(1).strip()
            result.checked += 1
            if target in by_relpath or target in dirnames:
                continue
            if "/" not in target and target in by_basename:
                continue
            result.broken.append((rel_source, target))

    return result
