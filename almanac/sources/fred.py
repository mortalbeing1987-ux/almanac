"""FRED fredgraph.csv (keyless): `observation_date,<CODE>` then one row per date.
Missing values are empty or '.'."""

from __future__ import annotations

from ..model import Observation
from ..registry import delivered_id, keys
from ._util import get, num, require, rows, url_for


def parse(body: bytes, code: str) -> list[tuple[str, float]]:
    table = rows(body)
    require(bool(table) and len(table[0]) >= 2 and table[0][1].strip() == code,
            f"FRED header for {code}")
    out = []
    for r in table[1:]:
        if len(r) >= 2 and (v := num(r[1])) is not None:
            out.append((r[0].strip(), v))
    return out


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        for k in keys(s):
            for d, v in parse(get(ctx, url_for(ctx, k)), k):
                obs.append(Observation(delivered_id(s, k), d, v, ctx.source))
    return obs
