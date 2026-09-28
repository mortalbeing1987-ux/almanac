"""Bank of England Interactive Statistical Database (IADB), CSV (keyless):
`DATE,<series code>` then rows dated like `24 Sep 2026`. The request takes
Datefrom as dd/Mon/yyyy. SONIA reaches the database by 10:00 London on the
working day after it is first published (Open Government Licence v3.0)."""

from __future__ import annotations

from datetime import date

from ..model import Observation
from ._dates import month_number
from ._util import get, num, require, rows

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def boe_date(d: date) -> str:
    """dd/Mon/yyyy with English month names, whatever the locale."""
    return f"{d.day:02d}/{MONTHS[d.month - 1]}/{d.year}"


def parse(body: bytes, code: str) -> list[tuple[str, float]]:
    table = rows(body)
    require(bool(table) and [c.strip() for c in table[0][:2]] == ["DATE", code], f"IADB header DATE,{code}")
    out = []
    for r in table[1:]:
        if len(r) < 2 or not r[0].strip():
            continue
        try:
            day, mon, year = r[0].split()
            obs = date(int(year), month_number(mon), int(day))
        except (ValueError, KeyError):
            require(False, f"IADB date {r[0][:12]!r}")
        if (v := num(r[1])) is not None:
            out.append((obs.isoformat(), v))
    return out


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        url = ctx.src["url"].format(key=s["key"], since=boe_date(ctx.since))
        obs += [Observation(s["id"], d, v, ctx.source) for d, v in parse(get(ctx, url), s["key"])]
    return obs
