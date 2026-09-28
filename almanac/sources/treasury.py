"""Treasury daily par yield curve CSV, one file per calendar year:
`Date,"1 Mo","1.5 Month","2 Mo",...,"30 Yr"`, dates MM/DD/YYYY, newest first.
Delivered ids: <series id>_<tenor>, tenors 1M, 6W (1.5 month), 2M ... 30Y."""

from __future__ import annotations

import re
from datetime import datetime

from ..model import Observation
from ._util import get, num, require, rows


def tenor(label: str) -> str:
    label = label.strip()
    if re.fullmatch(r"1\.5 Month", label):
        return "6W"
    m = re.fullmatch(r"(\d+) (Mo|Month|Yr|Year)s?", label)
    require(m is not None, f"Treasury tenor column {label!r}")
    return m.group(1) + ("M" if m.group(2).startswith("M") else "Y")


def parse(body: bytes) -> list[tuple[str, str, float]]:
    table = rows(body)
    require(bool(table) and table[0][0].strip() == "Date", "Treasury header")
    tenors = [tenor(c) for c in table[0][1:]]
    out = []
    for r in table[1:]:
        if not r or not r[0].strip():
            continue
        d = datetime.strptime(r[0].strip(), "%m/%d/%Y").date().isoformat()
        for t, cell in zip(tenors, r[1:]):
            if (v := num(cell)) is not None:
                out.append((d, t, v))
    return out


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        for year in range(ctx.since.year, ctx.today.year + 1):
            url = ctx.src["url"].format(year=year)
            for d, t, v in parse(get(ctx, url)):
                obs.append(Observation(f"{s['id']}_{t}", d, v, ctx.source))
    return obs
