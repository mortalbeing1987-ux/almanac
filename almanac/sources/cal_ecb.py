"""ECB 'Meetings of the Governing Council and the General Council' page: a
<dt>DD/MM/YYYY</dt><dd>description</dd> list. The decision is announced on
the last day of a monetary policy meeting ("(Day 2)", or a one-day meeting);
"non-monetary policy" meetings are skipped."""

from __future__ import annotations

import re
from datetime import datetime

from ._dates import text_of
from ._util import get, require, text

ITEM = re.compile(r"<dt>\s*(\d{2}/\d{2}/\d{4})\s*</dt>\s*<dd>(.*?)</dd>", re.S)


def parse(body: bytes):
    items = ITEM.findall(text(body))
    require(bool(items), "ECB <dt>date</dt><dd>meeting</dd> list")
    out = []
    for d, desc in items:
        t = text_of(desc).lower()
        if "monetary policy meeting" in t and "non-monetary" not in t and "day 1" not in t:
            out.append(datetime.strptime(d, "%d/%m/%Y").date())
    require(bool(out), "ECB monetary policy meetings")
    return out


def fetch(ctx):
    return parse(get(ctx, ctx.src["url"]))
