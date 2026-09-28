"""RBA statistical table CSV (e.g. F1): title and metadata rows, a
`Series ID,<ID>,...` row naming each column, then rows dated like 25-Sep-2026."""

from __future__ import annotations

from datetime import datetime

from ..model import Observation
from ._util import get, num, require, rows, url_for


def parse(body: bytes, series_id: str) -> list[tuple[str, float]]:
    table = rows(body)
    head = next((i for i, r in enumerate(table) if r and r[0] == "Series ID"), None)
    require(head is not None, "RBA 'Series ID' row")
    require(series_id in table[head], f"RBA column {series_id}")
    ci = table[head].index(series_id)
    out = []
    for r in table[head + 1:]:
        if len(r) > ci and r[0].strip() and (v := num(r[ci])) is not None:
            out.append((datetime.strptime(r[0].strip(), "%d-%b-%Y").date().isoformat(), v))
    return out


def fetch(ctx) -> list[Observation]:
    body = get(ctx, url_for(ctx))
    return [Observation(s["id"], d, v, ctx.source)
            for s in ctx.series for d, v in parse(body, s["key"])]
