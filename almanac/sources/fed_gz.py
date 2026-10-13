"""Federal Reserve, Gilchrist-Zakrajsek credit spread and excess bond premium
(FEDS Notes): one CSV, `date,gz_spread,ebp,est_prob`, one row per month dated
on the first of the month, M/D/YYYY until 2026 and YYYY-MM-DD since (the Fed
switched by 2026-10-08; both are accepted). The whole history comes in every request.
Registry keys map to columns through `columns`; a missing column, a date that
is not the 1st, or dates out of order is a layout error, never "up to date"."""

from __future__ import annotations

import re
from datetime import date

from ..model import Observation
from ..registry import delivered_id, keys
from ._util import get, num, require, rows, url_for


def _day(s: str) -> date:
    s = s.strip()
    if "/" in s:
        m, d, y = (int(x) for x in s.split("/"))
        return date(y, m, d)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return date.fromisoformat(s)
    raise ValueError(s)


def parse(body: bytes, columns: dict[str, str]) -> dict[str, list[tuple[str, float]]]:
    """{key: [(YYYY-MM-01, value)]} for the registry keys' columns."""
    table = [r for r in rows(body) if any(c.strip() for c in r)]
    require(bool(table), "GZ file has a header")
    head = [c.strip().lower() for c in table[0]]
    require(head[:1] == ["date"], "GZ first column 'date'")
    idx = {}
    for key, col in columns.items():
        require(col in head, f"GZ column {col!r}")
        idx[key] = head.index(col)
    out: dict[str, list[tuple[str, float]]] = {k: [] for k in columns}
    last = ""
    for r in table[1:]:
        try:
            day = _day(r[0])
        except ValueError:
            require(False, f"GZ date {r[0][:12]!r} (M/D/YYYY or YYYY-MM-DD)")
        require(day.day == 1, f"GZ date {r[0][:12]!r} is the first of the month")
        iso = day.isoformat()
        require(iso > last, "GZ dates ascending, no duplicates")
        last = iso
        for key, i in idx.items():
            if len(r) > i and (v := num(r[i])) is not None:
                out[key].append((iso, v))
    return out


def fetch(ctx) -> list[Observation]:
    body = get(ctx, url_for(ctx))
    obs = []
    for s in ctx.series:
        got = parse(body, {k: s["columns"][k] for k in keys(s)})
        for k in keys(s):
            obs += [Observation(delivered_id(s, k), d, v, ctx.source) for d, v in got[k]]
    return obs
