"""Verify sleep inferences do not damage the Part A clean-recall benchmark.

Three cells, all on the HOLDOUT seeds (6-10) of the synthetic recall
benchmark (memsub.bench part A), budgets 100-800, zero LLM cost:

  1. `policy+cons` (regression): the shipping consolidated system on the
     unmodified datasets. Must reproduce the recorded 98.0% clean recall
     exactly, proving the consolidate.py inference-rule edits changed
     nothing when no inference episodes exist.
  2. `policy+cons+adv-inference (rule ON)`: every dataset is augmented with
     one adversarial inference per fact family: a sleep-style inference
     episode (kind="inference", provenance.inferred=True) restating a STALE
     value with a timestamp NEWER than every observed version, i.e. the
     worst case where sleep hallucinated an outdated value after the fact.
     Consolidation uses a deterministic contradiction judge (contradicts
     iff same subject+attribute, different value; this measures the
     supersede-direction RULE at benchmark scale, not any LLM's judgment).
  3. Same, with the observed-beats-inferred rule DISABLED (monkeypatched):
     the counterfactual that quantifies what the rule is worth.

Appends results to docs/benchmark.md. Every number from an executed run.
Run: .venv/bin/python scripts/verify_sleep_cleanrecall.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import memsub.consolidate as consolidate_mod  # noqa: E402
from memsub.backends import MockBackend  # noqa: E402
from memsub.bench import (  # noqa: E402
    PolicySystem,
    RecallResultRow,
    generate_recall_dataset,
)
from memsub.store import Episode  # noqa: E402

SEEDS = [6, 7, 8, 9, 10]  # HOLDOUT (docs/benchmark.md protocol)
BUDGETS = [100, 200, 400, 800]
TUNED = {"recency_weight": 0.999, "recency_floor": 0.75, "importance_floor": 0.0,
         "dedup_threshold": 0.88}

_FACT_RE = re.compile(r"^(?P<prefix>The .+ of .+ is )(?P<val>\d+)\.?$")


def deterministic_judge() -> MockBackend:
    """Contradicts iff both statements are 'The <attr> of <subj> is <val>.'
    with identical prefix and different value. Deterministic, no LLM."""

    def responder(system: str, messages: list[dict]) -> str:
        import json

        verdicts = []
        text = messages[0]["content"]
        for block in text.split("PAIR ")[1:]:
            lines = block.strip().splitlines()
            idx = int(lines[0].rstrip(":"))
            a = lines[1].partition("A (older): ")[2]
            b = lines[2].partition("B (newer): ")[2]
            ma, mb = _FACT_RE.match(a.strip()), _FACT_RE.match(b.strip())
            contradicts = bool(
                ma and mb and ma.group("prefix") == mb.group("prefix")
                and ma.group("val") != mb.group("val")
            )
            verdicts.append({"pair": idx, "contradicts": contradicts})
        return json.dumps(verdicts)

    return MockBackend(responder=responder, model="deterministic-judge")


def adversarial_dataset(seed: int):
    """Part A dataset + one sleep-style adversarial inference per fact family:
    a stale value restated as an inference, newer than every observed version."""
    ds = generate_recall_dataset(seed)
    max_ts = max(ep.ts for ep in ds.episodes)
    extra: list[Episode] = []
    for qi, q in enumerate(ds.queries):
        stale_val = q.stale[0]
        # reconstruct the family wording from the gold episode
        gold = next(ep for ep in ds.episodes if ep.id == q.gold_episode_id)
        m = _FACT_RE.match(gold.content)
        assert m, gold.content
        extra.append(
            Episode(
                content=f"{m.group('prefix')}{stale_val}.",
                run_id="bench",
                session_id="sleep:inference",
                kind="inference",
                importance=0.7,
                provenance={"inferred": True, "origin": "sleep_inference"},
                ts=max_ts + 100.0 + qi,
                id=f"adv-inf-{qi}",
            )
        )
    ds.episodes = ds.episodes + extra
    return ds


def run_cell(name: str, adversarial: bool, rule_enabled: bool) -> list[RecallResultRow]:
    orig = consolidate_mod._is_inferred
    if not rule_enabled:
        consolidate_mod._is_inferred = lambda ep: False
    try:
        def factory():
            return [PolicySystem(consolidated=True,
                                 consolidation_backend=deterministic_judge(),
                                 name=name, **TUNED)]

        acc = {}
        for seed in SEEDS:
            ds = adversarial_dataset(seed) if adversarial else generate_recall_dataset(seed)
            rows = _run_one(ds, factory, BUDGETS)
            for r in rows:
                key = (r.system, r.budget)
                cell = acc.setdefault(key, [0, 0, 0, 0, 0.0])
                cell[0] += r.n
                cell[1] += r.correct
                cell[2] += r.clean
                cell[3] += r.stale_only
                cell[4] += r.avg_tokens_used * r.n
        out = []
        for (sysname, budget), (n, c, cl, st, tok) in sorted(acc.items(), key=lambda kv: kv[0][1]):
            out.append(RecallResultRow(system=sysname, budget=budget, n=n, correct=c,
                                       clean=cl, stale_only=st,
                                       avg_tokens_used=round(tok / n, 1)))
        return out
    finally:
        consolidate_mod._is_inferred = orig


def _run_one(ds, factory, budgets):
    """Single-dataset version of memsub.bench.run_recall_benchmark's inner loop."""
    from memsub.policy import approx_tokens

    rows = []
    for system in factory():
        system.ingest(list(ds.episodes))
        for budget in budgets:
            n = correct = clean = stale_only = 0
            tokens = 0.0
            for q in ds.queries:
                packed = system.inject(q.question, budget)
                text = "\n".join(ep.content for ep in packed)
                gold_in = any(ep.id == q.gold_episode_id for ep in packed)
                stale_in = any(s in text for s in q.stale)
                n += 1
                correct += int(gold_in)
                clean += int(gold_in and not stale_in)
                stale_only += int(stale_in and not gold_in)
                tokens += sum(approx_tokens(ep.content) + 1 for ep in packed)
            rows.append(RecallResultRow(system=system.name, budget=budget, n=n,
                                        correct=correct, clean=clean, stale_only=stale_only,
                                        avg_tokens_used=round(tokens / n, 1)))
    return rows


