"""Layer 1: distillation (the librarian).

Design source: docs/second-brain-spec-2026-07-20.md Layer 1, itself
Rustom's complaint about "a mess of claude notes". A nightly pass (local
qwen3:8b only) that clusters active raw memories into topics and writes one
evergreen note per topic at vault/topics/<slug>.md. Full assumptions and
success criteria: WORKLOG.md, 2026-07-20 "Second brain Layers 1 + 4" entry.

Pipeline, in order:

1. `cluster_topics`: seed candidate groups from the entity co-occurrence
   graph memsub/graph.py already writes (entities/entity_links tables,
   populated by `memsub graph backfill`, a prerequisite this module never
   runs itself), then refine large candidates by lexical similarity
   (memsub.lexical.tokenize + Jaccard) so an over-connected "hub" entity
   cannot merge unrelated notes into one giant topic. Deterministic: same
   store state always produces the same clusters.
2. `generate_topic_content`: exactly one qwen3:8b call per *changed* topic,
   asking for a title, one-paragraph overview, a same-length same-order
   rewrite of each member's raw content as a clean fact sentence, and a
   short open-questions list. The LLM never controls which note a fact
   links to -- every wikilink is assembled deterministically by index
   position, never parsed out of LLM prose. A missing/malformed LLM field
   falls back to a plain deterministic rewrite, so the note is never
   incomplete even with backend=None (dry runs, tests).
3. `distill`: idempotent write loop. A topic's `member_hash` (episode id
   set fingerprint; episodes are immutable once written, so the id set
   alone is a sufficient fingerprint) is compared against what's already on
   disk; unchanged topics cost zero LLM calls and are not even opened for a
   write. Human sovereignty: the machine-generated body lives between two
   fixed markers; text outside them is preserved verbatim on regeneration,
   and any frontmatter field the machine doesn't own is carried forward
   unchanged. `verdict: reject` deletes the topic note outright (mirrors
   memsub/vault.py apply_vault's existing verdict:reject semantics for
   episode notes).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import provenance as provenance_mod
from .backends import LLMBackend
from .graph import entity_id as graph_entity_id
from .graph import normalize_entity
from .lexical import tokenize
from .sleep import _extract_entities as extract_entities_rule_based
from .store import Episode, EpisodicStore
from .vault import note_wikilink, slugify

HUB_CAP = 30  # an entity linking more episodes than this is too generic to
# trust as a clustering edge (a frequent speaker name would otherwise chain
# unrelated episodes into one giant component) -- same failure class
# memsub/vault_notes.py::is_content_note and vault_graph.py's hub-cap guard
# against elsewhere in this codebase; tuned by inspection against the real
# store, not a labeled set.
REFINE_THRESHOLD = 8  # candidate components larger than this get lexically
# refined (split, never merged across candidates).
LEXICAL_JACCARD_THRESHOLD = 0.12  # tuned by inspection, same footing as
# memsub/graph.py's NEAR_MISS_THRESHOLD.
MIN_TOPIC_SIZE = 3  # spec: "a topic needs 3+ memories"

MACHINE_BEGIN = "<!-- memsub:distill:begin -->"
MACHINE_END = "<!-- memsub:distill:end -->"

# frontmatter fields the machine always regenerates; everything else already
# in an existing note's frontmatter (including a human's own added fields)
# is carried forward unchanged. `created` is special-cased below: preserved
# from the existing note (first-write time), never regenerated.
_MACHINE_OWNED_FIELDS = {
    "type", "namespace", "slug", "member_count", "member_hash", "entities", "updated",
}

DISTILL_SYSTEM = """You are a librarian distilling raw memory notes into one clean, \
evergreen topic note in the Zettelkasten style: plain, confident English, short. \
Never use the words "note", "episode", "session", "extraction", or any pipeline \
jargon in your output -- write as if you already know this topic well and are \
explaining it to a friend.

