"""SNB 'Time schedule' (event schedule) page: a list of upcoming events, each
with <span class="publication-date">DD.MM.YYYY</span>, a time, and an <h3>
title. Monetary policy assessments are the entries titled "Monetary policy
assessment ..." (the later "Summary of monetary policy discussion" is not)."""

from __future__ import annotations

import re
from datetime import datetime

from ._dates import text_of
from ._util import get, require, text

ITEM = re.compile(r'publication-date[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*</span>.*?<h3[^>]*>(.*?)</h3>', re.S)


def parse(body: bytes):
    items = ITEM.findall(text(body))
    require(bool(items), "SNB event list (publication-date + h3 title)")
    return [datetime.strptime(d, "%d.%m.%Y").date() for d, title in items
            if text_of(title).lower().startswith("monetary policy assessment")]


def fetch(ctx):
    return parse(get(ctx, ctx.src["url"]))
