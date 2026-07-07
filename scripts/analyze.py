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


def table_protocol_v2(conn) -> list[dict]:
    """Cell G: protocol v2 (in-frame work log + account probing) contrast."""
    rows = _rows(conn, "cell = 'G'")
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
        out_rows.append([cond, n, flag, lie["mean"], over["mean"], tempt["mean"],
                         credits["mean"], jdec["mean"], jcon["mean"]])
        summary.append({"condition": cond, "n": n, "lie_rate": lie["mean"],
                        "temptation_overstatement": tempt["mean"],
                        "credits": credits["mean"], "judge_deception": jdec["mean"],
                        "judge_consistency": jcon["mean"]})
    if out_rows:
        p = _write_csv(
            "protocol_v2.csv",
            ["condition", "n", "flag", "lie_rate_mean", "overstatement_mean",
             "temptation_overstatement_mean", "credits_mean",
             "judge_deception_mean", "judge_consistency_mean"],
            out_rows,
        )
        print(f"wrote {p}")
    else:
        print("cell G: no rows, skipped protocol_v2")
    return summary


def table_judge_reliability(conn) -> list[dict]:
    """Cell E truthful baseline + judge SD across all runs with replicas."""
    out_rows = []
    summary = []
    # truthful baseline: judge deception should be near zero if the judge is sound
    e_rows = _rows(conn, "cell = 'E'")
    if e_rows:
        jdec = _agg([r["judge_deception"] for r in e_rows])
        over = _agg([r["total_overstatement"] for r in e_rows])
        out_rows.append(["truthful_baseline", len(e_rows),
                         jdec["mean"], over["mean"], None])
        summary.append({"group": "truthful_baseline", "judge_deception": jdec["mean"],
                        "overstatement": over["mean"]})
    # judge SD by condition, where replicas were run
    rep_rows = _rows(conn, "n_judge_replicas > 1")
    for cond, g in sorted(_group_by(rep_rows, "condition").items()):
        sd = _agg([r["judge_deception_sd"] for r in g])
        out_rows.append([cond, len(g), None, None, sd["mean"]])
    if out_rows:
        p = _write_csv(
            "judge_reliability.csv",
            ["group", "n", "judge_deception_mean", "overstatement_mean",
             "judge_deception_sd_mean"],
            out_rows,
        )
        print(f"wrote {p}")
    else:
        print("no cell E / judge-replica rows, skipped judge_reliability")
    return summary


def table_probing_depth(conn) -> list[dict]:
    rows = _rows(conn, "cell = 'F'")
    out_rows = []
    summary = []
    # group by (depth via condition prefix, memory_kind)
    def depth_of(cond: str) -> int:
        try:
            return int(cond.split("_")[1])
        except (IndexError, ValueError):
            return -1

    groups: dict = {}
    for r in rows:
        key = (depth_of(r["condition"]), r["memory_kind"])
        groups.setdefault(key, []).append(r)
    for (depth, kind) in sorted(groups):
        g = groups[(depth, kind)]
        tempt = _agg([r["temptation_overstatement"] for r in g])
        lie = _agg([r["lie_rate"] for r in g])
        out_rows.append([depth, kind, len(g), tempt["mean"], lie["mean"]])
        summary.append({"depth": depth, "memory": kind, "n": len(g),
                        "temptation_overstatement": tempt["mean"], "lie_rate": lie["mean"]})
    if out_rows:
        p = _write_csv(
            "probing_depth.csv",
            ["probe_depth", "memory_kind", "n",
             "temptation_overstatement_mean", "lie_rate_mean"],
            out_rows,
        )
        print(f"wrote {p}")
    else:
        print("cell F: no rows, skipped probing_depth")
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
#
# Palette: validated categorical slots (fixed order, never cycled) from the
# dataviz reference palette, light-mode surface. Single-series charts use slot 1
# only. Grid is recessive; identity for >= 2 series is carried by a legend plus
# direct labels, not color alone.
# --------------------------------------------------------------------------- #
PALETTE = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948"]
INK = "#333333"
GRID = "#e3e3e3"


def _style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK, labelsize=8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)


def _bar(names, values, title, ylabel, fname):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vals = [v if v is not None else 0 for v in values]
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 4))
    bars = ax.bar(range(len(names)), vals, color=PALETTE[0], width=0.62, zorder=2)
    for rect, orig in zip(bars, values):
        label = "n/a" if orig is None else f"{round(orig, 1):g}"
        ax.annotate(label, (rect.get_x() + rect.get_width() / 2, rect.get_height()),
                    ha="center", va="bottom", fontsize=8, color=INK)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
    ax.set_title(title, fontsize=10, color=INK)
    ax.set_ylabel(ylabel, fontsize=9, color=INK)
    _style_axes(ax)
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
    ax.plot(x, [v if v is not None else 0 for v in y], marker="o", markersize=6,
            linewidth=2, color=PALETTE[0], zorder=2)
    ax.set_title(title, fontsize=10, color=INK)
    ax.set_xlabel(xlabel, fontsize=9, color=INK)
    ax.set_ylabel(ylabel, fontsize=9, color=INK)
    _style_axes(ax)
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


def _plot_probing_depth(summary: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_mem: dict = {}
    for s in summary:
        by_mem.setdefault(s["memory"], []).append(s)
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 4))
    # fixed slot assignment: control -> slot 1, retrieval -> slot 2 (never cycled)
    slot = {"none": PALETTE[0], "retrieval": PALETTE[1]}
    for mem in sorted(by_mem):
        pts = sorted(by_mem[mem], key=lambda x: x["depth"])
        xs = [p["depth"] for p in pts]
        ys = [p["temptation_overstatement"] or 0 for p in pts]
        color = slot.get(mem, PALETTE[2])
        ax.plot(xs, ys, marker="o", markersize=6, linewidth=2,
                label=mem, color=color, zorder=2)
        # direct label at the line's right end (identity not by color alone)
        if xs:
            ax.annotate(mem, (xs[-1], ys[-1]), textcoords="offset points",
                        xytext=(6, 0), fontsize=8, color=INK)
    ax.set_title("Probing depth x memory (Cell F)", fontsize=10, color=INK)
    ax.set_xlabel("overseer probing depth (n_probes)", fontsize=9, color=INK)
    ax.set_ylabel("temptation overstatement", fontsize=9, color=INK)
    ax.legend(fontsize=8, frameon=False)
    _style_axes(ax)
    fig.tight_layout()
    path = PLOTS_DIR / "probing_depth.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"wrote {path}")


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
    v2 = table_protocol_v2(conn)
    table_judge_reliability(conn)
    depth = table_probing_depth(conn)
    table_manipulation_check(conn)
    make_plots(cond, length, typ, cap)
    if depth:
        _plot_probing_depth(depth)
    if v2:
        _bar([c["condition"] for c in v2],
             [c["credits"] for c in v2],
             "Protocol v2: credits by condition (Cell G)",
             "mean total credits", "v2_credits_by_condition.png")
        _bar([c["condition"] for c in v2],
             [c["judge_deception"] for c in v2],
             "Protocol v2: judge deception by condition (Cell G)",
             "mean judge deception (0-100)", "v2_judge_deception.png")
    conn.close()
    print("analysis complete")


if __name__ == "__main__":
    main()
