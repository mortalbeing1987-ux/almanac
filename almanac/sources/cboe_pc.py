"""Cboe put/call volume ratios (put volume / call volume), six product groups.

Two Cboe publications, stitched at the day one ends and the other begins:
  * history CSVs, one file per group, up to 2019-10-04 (the files carry a disclaimer
    line first, then a DATE header; calls and puts are columns of volume);
  * one JSON file per trading day from 2019-10-07 (`..._daily_options`), listing every
    product group with its VOLUME and OPEN INTEREST call/put counts. A day without a
    file (weekend, market holiday) answers 403 AccessDenied -- that is "no trading
    that day", not an outage, so it is skipped without a request when it is a weekend
    and tolerated when it is a weekday.
The ratio is computed from the volumes (put / call, full precision), not read from the
published 2-decimal ratio. Definition caveat: the CSV era is the Cboe Options Exchange
file of each group; the JSON era sums all Cboe exchanges; "SPX" is SPX in the CSV era and
SPX + SPXW in the JSON era. Expect a small level shift at 2019-10-07 for SPX.

Catch-up is chunked: one call fetches at most MAX_DAYS_PER_RUN weekdays, oldest first, so
the first delivery of the full history takes a few scheduled runs (each run resumes from
the last delivered date) instead of one request-per-second hour that would outlast the
workflow's time limit. A run of BLIND_DAYS weekdays in a row with no file is an error
(Cboe changed the layout or blocks us), never "up to date".

Cboe data is redistribution-restricted: only counts are ever logged.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from ..model import FetchError, Observation
from ..registry import delivered_id, keys
from ._util import get, num, require, rows

JSON_FROM = date(2019, 10, 7)   # first per-day JSON; the history CSVs end 2019-10-04
MAX_DAYS_PER_RUN = 400          # weekdays fetched per call (about 8 minutes at 1 request/s)
BLIND_DAYS = 5                  # this many weekdays in a row without a file = error

# key -> (section in the daily JSON, history CSV)
GROUPS = {
    "TOTAL": ("SUM OF ALL PRODUCTS", "totalpc"),
    "INDEX": ("INDEX OPTIONS", "indexpc"),
    "ETP": ("EXCHANGE TRADED PRODUCTS", "etppc"),
    "EQUITY": ("EQUITY OPTIONS", "equitypc"),
    "VIX": ("CBOE VOLATILITY INDEX (VIX)", "vixpc"),
    "SPX": ("SPX + SPXW", "spxpc"),
}


def ratio(call: float | None, put: float | None) -> float | None:
    return None if call is None or put is None or call <= 0 else put / call


def parse_csv(body: bytes, name: str) -> list[tuple[str, float]]:
    table = rows(body)
    hi = next((i for i, r in enumerate(table) if r and r[0].strip().upper() == "DATE"), None)
    require(hi is not None, f"Cboe {name} DATE header")
    head = [c.strip().upper() for c in table[hi]]
    put_i = next((i for i, h in enumerate(head) if "PUT" in h and "RATIO" not in h), None)
    call_i = next((i for i, h in enumerate(head) if "CALL" in h and "RATIO" not in h), None)
    require(put_i is not None and call_i is not None, f"Cboe {name} put and call volume columns")
    out = []
    for r in table[hi + 1:]:
        if len(r) <= max(put_i, call_i) or not r[0].strip():
            continue
        try:
            d = datetime.strptime(r[0].strip(), "%m/%d/%Y").date()
        except ValueError:
            continue  # a trailing note line
        if (v := ratio(num(r[call_i]), num(r[put_i]))) is not None:
            out.append((d.isoformat(), v))
    return out


def parse_day(body: bytes) -> dict[str, float]:
    """{key: put/call ratio} from one daily JSON; groups with no call volume are left out."""
    try:
        doc = json.loads(body.decode("utf-8-sig"))
    except ValueError:
        raise FetchError("error", "unexpected layout: Cboe daily options is not JSON") from None
    require(isinstance(doc, dict) and "ratios" in doc and "SUM OF ALL PRODUCTS" in doc, "Cboe daily options sections")
    out = {}
    for key, (section, _) in GROUPS.items():
        vol = next((x for x in doc.get(section, []) if str(x.get("name", "")).upper() == "VOLUME"), None)
        if vol is not None and (v := ratio(vol.get("call"), vol.get("put"))) is not None:
            out[key] = v
    return out


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def fetch(ctx) -> list[Observation]:
    obs: list[Observation] = []
    for s in ctx.series:
        wanted = [k for k in keys(s) if k in GROUPS]
        require(bool(wanted), "PUTCALL keys")
        if ctx.since < JSON_FROM:
            for k in wanted:
                url = ctx.src["csv_url"].format(file=GROUPS[k][1])
                for d, v in parse_csv(get(ctx, url), k):
                    if ctx.since.isoformat() <= d < JSON_FROM.isoformat():
                        obs.append(Observation(delivered_id(s, k), d, v, ctx.source))
        days = list(weekdays(max(ctx.since, JSON_FROM), ctx.today))[:MAX_DAYS_PER_RUN]
        blind = 0
        for d in days:
            try:
                body = get(ctx, ctx.src["url"].format(date=d.isoformat()))
            except FetchError as e:
                if e.kind == "error" and "HTTP 403" in e.reason:   # no file: a market holiday
                    blind += 1
                    if blind >= BLIND_DAYS:
                        raise FetchError("error", f"no Cboe put/call file for {BLIND_DAYS} weekdays in a row "
                                                  f"up to {d.isoformat()}") from None
                    continue
                raise
            blind = 0
            for k, v in parse_day(body).items():
                if k in wanted:
                    obs.append(Observation(delivered_id(s, k), d.isoformat(), v, ctx.source))
    return obs
