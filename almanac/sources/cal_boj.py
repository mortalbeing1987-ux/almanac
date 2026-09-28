"""BoJ 'Monetary Policy Meetings' page: a table per year under <h2 id="p<year>">
whose first column is the meeting, e.g. "Jan. 21 (Thurs.), 22 (Fri.)" or
"Apr. 30 (Thurs.), May 1 (Fri.)". The decision comes on the last day; the BoJ
publishes no fixed announcement time."""

from __future__ import annotations

import re
from datetime import date

from ._dates import month_number, text_of
from ._util import get, require, text

SECTION = re.compile(r'<h2 id="p(\d{4})">(.*?)(?=<h2|\Z)', re.S)
FIRST_CELL = re.compile(r"<tr>\s*<td[^>]*>(.*?)</td>", re.S)
DAY = re.compile(r"(?:([A-Z][a-z]+)\.?\s+)?(\d{1,2})\s*\(")


def last_day(cell: str, year: int) -> date | None:
    month, day = None, None
    for m, d in DAY.findall(cell):
        month = month_number(m) if m else month
        day = int(d)
    return date(year, month, day) if month and day else None


def parse(body: bytes) -> list[date]:
    sections = SECTION.findall(text(body))
    require(bool(sections), "BoJ year sections (h2 id=p<year>)")
    out = []
    for year, block in sections:
        for cell in FIRST_CELL.findall(block):
            if (d := last_day(text_of(cell), int(year))) is not None:
                out.append(d)
    require(bool(out), "BoJ meeting rows")
    return out


def fetch(ctx) -> list[date]:
    return parse(get(ctx, ctx.src["url"]))
