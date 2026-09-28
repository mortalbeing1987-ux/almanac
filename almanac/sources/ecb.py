"""ECB data API, format=csvdata: header row with TIME_PERIOD and OBS_VALUE columns."""

from __future__ import annotations

from ..model import Observation
from ..registry import delivered_id, keys
from ._util import get, num, require, rows, url_for


def parse(body: bytes) -> list[tuple[str, float]]:
    table = rows(body)
    require(bool(table) and "TIME_PERIOD" in table[0] and "OBS_VALUE" in table[0], "ECB csvdata header")
    ti, vi = table[0].index("TIME_PERIOD"), table[0].index("OBS_VALUE")
    return [(r[ti], v) for r in table[1:] if len(r) > vi and (v := num(r[vi])) is not None]


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        for k in keys(s):
            for d, v in parse(get(ctx, url_for(ctx, k))):
                obs.append(Observation(delivered_id(s, k), d, v, ctx.source))
    return obs
