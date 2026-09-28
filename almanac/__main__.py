"""python -m almanac collect --uses AB --since 2026-09-01 --out out/

Fetches the selected use cases and writes one macro bundle to --out. Prints a
per-source status summary only -- never observation values.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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
    args = ap.parse_args(argv)

    started = datetime.now(timezone.utc)
    results = collect(load(), args.uses, Http(), args.since)
    path = write_macro_bundle(args.out, started, results)
    for r in results:
        print(f"{r.source:10s} {r.status:7s} {len(r.observations):6d} obs  {r.reason}")
    print(f"bundle: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
