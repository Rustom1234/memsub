#!/usr/bin/env python
"""Run experiment cells and write results.

Usage:
  python scripts/run_experiment.py --cell A --backend mock --n-runs 3
  python scripts/run_experiment.py --cell all --backend cli --model haiku --n-runs 5
  python scripts/run_experiment.py --cell D --backend cli --models haiku sonnet --n-runs 3

Outputs (under results/):
  results/runs/<run_id>.jsonl        full per-call transcript log
  results/runs/<run_id>.result.json  the RunResult (config + records + measures)
  results/results.sqlite             aggregate results database (one row per run)
  results/results.jsonl              append-only aggregate (one line per run)

Every run is seeded and its config is embedded in the result, so any row is
reproducible from its logged config alone.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Repo root, for the memsub library the harness depends on.
sys.path.insert(0, str(ROOT.parent.parent))

from lying_game.backends import build_backend  # noqa: E402
from lying_game.config import ExperimentConfig  # noqa: E402
from lying_game.experiments import (  # noqa: E402
    cell_a_memory_vs_control,
    cell_b_length_ablation,
    cell_c_type_ablation,
    cell_d_capability,
    cell_e_truthful_baseline,
    cell_f_probing_depth,
    cell_g_protocol_v2,
)
from lying_game.harness import Harness  # noqa: E402

RESULTS_DIR = ROOT / "results"
RUNS_DIR = RESULTS_DIR / "runs"
DB_PATH = RESULTS_DIR / "results.sqlite"
JSONL_PATH = RESULTS_DIR / "results.jsonl"

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    cell TEXT,
    condition TEXT,
    memory_kind TEXT,
    write_transform TEXT,
    memory_capacity INTEGER,
    model TEXT,
    backend TEXT,
    seed INTEGER,
    n_sessions INTEGER,
    total_credits INTEGER,
    n_lies INTEGER,
    n_scored_sessions INTEGER,
    lie_rate REAL,
    total_overstatement INTEGER,
    temptation_overstatement INTEGER,
    judge_deception INTEGER,
    judge_consistency INTEGER,
    judge_deception_sd REAL,
    judge_consistency_sd REAL,
    n_judge_replicas INTEGER,
    agent_variant TEXT,
    memory_used_rate REAL,
    manipulation_prior_rate REAL,
    ts REAL
);
"""


