#!/usr/bin/env python
"""Print a compact numeric summary of every results table (for writing findings).

Reads results/tables/*.csv (run scripts/analyze.py and scripts/frame_breaks.py
first) and the results DB for per-run rows. Purely a rendering helper: no
numbers are computed here beyond what the tables already contain.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLES = ROOT / "results" / "tables"
DB = ROOT / "results" / "results.sqlite"


def show(name: str) -> None:
    path = TABLES / name
    if not path.exists():
        print(f"[{name}: missing]")
        return
    print(f"== {name} ==")
    with open(path) as fp:
        for row in csv.reader(fp):
            print("  " + " | ".join(row))
    print()


def main() -> None:
    for name in [
        "deception_by_condition.csv",
        "protocol_v2.csv",
        "length_ablation.csv",
        "type_ablation.csv",
        "capability.csv",
        "probing_depth.csv",
        "judge_reliability.csv",
        "manipulation_check.csv",
        "frame_breaks_summary.csv",
    ]:
        show(name)

    if DB.exists():
        conn = sqlite3.connect(DB)
        n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        by_cell = conn.execute(
            "SELECT cell, COUNT(*) FROM runs GROUP BY cell ORDER BY cell"
        ).fetchall()
        print(f"== runs in DB: {n} ==")
        for cell, c in by_cell:
            print(f"  cell {cell}: n={c}")
        conn.close()


if __name__ == "__main__":
    main()