You will be given a numbered list of raw notes about one topic. Output ONLY a JSON \
object with these exact keys:

{"title": "<short prose topic title, 3-8 words, no surrounding quotes>",
 "overview": "<one paragraph, 2-4 plain-English sentences summarizing the topic>",
 "facts": ["<one rewritten sentence per raw note, SAME ORDER, SAME COUNT as the input, plain prose, no speaker prefixes>"],
 "open_questions": ["<a question the notes imply but do not answer>", ...]}

open_questions may be an empty list if nothing is genuinely open. Output nothing \
but the JSON object."""


@dataclass
class Topic:
    members: list[Episode]
    top_entities: list[str] = field(default_factory=list)

    @property
    def member_ids(self) -> list[str]:
        return [ep.id for ep in self.members]

    def member_hash(self) -> str:
        return hashlib.sha256(",".join(sorted(self.member_ids)).encode("utf-8")).hexdigest()[:16]

    def slug_seed(self) -> str:
        return self.top_entities[0] if self.top_entities else self.members[0].content[:40]


@dataclass(frozen=True)
class DistillResult:
    topics_found: int
    topics_written: int
    topics_skipped_unchanged: int
    topics_killed: int
    llm_calls: int
    slugs: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Clustering
# --------------------------------------------------------------------------- #
class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _build_entity_index(store: EpisodicStore, episodes: list[Episode]) -> dict[str, set[str]]:
    """entity_id -> set of episode ids, read from the entities/entity_links
    tables memsub/graph.py's write path already populated. This module never
    writes entities itself -- `memsub graph backfill` is a documented
    prerequisite (WORKLOG.md assumption 8)."""
    index: dict[str, set[str]] = defaultdict(set)
    for ep in episodes:
        for ent in store.entities_for_episode(ep.id):
            index[ent.id].add(ep.id)
    return index


def _entity_components(entity_index: dict[str, set[str]], episode_ids: list[str]) -> list[set[str]]:
    uf = _UnionFind(episode_ids)
    for members in entity_index.values():
        if len(members) < 2 or len(members) > HUB_CAP:
            continue  # too rare to link anything, or too generic (hub) to trust
        ordered = sorted(members)
        first = ordered[0]
        for other in ordered[1:]:
            uf.union(first, other)
    groups: dict[str, set[str]] = defaultdict(set)
    for eid in episode_ids:
        groups[uf.find(eid)].add(eid)
    return [g for g in groups.values() if len(g) >= 2]


def _lexical_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _refine_component(
    component_ids: list[str], by_id: dict[str, Episode], threshold: int = REFINE_THRESHOLD
) -> list[set[str]]:
    """Split (never merge across candidates) an entity-seeded component that
    is large enough that a generic shared entity might be doing the work
    instead of genuine topical similarity."""
    if len(component_ids) <= threshold:
        return [set(component_ids)]
    tokens = {eid: set(tokenize(by_id[eid].content)) for eid in component_ids}
    uf = _UnionFind(component_ids)
    for i in range(len(component_ids)):
        for j in range(i + 1, len(component_ids)):
            a, b = component_ids[i], component_ids[j]
            if _lexical_jaccard(tokens[a], tokens[b]) >= LEXICAL_JACCARD_THRESHOLD:
                uf.union(a, b)
    groups: dict[str, set[str]] = defaultdict(set)
    for eid in component_ids:
        groups[uf.find(eid)].add(eid)
    return list(groups.values())


def _top_entities_for(member_ids: set[str], entity_index: dict[str, set[str]]) -> list[str]:
    scored = [
        (len(members & member_ids), entity)
        for entity, members in entity_index.items()
        if len(members & member_ids) >= 2
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [e.split("::", 1)[1] if "::" in e else e for _, e in scored[:5]]


def cluster_topics(
    store: EpisodicStore, namespace: str | None = None, min_size: int = MIN_TOPIC_SIZE
) -> list[Topic]:
    """Deterministic entity-seeded, lexically-refined clustering over active
    episodes. Same store state always produces the same topics (sort keys
    are all stable: episode id, entity id text)."""
    episodes = store.active(namespace=namespace)
    by_id = {ep.id: ep for ep in episodes}
    entity_index = _build_entity_index(store, episodes)
    components = _entity_components(entity_index, sorted(by_id.keys()))
    topics: list[Topic] = []
    for comp in components:
        for sub in _refine_component(sorted(comp), by_id):
            if len(sub) < min_size:
                continue
            members = sorted((by_id[eid] for eid in sub), key=lambda ep: (ep.ts, ep.id))
            topics.append(Topic(members=members, top_entities=_top_entities_for(sub, entity_index)))
    topics.sort(key=lambda t: (-len(t.members), t.slug_seed().lower()))
    return topics


# --------------------------------------------------------------------------- #
# LLM content generation (facts/title/overview/open questions)
# --------------------------------------------------------------------------- #
_SPEAKER_PREFIX_RE = re.compile(r"^[A-Z][a-zA-Z]*: (.*)$")


def _fallback_fact(ep: Episode) -> str:
    """Deterministic, zero-LLM rewrite: strip a leading speaker prefix, cap
    length. Used whenever a backend is unavailable, or the LLM's facts
    array is missing/malformed/mismatched-length for a given index -- a
    topic note is never left incomplete."""
    m = _SPEAKER_PREFIX_RE.match(ep.content)
    text = m.group(1) if m else ep.content
    text = " ".join(text.split())
    return text if len(text) <= 220 else text[:220].rstrip() + "..."


def _distill_prompt(topic: Topic) -> str:
    return "\n".join(f"[{i}] {ep.content}" for i, ep in enumerate(topic.members))


def _parse_json_object(response: str) -> dict:
    start, end = response.find("{"), response.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(response[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def generate_topic_content(topic: Topic, backend: LLMBackend | None) -> dict:
    """Returns {"title", "overview", "facts" (one per member, same order),
    "open_questions", "llm_calls"}. facts/title/overview always fall back to
    deterministic content when backend is None or the LLM output is
    malformed, so this never raises and never drops a member."""
    facts = [_fallback_fact(ep) for ep in topic.members]
    title = topic.top_entities[0].title() if topic.top_entities else "Untitled topic"
    overview = ""
    open_questions: list[str] = []
    llm_calls = 0
    if backend is not None:
        response = backend.complete(DISTILL_SYSTEM, [{"role": "user", "content": _distill_prompt(topic)}])
        llm_calls = 1
        data = _parse_json_object(response)
        if str(data.get("title") or "").strip():
            title = str(data["title"]).strip()
        if str(data.get("overview") or "").strip():
            overview = " ".join(str(data["overview"]).split())
        llm_facts = data.get("facts")
        if isinstance(llm_facts, list) and len(llm_facts) == len(facts):
            facts = [str(f).strip() or facts[i] for i, f in enumerate(llm_facts)]
        oq = data.get("open_questions")
        if isinstance(oq, list):
            open_questions = [str(q).strip() for q in oq if str(q).strip()][:4]
    if not overview:
        who = ", ".join(topic.top_entities[:2]) or "this topic"
        overview = f"Notes about {who}, gathered from {len(topic.members)} memories."
    return {
        "title": title,
        "overview": overview,
        "facts": facts,
        "open_questions": open_questions,
        "llm_calls": llm_calls,
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _fmt_date(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _build_timeline_lines(topic: Topic, facts: list[str]) -> list[str]:
    dated = [(ep, fact) for ep, fact in zip(topic.members, facts) if ep.valid_from]
    if not dated:
        return []
    dated.sort(key=lambda pair: pair[0].valid_from)
    lines = ["## Timeline", ""]
    for ep, fact in dated:
        when = _fmt_date(ep.valid_from)
        if ep.valid_to and ep.valid_to != ep.valid_from:
            when = f"{when} to {_fmt_date(ep.valid_to)}"
        lines.append(f"- {when}: {fact} [[{note_wikilink(ep)}]]")
    lines.append("")
    return lines


def render_machine_block(topic: Topic, content: dict) -> str:
    lines = [MACHINE_BEGIN, "", f"# {content['title']}", "", content["overview"], "", "## Key facts", ""]
    for ep, fact in zip(topic.members, content["facts"]):
        lines.append(f"- {fact} [[{note_wikilink(ep)}]]")
    lines.append("")
    lines += _build_timeline_lines(topic, content["facts"])
    lines += ["## Open questions", ""]
    if content["open_questions"]:
        lines += [f"- {q}" for q in content["open_questions"]]
    else:
        lines.append("- (none yet)")
    lines += ["", MACHINE_END]
    return "\n".join(lines)


def _yaml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{str(value).replace(chr(34), chr(92) + chr(34))}"'


def _render_frontmatter(fields: dict) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _split_existing(text: str) -> tuple[dict[str, str], str, str]:
    """(frontmatter_fields, pre_machine_text, post_machine_text) for an
    existing topic note. A note with no frontmatter fence or no machine
    markers is treated as fully human-owned: fields={}, pre=whole body,
    post="" -- regeneration then leaves it untouched (member_hash check
    upstream still governs whether we even get here)."""
    fields: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_block = text[3:end]
            nl = text.find("\n", end + 1)
            body = text[nl + 1 :] if nl != -1 else ""
            for line in fm_block.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fields[k.strip()] = v.strip().strip('"')
    begin_idx = body.find(MACHINE_BEGIN)
    end_idx = body.find(MACHINE_END)
    if begin_idx == -1 or end_idx == -1 or end_idx < begin_idx:
        return fields, body, ""
    return fields, body[:begin_idx], body[end_idx + len(MACHINE_END) :]


def _merge_frontmatter(existing: dict[str, str], fresh: dict) -> dict:
    merged = dict(fresh)
    if "created" in existing:
        merged["created"] = existing["created"]
    for k, v in existing.items():
        if k in _MACHINE_OWNED_FIELDS or k == "created":
            continue
        merged.setdefault(k, v)
    return merged


def topic_frontmatter(topic: Topic, slug: str, now_iso: str) -> dict:
    return {
        "type": "topic",
        "namespace": "topics",
        "slug": slug,
        "member_count": len(topic.members),
        "member_hash": topic.member_hash(),
        "entities": ", ".join(topic.top_entities[:5]),
        "created": now_iso,
        "updated": now_iso,
    }


def _write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def _unique_slug(seed: str, member_hash: str, used: dict[str, int]) -> str:
    base = slugify(seed)
    if base not in used:
        used[base] = 1
        return base
    used[base] += 1
    return f"{base}-{member_hash[:6]}"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def distill(
    store: EpisodicStore,
    vault_path: str | Path,
    backend: LLMBackend | None = None,
    namespace: str | None = None,
    min_size: int = MIN_TOPIC_SIZE,
    dry_run: bool = False,
) -> DistillResult:
    """Cluster active episodes into topics and write/update
    vault/topics/<slug>.md for each, idempotently (unchanged topics cost
    zero LLM calls and are not opened for a write). `verdict: reject` in an
    existing topic note's frontmatter deletes that note instead of
    regenerating it."""
    topics_dir = Path(vault_path) / "topics"
    topics = cluster_topics(store, namespace=namespace, min_size=min_size)
    used_slugs: dict[str, int] = {}
    written = skipped = killed = llm_calls = 0
    slugs_out: list[str] = []
    now = time.time()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))

    for topic in topics:
        slug = _unique_slug(topic.slug_seed(), topic.member_hash(), used_slugs)
        path = topics_dir / f"{slug}.md"
        existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
        existing_fields, pre, post = _split_existing(existing_text) if existing_text else ({}, "", "")

        if existing_fields.get("verdict", "").strip().lower() == "reject":
            killed += 1
            if not dry_run and path.exists():
                path.unlink()
            continue

        if existing_fields.get("member_hash") == topic.member_hash():
            skipped += 1
            slugs_out.append(slug)
            continue

        content = generate_topic_content(topic, backend)
        llm_calls += content["llm_calls"]
        machine_block = render_machine_block(topic, content)
        fields = _merge_frontmatter(existing_fields, topic_frontmatter(topic, slug, now_iso))
        body_text = f"{pre}{machine_block}{post}"
        if not body_text.endswith("\n"):
            body_text += "\n"
        full_text = f"{_render_frontmatter(fields)}\n\n{body_text}"

        if dry_run:
            written += 1
        elif _write_if_changed(path, full_text):
            written += 1
        slugs_out.append(slug)

    return DistillResult(
        topics_found=len(topics),
        topics_written=written,
        topics_skipped_unchanged=skipped,
        topics_killed=killed,
        llm_calls=llm_calls,
        slugs=slugs_out,
    )


# --------------------------------------------------------------------------- #
# Layer 4: deliberate idea capture
# --------------------------------------------------------------------------- #
def write_idea(
    store: EpisodicStore,
    content: str,
    namespace: str = "default",
    run_id: str = "idea-cli",
    session_id: str = "idea-cli",
    key: bytes | None = None,
) -> Episode:
    """Instant deliberate capture: a user_stated, provenance-signed episode
    written immediately, bypassing the 30-min extraction wait. kind="idea"
    -- memsub/vault.py::render_note already writes
    frontmatter_fields["kind"] = ep.kind unconditionally, and
    memsub/vault_serve.py::_render_kind_badges already renders any
    non-default kind as a small chip, so the "distinct badge in vault"
    requirement needs zero changes to vault.py or the vault_serve family
    (WORKLOG.md assumption 5: reuse over invention)."""
    ep = Episode(
        content=content,
        run_id=run_id,
        session_id=session_id,
        kind="idea",
        provenance={"origin": "memsub_idea_cli"},
        namespace=namespace,
    )
    ep = provenance_mod.tag_episode(ep, provenance_mod.CHANNEL_USER_STATED, key=key)
    return store.append(ep)


def _topic_title_from_text(text: str) -> str | None:
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            body = text[nl + 1 :] if nl != -1 else ""
    for line in body.splitlines():
        m = re.match(r"^#\s+(.*)$", line)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def match_entities_and_topics(
    store: EpisodicStore,
    content: str,
    namespace: str,
    vault_path: str | Path | None = None,
) -> tuple[list[str], list[str]]:
    """Cheap (LLM-free) echo for `memsub idea`: rule-based entity extraction
    (memsub.sleep._extract_entities, the same extractor memsub/graph.py's
    write path already uses) matched against store.entities by exact
    normalized id -- no fuzzy difflib pass, no LLM call. Topic matches are a
    plain-text scan of vault/topics/*.md frontmatter for an `entities` list
    that intersects. Returns (matched_entities, matched_topic_titles)."""
    texts = extract_entities_rule_based(content)
    matched_entities = [
        text for text in sorted(texts)
        if store.get_entity(graph_entity_id(namespace, normalize_entity(text))) is not None
    ]

    matched_topics: list[str] = []
    if vault_path is not None:
        topics_dir = Path(vault_path) / "topics"
        wanted = {normalize_entity(t) for t in matched_entities}
        if wanted and topics_dir.is_dir():
            for f in sorted(topics_dir.glob("*.md")):
                try:
                    text = f.read_text(encoding="utf-8")
                except OSError:
                    continue
                fields, _, _ = _split_existing(text)
                topic_entities = {
                    normalize_entity(e) for e in fields.get("entities", "").split(",") if e.strip()
                }
                if wanted & topic_entities:
                    matched_topics.append(_topic_title_from_text(text) or f.stem)
    return matched_entities, matched_topics
