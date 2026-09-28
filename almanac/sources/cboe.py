"""CBOE index history CSVs, one file per index (MM/DD/YYYY dates):
  VIX, VIX3M:  DATE,OPEN,HIGH,LOW,CLOSE
  VVIX:        DATE,VVIX
The close is delivered as the value: the CLOSE column where the file has OHLC,
otherwise the single column named after the index. CBOE index data is
redistribution-restricted: only counts are ever logged."""

from __future__ import annotations

from datetime import datetime

from ..model import Observation
from ..registry import delivered_id, keys
from ._util import get, num, require, rows, url_for


def parse(body: bytes, index: str) -> list[tuple[str, float]]:
    table = rows(body)
    require(bool(table) and table[0] and table[0][0].strip().upper() == "DATE", f"CBOE {index} DATE header")
    head = [c.strip().upper() for c in table[0]]
    col = "CLOSE" if "CLOSE" in head else index.upper()
    require(col in head, f"CBOE {index} CLOSE or {index} column")
    ci = head.index(col)
    out = []
    for r in table[1:]:
        if len(r) > ci and r[0].strip() and (v := num(r[ci])) is not None:
            out.append((datetime.strptime(r[0].strip(), "%m/%d/%Y").date().isoformat(), v))
    return out


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        for k in keys(s):
            for d, v in parse(get(ctx, url_for(ctx, k)), k):
                obs.append(Observation(delivered_id(s, k), d, v, ctx.source))
    return obs
