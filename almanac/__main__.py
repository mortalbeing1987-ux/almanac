"""python -m almanac collect --uses AB --since 2026-09-01 --out out/
python -m almanac deliver --data-dir <checked-out private data repo> --uses ABCDEF

`collect` fetches and writes one bundle to --out; `deliver` runs a scheduled
pass with delivery state (see deliver.py). Both print per-source status and
counts only -- never observation values.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import deliver
from .bundle import write_macro_bundle
from .http import Http
from .registry import load
from .run import collect


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="almanac")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="fetch and write a macro bundle")
    c.add_argument("--uses", default="AB", help="use-case tags, e.g. AB")
    c.add_argument("--since", type=date.fromisoformat,
                   default=date.today() - timedelta(days=14), help="earliest obs_date (YYYY-MM-DD)")
    c.add_argument("--out", type=Path, default=Path("out"))
    d = sub.add_parser("deliver", help="scheduled pass into a checked-out private data repo")
    d.add_argument("--data-dir", type=Path, required=True)
    d.add_argument("--uses", default="ABCDEF", help="use-case tags, e.g. ABCDEF (D = event calendar)")
    args = ap.parse_args(argv)

    if args.cmd == "deliver":
        status = deliver.run(load(), args.data_dir, args.uses)
        for src, s in sorted(status["sources"].items()):
            print(f"{src:10s} {s['status']:7s} {s['observations']:6d} obs  since {s['since']}  {s['reason']}")
        size = ""
        if status["bundle"]:
            parquet = args.data_dir / "bundles" / status["bundle"] / "observations.parquet"
            size = f"  ({parquet.stat().st_size / 1e6:.2f} MB)"
        print(f"rows delivered: {status['rows_delivered']} (revisions: {status['revisions_delivered']})"
              f"  bundle: {status['bundle']}{size}")
        if status["bundle"]:
            print("rows by series: " + ", ".join(f"{k} {n}" for k, n in sorted(_rows_by_series(parquet).items())))
        stale = status["freshness"]["stale"]
        print(f"stale series: {len(stale)}{'  ' + ', '.join(stale) if stale else ''}")
        for src, s in sorted(status["calendar_sources"].items()):
            print(f"{src:16s} {s['status']:7s} {s['events']:3d} events  furthest {s['furthest_event']}  {s['reason']}")
        if status["calendar_sources"]:
            ahead = status["freshness"]["calendar_ahead"]
            print(f"events delivered: {status['events_delivered']}  bundle: {status['cal_bundle']}")
            print(f"calendars short of {ahead['horizon_days']} days: {', '.join(ahead['short']) or 'none'}")
        return 0

    started = datetime.now(timezone.utc)
    results = collect(load(), args.uses, Http(), args.since)
    path = write_macro_bundle(args.out, started, results)
    for r in results:
        print(f"{r.source:10s} {r.status:7s} {len(r.observations):6d} obs  {r.reason}")
    print(f"bundle: {path}")
    return 0


def _rows_by_series(parquet: Path) -> dict[str, int]:
    """Row counts per registry series id (counts only, never values)."""
    import pyarrow.parquet as pq
    ids = sorted((s["id"] for s in load().series), key=len, reverse=True)
    out: dict[str, int] = {}
    for sid in pq.read_table(parquet, columns=["series_id"]).column("series_id").to_pylist():
        reg_id = next((i for i in ids if sid == i or sid.startswith(i + "_")), sid)
        out[reg_id] = out.get(reg_id, 0) + 1
    return out


if __name__ == "__main__":
    sys.exit(main())
