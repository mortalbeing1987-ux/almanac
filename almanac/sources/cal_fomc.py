"""Federal Reserve FOMC calendar page: per-year sections headed "<year> FOMC
Meetings", each meeting a row with `fomc-meeting__month` (e.g. "March",
"Apr/May") and `fomc-meeting__date` (e.g. "17-18*", "30-1"). The policy
decision is announced on the last day of the meeting. Rows that are not a plain
day range (notation votes, unscheduled meetings) are skipped."""

from __future__ import annotations

import re
from datetime import date

from ._dates import month_number, text_of
from ._util import get, require, text

ROW = re.compile(r'fomc-meeting__month[^>]*>(.*?)</div>\s*<div[^>]*fomc-meeting__date[^>]*>(.*?)</div>',
                 re.S)
DAYS = re.compile(r"^(\d{1,2})(?:\s*-\s*(\d{1,2}))?\*?$")


def parse(body: bytes) -> list[date]:
    page = text(body)
    years = [(m.start(), int(m.group(1))) for m in re.finditer(r"(\d{4}) FOMC Meetings", page)]
    require(bool(years), "FOMC '<year> FOMC Meetings' headings")
    out = []
    for i, (start, year) in enumerate(years):
        end = years[i + 1][0] if i + 1 < len(years) else len(page)
        for month_html, day_html in ROW.findall(page[start:end]):
            m = DAYS.match(text_of(day_html))
            if not m:
                continue
            last_month = text_of(month_html).split("/")[-1]
            out.append(date(year, month_number(last_month), int(m.group(2) or m.group(1))))
    require(bool(out), "FOMC meeting rows")
    return out


def fetch(ctx) -> list[date]:
    return parse(get(ctx, ctx.src["url"]))
