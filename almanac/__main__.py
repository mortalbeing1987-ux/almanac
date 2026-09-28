"""python -m almanac collect --uses AB --since 2026-09-01 --out out/
python -m almanac deliver --data-dir <checked-out private data repo> --uses AB

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
    d.add_argument("--uses", default="AB", help="use-case tags, e.g. AB")
    args = ap.parse_args(argv)

    if args.cmd == "deliver":
        status = deliver.run(load(), args.data_dir, args.uses)
        for src, s in sorted(status["sources"].items()):
            print(f"{src:10s} {s['status']:7s} {s['observations']:6d} obs  since {s['since']}  {s['reason']}")
        print(f"rows delivered: {status['rows_delivered']}  bundle: {status['bundle']}")
        print(f"stale series: {len(status['freshness']['stale'])}")
        return 0

    started = datetime.now(timezone.utc)
    results = collect(load(), args.uses, Http(), args.since)
    path = write_macro_bundle(args.out, started, results)
    for r in results:
        print(f"{r.source:10s} {r.status:7s} {len(r.observations):6d} obs  {r.reason}")
    print(f"bundle: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
