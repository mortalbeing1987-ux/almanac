"""FRED economic release calendar for one release (rid) and year: date header
rows like "Wednesday October 14, 2026", each followed by that release's row.
FRED republishes the schedules set by the statistical agencies (BLS for CPI and
the Employment Situation); it is used because bls.gov refuses scripted clients."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from ._util import get, require, text

DAY = re.compile(r">\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
                 r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*<")


def parse(body: bytes, rid: str):
    page = text(body)
    require(f"/release?rid={rid}" in page, f"FRED calendar rows for release {rid}")
    return [datetime.strptime(d, "%B %d, %Y").date() for d in DAY.findall(page)]


def fetch(ctx):
    from ..calendar import AHEAD_DAYS  # one page per calendar year the horizon touches
    rid = ctx.series[0]["key"]
    last_year = (ctx.today + timedelta(days=AHEAD_DAYS)).year
    out = []
    for year in range(ctx.since.year, last_year + 1):
        out += parse(get(ctx, ctx.src["url"].format(key=rid, year=year)), rid)
    return out
