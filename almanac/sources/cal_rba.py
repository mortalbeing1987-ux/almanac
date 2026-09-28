"""RBA 'Board Meeting Schedules' page: a table per year, captioned "Board
meeting schedules <year>", with a row per month whose first cell is the
Monetary Policy Board meeting, e.g. "2–3 February" or "30 September – 1 October".
The decision is announced on the second day."""

from __future__ import annotations

import re
from datetime import date

from ._dates import month_number, text_of
from ._util import get, require, text

TABLE = re.compile(r"<caption[^>]*>\s*Board meeting schedules (\d{4})\s*</caption>(.*?)</table>", re.S)
ROW = re.compile(r'<th scope="row">.*?</th>\s*<td[^>]*>(.*?)</td>', re.S)
DAY = re.compile(r"(\d{1,2})\s*([A-Z][a-z]+)?")


def parse(body: bytes) -> list[date]:
    tables = TABLE.findall(text(body))
    require(bool(tables), "RBA 'Board meeting schedules <year>' table")
    out = []
    for year, block in tables:
        for cell in ROW.findall(block):
            parts = DAY.findall(text_of(cell))
            if not parts:
                continue
            day, month = parts[-1]
            require(bool(month), f"RBA month in {text_of(cell)!r}")
            out.append(date(int(year), month_number(month), int(day)))
    require(bool(out), "RBA Monetary Policy Board rows")
    return out


def fetch(ctx) -> list[date]:
    return parse(get(ctx, ctx.src["url"]))
