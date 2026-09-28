"""BoJ Time-Series Data Search API, CSV: STATUS/MESSAGE/PARAMETER lines, a
NEXTPOSITION line (non-empty = more pages), then SERIES_CODE,...,SURVEY_DATES,VALUES
with dates YYYYMMDD and 'null' for missing."""

from __future__ import annotations

from datetime import datetime

from ..model import FetchError, Observation
from ._util import get, num, require, rows, url_for

MAX_PAGES = 50


def parse(body: bytes, code: str) -> tuple[list[tuple[str, float]], str]:
    table = rows(body)
    meta = {r[0]: r[1:] for r in table if r and r[0] in ("STATUS", "MESSAGE", "NEXTPOSITION")}
    status = (meta.get("STATUS") or [""])[0]
    if status != "200":
        raise FetchError("error", f"BoJ status {status}: {(meta.get('MESSAGE') or [''])[0][:80]}")
    head = next((i for i, r in enumerate(table) if r and r[0] == "SERIES_CODE"), None)
    require(head is not None, "BoJ SERIES_CODE header")
    cols = table[head]
    require("SURVEY_DATES" in cols and "VALUES" in cols, "BoJ SURVEY_DATES/VALUES columns")
    di, vi = cols.index("SURVEY_DATES"), cols.index("VALUES")
    out = []
    for r in table[head + 1:]:
        if len(r) > vi and r[0] == code and (v := num(r[vi])) is not None:
            out.append((datetime.strptime(r[di], "%Y%m%d").date().isoformat(), v))
    nxt = (meta.get("NEXTPOSITION") or [""])[0].strip()
    return out, nxt


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        url = url_for(ctx, s["key"])
        for _ in range(MAX_PAGES):
            points, nxt = parse(get(ctx, url), s["key"])
            obs += [Observation(s["id"], d, v, ctx.source) for d, v in points]
            if not nxt:
                break
            url = f"{url_for(ctx, s['key'])}&startPosition={nxt}"
        else:
            raise FetchError("error", f"BoJ pagination exceeded {MAX_PAGES} pages")
    return obs