def main() -> int:
    t0 = time.time()
    cells = [
        ("policy+cons (regression, no inferences)", False, True),
        ("policy+cons + adversarial inferences, rule ON", True, True),
        ("policy+cons + adversarial inferences, rule OFF", True, False),
    ]
    lines = [
        "",
        "## Sleep-inference safety check on Part A clean recall "
        f"(executed {time.strftime('%Y-%m-%d %H:%M')}, `scripts/verify_sleep_cleanrecall.py`)",
        "",
        "Holdout seeds 6-10, n=150 queries per cell per budget. Adversarial setup: one "
        "sleep-style inference per fact family restating a STALE value with a timestamp "
        "newer than every observed version (worst case: sleep hallucinated an outdated "
        "value after the fact). Contradictions judged deterministically (same "
        "subject+attribute, different value), so this measures the supersede-direction "
        "rule (observed beats inferred, in dedup and in the contradiction pass), not any "
        "LLM's judgment. Rule OFF = `_is_inferred` monkeypatched to always-False, the "
        "counterfactual.",
        "",
        "| cell | budget | recall | clean | stale | n |",
        "|---|---|---|---|---|---|",
    ]
    for name, adversarial, rule_enabled in cells:
        rows = run_cell(name, adversarial, rule_enabled)
        for r in rows:
            lines.append(
                f"| {name} | {r.budget} | {100 * r.recall:.1f}% | {100 * r.clean_recall:.1f}% "
                f"| {100 * r.stale_rate:.1f}% | {r.n} |"
            )
        print(f"done: {name} ({time.time() - t0:.1f}s)")
    report = "\n".join(lines) + "\n"
    with open(ROOT / "docs" / "benchmark.md", "a") as f:
        f.write(report)
    print(report)
    print(f"wall {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
