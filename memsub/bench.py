"""Benchmark harness: policy layer vs naive baselines under a hard token budget.

Two evaluations share the machinery here:

Part A, recall@budget: seeded synthetic memory sets with known ground truth
(fact families where later versions supersede earlier ones, plus low-value
distractors). A query is correct when the latest version of the queried fact
made it into the injected context under the budget; stale when only an
outdated version did.

Part B, LOCOMO evidence recall: the LOCOMO long-conversation QA set (Maharana
et al. 2024), turn-level ingestion. A question scores when the annotated
evidence turns are present in the injected context under the budget.

Systems compared (one interface, ingest + inject):

  policy        relevance x recency x importance scoring, greedy packing
  policy+cons   same, after an offline consolidation pass (dedup + decay)
  naive-topk    pure cosine relevance, greedy packing (store everything)
  recent        newest-first packing, no semantic search

The token budget is a hard cap using the chars/4 approximation from
memsub.policy. Every number reported from this module comes from an executed
run; nothing is estimated.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from .consolidate import ConsolidationConfig, consolidate
from .embed import Embedder, HashingEmbedder
from .policy import approx_tokens
from .store import Episode, EpisodicStore


def pack_greedy(episodes: list[Episode], budget_tokens: int) -> list[Episode]:
    """Pack episodes in the given priority order under a hard token budget."""
    packed: list[Episode] = []
    used = 0
    for ep in episodes:
        cost = approx_tokens(ep.content) + 1
        if used + cost > budget_tokens:
            continue
        packed.append(ep)
        used += cost
    return packed


class BenchSystem(Protocol):
    name: str

    def ingest(self, episodes: list[Episode]) -> None: ...
    def inject(self, query: str, budget_tokens: int) -> list[Episode]: ...


class _Index:
    """Minimal cosine index used by the bench systems."""

    def __init__(self, embedder: Embedder | None = None) -> None:
        self.embedder = embedder or HashingEmbedder()
        self.episodes: list[Episode] = []
        self._vectors: list[np.ndarray] = []

    def add(self, episodes: list[Episode]) -> None:
        for ep in episodes:
            self.episodes.append(ep)
            self._vectors.append(self.embedder.embed(ep.content))

    def scores(self, query: str) -> np.ndarray:
        qvec = self.embedder.embed(query)
        return np.stack(self._vectors) @ qvec


class PolicySystem:
    """The policy layer: relevance x recency x importance, greedy packing."""

    def __init__(self, recency_weight: float = 0.995, consolidated: bool = False) -> None:
        self.name = "policy+cons" if consolidated else "policy"
        self.recency_weight = recency_weight
        self.consolidated = consolidated
        self._index = _Index()

    def ingest(self, episodes: list[Episode]) -> None:
        if self.consolidated:
            store = EpisodicStore(":memory:")
            for ep in episodes:
                store.append(ep)
            # dedup only: decay against wall-clock time is meaningless for
            # benchmark datasets with synthetic timestamps
            consolidate(store, backend=None, config=ConsolidationConfig(decay_value_threshold=0.0))
            episodes = store.active()
            store.close()
        self._index.add(sorted(episodes, key=lambda e: e.ts))

    def inject(self, query: str, budget_tokens: int) -> list[Episode]:
        eps = self._index.episodes
        if not eps:
            return []
        sims = self._index.scores(query)
        n = len(eps)
        ranks = np.arange(n - 1, -1, -1, dtype=np.float64)  # newest gets rank 0
        recency = self.recency_weight**ranks
        importance = np.array([ep.importance for ep in eps])
        scores = sims * recency * importance
        order = np.argsort(-scores, kind="stable")
        return pack_greedy([eps[i] for i in order], budget_tokens)


class NaiveTopKSystem:
    """Store everything, rank by cosine relevance only, pack top-k first."""

    name = "naive-topk"

    def __init__(self) -> None:
        self._index = _Index()

    def ingest(self, episodes: list[Episode]) -> None:
        self._index.add(episodes)

    def inject(self, query: str, budget_tokens: int) -> list[Episode]:
        eps = self._index.episodes
        if not eps:
            return []
        order = np.argsort(-self._index.scores(query), kind="stable")
        return pack_greedy([eps[i] for i in order], budget_tokens)


class RecentFirstSystem:
    """No search at all: newest memories first until the budget is full."""

    name = "recent"

    def __init__(self) -> None:
        self._episodes: list[Episode] = []

    def ingest(self, episodes: list[Episode]) -> None:
        self._episodes.extend(episodes)

    def inject(self, query: str, budget_tokens: int) -> list[Episode]:
        ordered = sorted(self._episodes, key=lambda e: e.ts, reverse=True)
        return pack_greedy(ordered, budget_tokens)


def build_systems(consolidated_row: bool = True) -> list[BenchSystem]:
    systems: list[BenchSystem] = [PolicySystem()]
    if consolidated_row:
        systems.append(PolicySystem(consolidated=True))
    systems.extend([NaiveTopKSystem(), RecentFirstSystem()])
    return systems


# --------------------------------------------------------------------------- #
# Part A: synthetic recall@budget with ground truth
# --------------------------------------------------------------------------- #
_ADJ = ["amber", "cobalt", "crimson", "ivory", "jade", "onyx", "sable", "teal", "umber", "violet"]
_NOUN = ["gateway", "indexer", "ledger", "mailer", "parser", "queue", "router", "scheduler", "tracker", "worker"]
_ATTR = ["deploy port", "primary region", "owner team", "current version", "log level", "backup schedule", "api quota", "retry limit"]
_CHIT = [
    "we chatted about the weather being {w} again",
    "lunch order was {w} bowls from the corner place",
    "someone mentioned the {w} conference happening downtown",
    "long thread about which {w} playlist is best for focus",
    "reminder that the office plants need watering on {w}",
]
_CHIT_W = ["rainy", "sunny", "noodle", "salad", "devops", "design", "jazz", "synth", "monday", "friday"]


@dataclass(frozen=True)
class RecallQuery:
    question: str
    gold: str  # value that must appear in the injected context
    stale: tuple[str, ...]  # superseded values for the same fact
    gold_episode_id: str


@dataclass
class RecallDataset:
    episodes: list[Episode] = field(default_factory=list)
    queries: list[RecallQuery] = field(default_factory=list)


def generate_recall_dataset(
    seed: int,
    n_facts: int = 30,
    n_versions: int = 3,
    n_distractors: int = 120,
) -> RecallDataset:
    """Seeded synthetic dataset: fact families with updates + distractors."""
    rng = random.Random(seed)
    ds = RecallDataset()
    t0 = 1_700_000_000.0
    tick = [0]

    def next_ts() -> float:
        tick[0] += 1
        return t0 + tick[0] * 3600.0 + rng.random() * 60

    subjects = rng.sample([f"the {a}-{n} service" for a in _ADJ for n in _NOUN], n_facts)
    pending: list[Episode] = []
    for fi, subject in enumerate(subjects):
        attr = rng.choice(_ATTR)
        values = rng.sample(range(1000, 9999), n_versions)
        gold_id = ""
        stale_vals: list[str] = []
        for vi, val in enumerate(values):
            content = f"The {attr} of {subject} is {val}."
            ep = Episode(
                content=content,
                run_id="bench",
                session_id=f"s{fi}",
                kind="fact",
                importance=round(rng.uniform(0.8, 0.95), 3),
                ts=next_ts(),
                id=f"fact-{fi}-v{vi}",
            )
            pending.append(ep)
            if vi == n_versions - 1:
                gold_id = ep.id
            else:
                stale_vals.append(str(val))
        ds.queries.append(
            RecallQuery(
                question=f"What is the {attr} of {subject}?",
                gold=str(values[-1]),
                stale=tuple(stale_vals),
                gold_episode_id=gold_id,
            )
        )
    for di in range(n_distractors):
        content = rng.choice(_CHIT).format(w=rng.choice(_CHIT_W))
        pending.append(
            Episode(
                content=content,
                run_id="bench",
                session_id="chit",
                kind="event",
                importance=round(rng.uniform(0.15, 0.4), 3),
                ts=next_ts(),
                id=f"chit-{di}",
            )
        )
    rng.shuffle(pending)
    # timestamps stay as assigned; shuffle only changes list order
    ds.episodes = pending
    return ds


@dataclass
class RecallResultRow:
    system: str
    budget: int
    n: int
    correct: int
    stale_only: int
    avg_tokens_used: float

    @property
    def recall(self) -> float:
        return self.correct / self.n if self.n else 0.0

    @property
    def stale_rate(self) -> float:
        return self.stale_only / self.n if self.n else 0.0


def run_recall_benchmark(
    seeds: list[int],
    budgets: list[int],
    systems_factory=build_systems,
) -> list[RecallResultRow]:
    """Execute part A. Returns one row per (system, budget), aggregated over seeds."""
    acc: dict[tuple[str, int], list[int | float]] = {}
    for seed in seeds:
        ds = generate_recall_dataset(seed)
        for system in systems_factory():
            system.ingest(list(ds.episodes))
            for budget in budgets:
                key = (system.name, budget)
                cell = acc.setdefault(key, [0, 0, 0, 0.0])  # n, correct, stale, tokens
                for q in ds.queries:
                    packed = system.inject(q.question, budget)
                    text = "\n".join(ep.content for ep in packed)
                    used = sum(approx_tokens(ep.content) + 1 for ep in packed)
                    gold_in = any(ep.id == q.gold_episode_id for ep in packed)
                    stale_in = any(s in text for s in q.stale)
                    cell[0] += 1
                    cell[1] += int(gold_in)
                    cell[2] += int(stale_in and not gold_in)
                    cell[3] += used
    rows = []
    for (name, budget), (n, correct, stale, tokens) in sorted(acc.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        rows.append(
            RecallResultRow(
                system=name, budget=budget, n=n, correct=correct,
                stale_only=stale, avg_tokens_used=round(tokens / n, 1),
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# Part B: LOCOMO evidence recall
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LocomoQA:
    conversation_id: str
    question: str
    answer: str
    evidence: tuple[str, ...]  # dia_ids of gold evidence turns
    category: int


@dataclass
class LocomoConversation:
    conversation_id: str
    episodes: list[Episode] = field(default_factory=list)
    qa: list[LocomoQA] = field(default_factory=list)


def load_locomo(path: str | Path) -> list[LocomoConversation]:
    """Parse the LOCOMO dataset into turn-level episodes + QA with evidence ids."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[LocomoConversation] = []
    for item in data:
        cid = str(item.get("sample_id", len(out)))
        conv = LocomoConversation(conversation_id=cid)
        conversation = item.get("conversation", {})
        session_idx = 0
        ts = 0.0
        while True:
            session_idx += 1
            turns = conversation.get(f"session_{session_idx}")
            if turns is None:
                break
            for turn in turns:
                ts += 1.0
                text = str(turn.get("text", "")).strip()
                if not text:
                    continue
                dia_id = str(turn.get("dia_id", ""))
                conv.episodes.append(
                    Episode(
                        content=f"{turn.get('speaker', '?')}: {text}",
                        run_id=cid,
                        session_id=f"session_{session_idx}",
                        kind="turn",
                        importance=1.0,
                        ts=ts,
                        id=f"{cid}:{dia_id}",
                    )
                )
        for q in item.get("qa", []):
            raw_ev = q.get("evidence", [])
            if isinstance(raw_ev, str):
                try:
                    raw_ev = json.loads(raw_ev.replace("'", '"'))
                except json.JSONDecodeError:
                    raw_ev = []
            evidence = tuple(str(e) for e in raw_ev)
            try:
                category = int(q.get("category", -1))
            except (TypeError, ValueError):
                category = -1
            conv.qa.append(
                LocomoQA(
                    conversation_id=cid,
                    question=str(q.get("question", "")),
                    answer=str(q.get("answer", "")),
                    evidence=evidence,
                    category=category,
                )
            )
        out.append(conv)
    return out


