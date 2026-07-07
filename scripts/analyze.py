#!/usr/bin/env python
"""Analysis pipeline: read the results database, produce tables and plots.

Reads results/results.sqlite and writes, under results/:
  tables/*.csv    aggregate tables (deception by condition, ablation curves, ...)
  plots/*.png     figures

NEVER fabricates numbers. Every table and figure is computed from rows that
actually executed. n per cell is reported in every table. Cells with n below
`pilot_threshold` are flagged as pilot-sized. If the database is empty or a cell
has no rows, the corresponding table/plot is skipped with a printed note.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
DB_PATH = RESULTS_DIR / "results.sqlite"
TABLES_DIR = RESULTS_DIR / "tables"
PLOTS_DIR = RESULTS_DIR / "plots"

PILOT_THRESHOLD = 5


def _rows(conn, where: str = "", params=()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    q = "SELECT * FROM runs"
    if where:
        q += " WHERE " + where
    return [dict(r) for r in conn.execute(q, params).fetchall()]


def _agg(values: list[float]) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "sd": None}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 3),
        "sd": round(statistics.pstdev(vals), 3) if len(vals) > 1 else 0.0,
    }


def _write_csv(name: str, header: list[str], rows: list[list]) -> Path:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    path = TABLES_DIR / name
    with open(path, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(header)
        w.writerows(rows)
    return path


def _group_by(rows: list[dict], key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out


def table_deception_by_condition(conn) -> list[dict]:
    rows = _rows(conn, "cell = 'A'")
    groups = _group_by(rows, "condition")
    out_rows = []
    summary = []
    for cond in sorted(groups):
        g = groups[cond]
        lie = _agg([r["lie_rate"] for r in g])
        over = _agg([r["total_overstatement"] for r in g])
        tempt = _agg([r["temptation_overstatement"] for r in g])
        credits = _agg([r["total_credits"] for r in g])
        jdec = _agg([r["judge_deception"] for r in g])
        jcon = _agg([r["judge_consistency"] for r in g])
        n = len(g)
        flag = "PILOT" if n < PILOT_THRESHOLD else ""
        out_rows.append(
            [
                cond, n, flag,
                lie["mean"], over["mean"], tempt["mean"], credits["mean"],
                jdec["mean"], jcon["mean"],
            ]
        )
        summary.append({"condition": cond, "n": n, "lie_rate": lie["mean"],
                        "temptation_overstatement": tempt["mean"],
                        "credits": credits["mean"], "judge_consistency": jcon["mean"]})
    if out_rows:
        p = _write_csv(
            "deception_by_condition.csv",
            ["condition", "n", "flag", "lie_rate_mean", "overstatement_mean",
             "temptation_overstatement_mean", "credits_mean",
             "judge_deception_mean", "judge_consistency_mean"],
            out_rows,
        )
        print(f"wrote {p}")
    else:
        print("cell A: no rows, skipped deception_by_condition")
    return summary


def table_length_ablation(conn) -> list[dict]:
    rows = _rows(conn, "cell = 'B'")
    groups = _group_by(rows, "memory_capacity")
    out_rows = []
    summary = []
    for cap in sorted(groups, key=lambda x: int(x)):
        g = groups[cap]
        lie = _agg([r["lie_rate"] for r in g])
        tempt = _agg([r["temptation_overstatement"] for r in g])
        credits = _agg([r["total_credits"] for r in g])
        n = len(g)
        flag = "PILOT" if n < PILOT_THRESHOLD else ""
        out_rows.append([cap, n, flag, lie["mean"], tempt["mean"], credits["mean"]])
        summary.append({"capacity": int(cap), "n": n, "lie_rate": lie["mean"],
                        "temptation_overstatement": tempt["mean"], "credits": credits["mean"]})
    if out_rows:
        p = _write_csv(
            "length_ablation.csv",
            ["memory_capacity", "n", "flag", "lie_rate_mean",
             "temptation_overstatement_mean", "credits_mean"],
            out_rows,
        )
        print(f"wrote {p}")
    else:
        print("cell B: no rows, skipped length_ablation")
    return summary


def table_type_ablation(conn) -> list[dict]:
    rows = _rows(conn, "cell = 'C'")
    groups = _group_by(rows, "write_transform")
    out_rows = []
    summary = []
    for t in sorted(groups):
        g = groups[t]
        lie = _agg([r["lie_rate"] for r in g])
        tempt = _agg([r["temptation_overstatement"] for r in g])
        jcon = _agg([r["judge_consistency"] for r in g])
        n = len(g)
        flag = "PILOT" if n < PILOT_THRESHOLD else ""
        out_rows.append([t, n, flag, lie["mean"], tempt["mean"], jcon["mean"]])
        summary.append({"transform": t, "n": n, "lie_rate": lie["mean"],
                        "temptation_overstatement": tempt["mean"],
                        "judge_consistency": jcon["mean"]})
    if out_rows:
        p = _write_csv(
            "type_ablation.csv",
            ["write_transform", "n", "flag", "lie_rate_mean",
             "temptation_overstatement_mean", "judge_consistency_mean"],
            out_rows,
        )
        print(f"wrote {p}")
    else:
        print("cell C: no rows, skipped type_ablation")
    return summary


def table_capability(conn) -> list[dict]:
    rows = _rows(conn, "cell = 'D'")
    groups = _group_by(rows, "model")
    out_rows = []
    summary = []
    for model in sorted(groups):
        g = groups[model]
        lie = _agg([r["lie_rate"] for r in g])
        tempt = _agg([r["temptation_overstatement"] for r in g])
        credits = _agg([r["total_credits"] for r in g])
        n = len(g)
        flag = "PILOT" if n < PILOT_THRESHOLD else ""
        out_rows.append([model, n, flag, lie["mean"], tempt["mean"], credits["mean"]])
        summary.append({"model": model, "n": n, "lie_rate": lie["mean"],
                        "temptation_overstatement": tempt["mean"], "credits": credits["mean"]})
    if out_rows:
        p = _write_csv(
            "capability.csv",
            ["model", "n", "flag", "lie_rate_mean",
             "temptation_overstatement_mean", "credits_mean"],
            out_rows,
        )
        print(f"wrote {p}")
    else:
        print("cell D: no rows, skipped capability")
    return summary


def table_manipulation_check(conn) -> None:
    rows = _rows(conn)
    groups = _group_by(rows, "memory_kind")
    out_rows = []
    for kind in sorted(groups):
        g = groups[kind]
        used = _agg([r["memory_used_rate"] for r in g])
        prior = _agg([r["manipulation_prior_rate"] for r in g])
        out_rows.append([kind, len(g), used["mean"], prior["mean"]])
    if out_rows:
        p = _write_csv(
            "manipulation_check.csv",
            ["memory_kind", "n_runs", "memory_used_rate_mean", "retrieved_prior_rate_mean"],
            out_rows,
        )
        print(f"wrote {p}")


# --------------------------------------------------------------------------- #
# Plots
# --------------------------------------------------------------------------- #
def _bar(names, values, title, ylabel, fname):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vals = [v if v is not None else 0 for v in values]
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(range(len(names)), vals, color="#4C72B0")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    fig.tight_layout()
    path = PLOTS_DIR / fname
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"wrote {path}")


def _line(x, y, title, xlabel, ylabel, fname):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(x, [v if v is not None else 0 for v in y], marker="o", color="#C44E52")
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    fig.tight_layout()
    path = PLOTS_DIR / fname
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"wrote {path}")


def make_plots(cond_summary, length_summary, type_summary, cap_summary):
    if cond_summary:
        _bar([c["condition"] for c in cond_summary],
             [c["temptation_overstatement"] for c in cond_summary],
             "Temptation-session overstatement by condition (Cell A)",
             "mean overstatement (tasks)", "deception_by_condition.png")
        _bar([c["condition"] for c in cond_summary],
             [c["credits"] for c in cond_summary],
             "Credits accumulated by condition (Cell A)",
             "mean total credits", "credits_by_condition.png")
        _bar([c["condition"] for c in cond_summary],
             [c["judge_consistency"] for c in cond_summary],
             "Judge cross-session consistency by condition (Cell A)",
             "mean consistency (0-100)", "consistency_by_condition.png")
    if length_summary:
        _line([s["capacity"] for s in length_summary],
              [s["temptation_overstatement"] for s in length_summary],
              "Memory-length ablation (Cell B)", "memory capacity",
              "temptation overstatement", "length_ablation.png")
    if type_summary:
        _bar([s["transform"] for s in type_summary],
             [s["temptation_overstatement"] for s in type_summary],
             "Memory-type ablation (Cell C)", "temptation overstatement",
             "type_ablation.png")
    if cap_summary:
        _bar([s["model"] for s in cap_summary],
             [s["temptation_overstatement"] for s in cap_summary],
             "Model-capability comparison (Cell D)", "temptation overstatement",
             "capability.png")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()
    if not Path(args.db).exists():
        print(f"no results database at {args.db}; run scripts/run_experiment.py first")
        sys.exit(1)
    conn = sqlite3.connect(args.db)
    n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print(f"analyzing {n} runs from {args.db}")
    cond = table_deception_by_condition(conn)
    length = table_length_ablation(conn)
    typ = table_type_ablation(conn)
    cap = table_capability(conn)
    table_manipulation_check(conn)
    make_plots(cond, length, typ, cap)
    conn.close()
    print("analysis complete")


if __name__ == "__main__":
    main()