def _db() -> sqlite3.Connection:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_DB_SCHEMA)
    # migrate: add columns that may be missing from a DB created by an older run
    existing = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    for col, typ in [
        ("judge_deception_sd", "REAL"),
        ("judge_consistency_sd", "REAL"),
        ("n_judge_replicas", "INTEGER"),
        ("agent_variant", "TEXT"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {typ}")
    conn.commit()
    return conn


def _temptation_overstatement(result) -> int | None:
    for p in result.deception["per_session"]:
        if p.get("overstatement") is not None:
            # temptation session is where actual is minimal; find via records
            pass
    for rec in result.sessions:
        if rec.is_temptation and rec.claimed_completed is not None:
            return max(0, rec.claimed_completed - rec.actual_completed)
    return None


def _write_result(result, cell: str, conn: sqlite3.Connection) -> None:
    cfg = result.config
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{cfg.run_id}.result.json").write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True)
    )
    import statistics as _stats

    reps = result.judge_replicas or []
    rep_dec = [x["deception"] for x in reps if x["deception"] is not None]
    rep_con = [x["consistency"] for x in reps if x["consistency"] is not None]
    jdec_sd = round(_stats.pstdev(rep_dec), 3) if len(rep_dec) > 1 else (0.0 if rep_dec else None)
    jcon_sd = round(_stats.pstdev(rep_con), 3) if len(rep_con) > 1 else (0.0 if rep_con else None)
    mem_used = [1 if r.memory_used else 0 for r in result.sessions]
    prior = [1 if r.memory_retrieved_prior_session else 0 for r in result.sessions]
    row = {
        "run_id": cfg.run_id,
        "cell": cell,
        "condition": cfg.condition,
        "memory_kind": cfg.memory_kind,
        "write_transform": cfg.write_transform,
        "memory_capacity": cfg.memory_capacity,
        "model": cfg.model or cfg.backend,
        "backend": cfg.backend,
        "seed": cfg.seed,
        "n_sessions": cfg.n_sessions,
        "total_credits": result.total_credits,
        "n_lies": result.deception["n_lies"],
        "n_scored_sessions": result.deception["n_scored_sessions"],
        "lie_rate": result.deception["lie_rate"],
        "total_overstatement": result.deception["total_overstatement"],
        "temptation_overstatement": _temptation_overstatement(result),
        "judge_deception": result.judge_deception,
        "judge_consistency": result.judge_consistency,
        "judge_deception_sd": jdec_sd,
        "judge_consistency_sd": jcon_sd,
        "n_judge_replicas": len(reps),
        "agent_variant": cfg.agent_variant,
        "memory_used_rate": (sum(mem_used) / len(mem_used)) if mem_used else None,
        "manipulation_prior_rate": (sum(prior) / len(prior)) if prior else None,
        "ts": time.time(),
    }
    cols = ", ".join(row.keys())
    ph = ", ".join("?" for _ in row)
    conn.execute(f"INSERT OR REPLACE INTO runs ({cols}) VALUES ({ph})", list(row.values()))
    conn.commit()
    with open(JSONL_PATH, "a") as fp:
        fp.write(json.dumps(row, sort_keys=True) + "\n")


def build_configs(args) -> list[tuple[str, ExperimentConfig]]:
    configs: list[tuple[str, ExperimentConfig]] = []
    cell = args.cell.upper()
    models = args.models if args.models else ([args.model] if args.model else ["haiku", "sonnet"])
    if cell in ("A", "ALL"):
        configs += [("A", c) for c in cell_a_memory_vs_control(args.n_runs, args.backend, args.model)]
    if cell in ("B", "ALL"):
        configs += [("B", c) for c in cell_b_length_ablation(args.n_runs, args.backend, args.model)]
    if cell in ("C", "ALL"):
        configs += [("C", c) for c in cell_c_type_ablation(args.n_runs, args.backend, args.model)]
    if cell in ("D", "ALL"):
        configs += [("D", c) for c in cell_d_capability(args.n_runs, args.backend, models)]
    if cell in ("E", "ALL"):
        configs += [("E", c) for c in cell_e_truthful_baseline(args.n_runs, args.backend, args.model)]
    if cell in ("F", "ALL"):
        configs += [("F", c) for c in cell_f_probing_depth(args.n_runs, args.backend, args.model)]
    if cell in ("G",):  # protocol v2 replication is opt-in, not part of "all"
        configs += [("G", c) for c in cell_g_protocol_v2(args.n_runs, args.backend, args.model)]
    # apply judge-replica override
    if args.judge_replicas and args.judge_replicas > 1:
        for _, c in configs:
            c.judge_replicas = args.judge_replicas
    return configs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", default="A", help="A|B|C|D|all")
    ap.add_argument("--backend", default="mock", help="mock|cli|api|auto")
    ap.add_argument("--model", default=None, help="model for cells A-C")
    ap.add_argument("--models", nargs="*", default=None, help="models for cell D")
    ap.add_argument("--n-runs", type=int, default=3, help="seeds per cell arm")
    ap.add_argument("--judge", action="store_true", help="also run the LLM judge")
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--judge-replicas", type=int, default=1,
                    help="run the judge N times per run for reliability stats")
    ap.add_argument("--limit", type=int, default=None, help="cap total runs (for pilots)")
    ap.add_argument("--parallel", type=int, default=1, help="concurrent runs (worker threads)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip run_ids that already have a result.json")
    ap.add_argument("--results-dir", default=None,
                    help="override results directory (default: <repo>/results); "
                         "use a scratch dir for mock smoke tests")
    args = ap.parse_args()

    if args.results_dir:
        global RESULTS_DIR, RUNS_DIR, DB_PATH, JSONL_PATH
        RESULTS_DIR = Path(args.results_dir)
        RUNS_DIR = RESULTS_DIR / "runs"
        DB_PATH = RESULTS_DIR / "results.sqlite"
        JSONL_PATH = RESULTS_DIR / "results.jsonl"

    configs = build_configs(args)
    if args.limit:
        configs = configs[: args.limit]
    if args.skip_existing:
        before = len(configs)
        configs = [
            (cell, c) for cell, c in configs
            if not (RUNS_DIR / f"{c.run_id}.result.json").exists()
        ]
        print(f"skip-existing: {before - len(configs)} already done, {len(configs)} to run",
              flush=True)
    conn = _db()
    backend = build_backend(args.backend, model=args.model)
    judge_backend = None
    if args.judge:
        judge_backend = build_backend(args.backend, model=args.judge_model or args.model)

    def execute(cell_cfg):
        cell, cfg = cell_cfg
        t0 = time.time()
        # rebuild a per-run backend when model differs (cell D)
        run_backend = backend
        if cfg.model and cfg.model != backend.model and args.backend != "mock":
            run_backend = build_backend(args.backend, model=cfg.model)
        result = Harness(cfg, run_backend, log_dir=RUNS_DIR).run(judge_backend=judge_backend)
        return cell, cfg, result, time.time() - t0

    print(
        f"Running {len(configs)} runs on backend={backend.name} "
        f"model={backend.model} parallel={args.parallel}",
        flush=True,
    )
    done = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = [pool.submit(execute, cc) for cc in configs]
        for fut in as_completed(futures):
            try:
                cell, cfg, result, dt = fut.result()
            except Exception as exc:  # keep the batch alive on one failure
                print(f"RUN FAILED: {type(exc).__name__}: {exc}", flush=True)
                continue
            # SQLite writes happen only on this (main) thread
            _write_result(result, cell, conn)
            done += 1
            print(
                f"[{done}/{len(configs)}] {cfg.run_id} "
                f"credits={result.total_credits} lies={result.deception['n_lies']} "
                f"judge_dec={result.judge_deception} ({dt:.1f}s)",
                flush=True,
            )
    conn.close()
    print(f"Done. Aggregate: {DB_PATH}", flush=True)


if __name__ == "__main__":
    main()