@dataclass
class LocomoResultRow:
    system: str
    budget: int
    n: int
    all_evidence_hit: int
    any_evidence_hit: int
    avg_tokens_used: float

    @property
    def recall_all(self) -> float:
        return self.all_evidence_hit / self.n if self.n else 0.0

    @property
    def recall_any(self) -> float:
        return self.any_evidence_hit / self.n if self.n else 0.0


def run_locomo_benchmark(
    conversations: list[LocomoConversation],
    budgets: list[int],
    systems_factory=build_systems,
    skip_categories: tuple[int, ...] = (5,),  # adversarial: no evidence to recall
) -> list[LocomoResultRow]:
    """Execute part B. One row per (system, budget), aggregated over conversations."""
    acc: dict[tuple[str, int], list[int | float]] = {}
    for conv in conversations:
        qa = [q for q in conv.qa if q.category not in skip_categories and q.evidence]
        if not qa:
            continue
        for system in systems_factory(consolidated_row=False):
            system.ingest(list(conv.episodes))
            for budget in budgets:
                key = (system.name, budget)
                cell = acc.setdefault(key, [0, 0, 0, 0.0])
                for q in qa:
                    packed = system.inject(q.question, budget)
                    packed_ids = {ep.id for ep in packed}
                    ev_ids = {f"{conv.conversation_id}:{e}" for e in q.evidence}
                    hits = len(ev_ids & packed_ids)
                    used = sum(approx_tokens(ep.content) + 1 for ep in packed)
                    cell[0] += 1
                    cell[1] += int(hits == len(ev_ids))
                    cell[2] += int(hits > 0)
                    cell[3] += used
    rows = []
    for (name, budget), (n, all_hit, any_hit, tokens) in sorted(acc.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        rows.append(
            LocomoResultRow(
                system=name, budget=budget, n=n, all_evidence_hit=all_hit,
                any_evidence_hit=any_hit, avg_tokens_used=round(tokens / n, 1),
            )
        )
    return rows
