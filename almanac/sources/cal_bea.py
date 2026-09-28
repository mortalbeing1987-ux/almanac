"""BEA release schedule page: tables headed "Year <yyyy>", one row per release
with <div class="release-date">Month D</div> and the release subject. GDP
releases are the subjects that start with "GDP (" (advance/second/third
estimates) or "Gross Domestic Product"."""

from __future__ import annotations

import re
from datetime import datetime

from ._dates import text_of
from ._util import get, require, text

YEAR = re.compile(r">\s*Year (\d{4})\s*<")
ROW = re.compile(r'<tr class="scheduled-releases-type[^"]*">(.*?)</tr>', re.S)
DATE = re.compile(r'class="release-date">(.*?)</div>', re.S)
SUBJECT = re.compile(r'headers="view-field-scheduled-release-subject-table-column">(.*?)</td>', re.S)
GDP = re.compile(r"^(GDP\s*\(|Gross Domestic Product)")


def parse(body: bytes):
    page = text(body)
    years = [(m.start(), int(m.group(1))) for m in YEAR.finditer(page)]
    require(bool(years), "BEA 'Year <yyyy>' schedule tables")
    out = []
    for i, (start, year) in enumerate(years):
        end = years[i + 1][0] if i + 1 < len(years) else len(page)
        for row in ROW.findall(page[start:end]):
            d, s = DATE.search(row), SUBJECT.search(row)
            if d and s and GDP.match(text_of(s.group(1))):
                out.append(datetime.strptime(f"{text_of(d.group(1))} {year}", "%B %d %Y").date())
    return out


def fetch(ctx):
    return parse(get(ctx, ctx.src["url"]))
