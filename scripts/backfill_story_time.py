"""Backfill story time onto existing episodes (eng-review I1 migration).

Re-derives each extraction-origin episode's event time from its source
transcript (provenance.source + turn_span -> max turn timestamp in span)
and writes the corrected `ts` to a COPY of the db, never in place. The old
transaction-time `ts` is preserved as provenance.extracted_at when that key
is missing. Swap the copy in manually after reviewing the report.

Run:
    python3 scripts/backfill_story_time.py [--db PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import shutil
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from memsub.extraction import load_transcript  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(Path.home() / ".memsub/memory.db"))
    ap.add_argument("--out", default=None, help="output db path (default <db>.backfilled)")
    args = ap.parse_args()

    src_db = Path(args.db).resolve()
    out_db = Path(args.out) if args.out else src_db.with_suffix(".backfilled.db")
    shutil.copy(src_db, out_db)

    conn = sqlite3.connect(out_db)
    rows = conn.execute("SELECT id, ts, provenance FROM episodes").fetchall()

    ts_cache: dict[str, dict[int, float]] = {}
    counts = collections.Counter()
    hist_after = collections.Counter()
    updates = []
    for ep_id, old_ts, prov_json in rows:
        try:
            prov = json.loads(prov_json)
        except ValueError:
            counts["bad_provenance"] += 1
            continue
        source = prov.get("source")
        span = prov.get("turn_span")
        if prov.get("origin") != "extraction" or not source or not span:
            counts["not_extraction"] += 1
            hist_after[datetime.date.fromtimestamp(old_ts).isoformat()] += 1
            continue
        if source not in ts_cache:
            try:
                ts_cache[source] = {t.index: t.ts for t in load_transcript(source) if t.ts}
            except OSError:
                ts_cache[source] = {}
        turn_ts = ts_cache[source]
        if not turn_ts:
            counts["source_missing_or_untimestamped"] += 1
            hist_after[datetime.date.fromtimestamp(old_ts).isoformat()] += 1
            continue
        lo, hi = int(span[0]), int(span[1])
        in_span = [v for i, v in turn_ts.items() if lo <= i <= hi]
        event_ts = max(in_span) if in_span else max(turn_ts.values())
        if not prov.get("extracted_at"):
            prov["extracted_at"] = old_ts
        updates.append((event_ts, json.dumps(prov, sort_keys=True), ep_id))
        counts["backfilled"] += 1
        hist_after[datetime.date.fromtimestamp(event_ts).isoformat()] += 1

    conn.executemany("UPDATE episodes SET ts = ?, provenance = ? WHERE id = ?", updates)
    conn.commit()
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    print(f"backfill -> {out_db}  (integrity: {ok})")
    for k, v in sorted(counts.items()):
        print(f"  {k:36s} {v}")
    print("  episodes per story-day (after):")
    for day in sorted(hist_after):
        print(f"    {day}  {hist_after[day]}")


if __name__ == "__main__":
    main()
