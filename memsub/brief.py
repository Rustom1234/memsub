"""Morning brief: a story-time digest of one day, with receipts.

F2 of the overnight build. Prospective reflection productized (RMM lineage,
docs/research-memory-landscape-2026-07-21.md): every morning the vault can
answer "what happened yesterday, what did I decide, what is still open"
from story time, each line carrying the episode id it came from. The LLM
(local qwen3:8b by default) only writes a 1-2 sentence summary over facts
it is handed; without a backend the brief is a deterministic outline. It
never invents content: the facts ARE the episode contents.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from .store import Episode, EpisodicStore

_SECTION_FOR_KIND = {
    "decision": "decisions",
    "milestone": "decisions",
    "idea": "ideas_open_loops",
}

SUMMARY_SYSTEM = """You write a one-to-two sentence plain-English summary of a person's working day from the factual notes given. Mention only what the notes state. No advice, no invented details, no lists. Output the sentence(s) only."""


@dataclass
class DayBrief:
    day: datetime.date
    sections: dict[str, list[Episode]] = field(default_factory=dict)
    summary: str = ""

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.sections.values())


def day_bounds(day: datetime.date) -> tuple[float, float]:
    start = datetime.datetime.combine(day, datetime.time.min).timestamp()
    end = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time.min).timestamp()
    return start, end


def build_brief(
    store: EpisodicStore,
    day: datetime.date,
    namespace: str | None = None,
    backend=None,
    max_summary_facts: int = 30,
) -> DayBrief:
    lo, hi = day_bounds(day)
    eps = store.active(namespace=namespace) if namespace else store.active()
    day_eps = sorted((e for e in eps if lo <= e.ts < hi), key=lambda e: e.ts)

    sections: dict[str, list[Episode]] = {"decisions": [], "events": [], "ideas_open_loops": []}
    for ep in day_eps:
        sections[_SECTION_FOR_KIND.get(ep.kind, "events")].append(ep)

    brief = DayBrief(day=day, sections=sections)
    if backend is not None and day_eps:
        facts = "\n".join(f"- {e.content}" for e in day_eps[:max_summary_facts])
        try:
            brief.summary = backend.complete(
                SUMMARY_SYSTEM, [{"role": "user", "content": facts}]
            ).strip()
        except Exception:
            brief.summary = ""  # summary is decoration; the outline is the brief
    return brief


def render_brief_markdown(brief: DayBrief) -> str:
    lines = [f"# Morning brief: {brief.day.isoformat()}", ""]
    if brief.total == 0:
        lines.append("No memories recorded for this day.")
        return "\n".join(lines) + "\n"
    if brief.summary:
        lines += [brief.summary, ""]
    titles = [
        ("decisions", "Decisions"),
        ("events", "What happened"),
        ("ideas_open_loops", "Ideas and open loops"),
    ]
    for key, title in titles:
        eps = brief.sections.get(key, [])
        if not eps:
            continue
        lines.append(f"## {title}")
        for ep in eps:
            t = datetime.datetime.fromtimestamp(ep.ts).strftime("%H:%M")
            lines.append(f"- {t} {ep.content}  `[{ep.id[:12]}]`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
