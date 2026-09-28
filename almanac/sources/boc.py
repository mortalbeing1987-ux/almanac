"""Bank of Canada Valet API, CSV (keyless): a TERMS AND CONDITIONS block, a
SERIES block, then "OBSERVATIONS" followed by `"date","<series>"` and one row
per business day. `start_date` narrows the request."""

from __future__ import annotations

from ..model import Observation
from ._util import get, num, require, rows, url_for


def parse(body: bytes, series: str) -> list[tuple[str, float]]:
    table = rows(body)
    start = next((i for i, r in enumerate(table) if r[:1] == ["OBSERVATIONS"]), None)
    require(start is not None, "Valet OBSERVATIONS block")
    head = table[start + 1] if start + 1 < len(table) else []
    require(head[:1] == ["date"] and series in head, f"Valet header date,{series}")
    ci = head.index(series)
    out = []
    for r in table[start + 2:]:
        if not r:
            break  # a blank line ends the block
        if len(r) > ci and (v := num(r[ci])) is not None:
            out.append((r[0].strip(), v))
    return out


def fetch(ctx) -> list[Observation]:
    return [Observation(s["id"], d, v, ctx.source)
            for s in ctx.series for d, v in parse(get(ctx, url_for(ctx, s["key"])), s["key"])]
