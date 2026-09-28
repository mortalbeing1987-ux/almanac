"""BEA API, International Transactions (ITA), quarterly SA by partner (keyed).

Used as a CROSS-CHECK of the geo workbook only (never delivered). One request
per indicator: comma-separated areas and years. TimePeriod `2026Q2` becomes
obs_date 2026-04-01 (the quarter's first day).

BEA's replies echo every request parameter -- including UserID, the key --
under BEAAPI.Request. Nothing from a reply body is ever put into an error
message except BEA's numeric error code.
"""

from __future__ import annotations

import json

from ..model import FetchError, Observation
from ..registry import delivered_ids
from ._util import get, num, require, text

QUARTER_START = {"1": "01", "2": "04", "3": "07", "4": "10"}


def quarter_date(period: str) -> str:
    require(len(period) == 6 and period[4] == "Q" and period[5] in QUARTER_START, "ITA TimePeriod YYYYQn")
    return f"{period[:4]}-{QUARTER_START[period[5]]}-01"


def parse(body: bytes) -> list[tuple[str, str, float]]:
    """(area, obs_date, value in USD millions)."""
    try:
        doc = json.loads(text(body))
    except ValueError:
        raise FetchError("error", "unexpected layout: BEA API reply is not JSON") from None
    api = doc.get("BEAAPI", {}) if isinstance(doc, dict) else {}
    res = api.get("Results", {})
    err = api.get("Error") or (res.get("Error") if isinstance(res, dict) else None)
    if err:
        err = err[0] if isinstance(err, list) else err
        code = str(err.get("APIErrorCode", "?")) if isinstance(err, dict) else "?"
        raise FetchError("error", f"BEA API error code {code[:10]}")
    require(isinstance(res, dict) and isinstance(res.get("Data"), list), "BEA ITA Results.Data")
    out = []
    for d in res["Data"]:
        require(all(k in d for k in ("AreaOrCountry", "TimePeriod", "DataValue", "UNIT_MULT")), "BEA ITA fields")
        require(str(d["UNIT_MULT"]) == "6", "BEA ITA values in millions (UNIT_MULT 6)")
        if (v := num(str(d["DataValue"]))) is not None:
            out.append((d["AreaOrCountry"], quarter_date(d["TimePeriod"]), v))
    return out


def fetch(ctx) -> list[Observation]:
    obs = []
    years = ",".join(str(y) for y in range(ctx.since.year, ctx.today.year + 1))
    for s in ctx.series:
        area_to_partner = {a: p for p, a in s["areas"].items()}
        for key in s["key"]:
            url = ctx.src["url"].format(key=key, years=years, areas=",".join(area_to_partner))
            ids = dict(zip(s["measures"], delivered_ids(s, key)))
            obs += [Observation(ids[area_to_partner[a]], d, v, ctx.source)
                    for a, d, v in parse(get(ctx, url)) if a in area_to_partner]
    return obs
