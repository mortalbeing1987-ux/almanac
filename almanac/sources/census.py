"""Census international trade API (keyed): monthly goods, not seasonally
adjusted, Census basis, in USD (dollars, not millions). Exports are FAS value
(ALL_VAL_MO), general imports customs value (GEN_VAL_MO).

Three kinds of series share the source (one host, one key):
  - by partner (TRADE_CEN_M): `hs` endpoint, all commodities, one request per
    flow for every partner (repeated CTY_CODE) and every month from the start;
  - one commodity by partner (TRADE_CEN_GOLD, HS 7108): the same plus
    COMM_LVL=HS4 and <E|I>_COMMODITY=<code>. Census omits partner-months with
    no trade, so a month present in the reply but missing for a partner is 0;
  - world by industry (TRADE_CEN_NAICS): `naics` endpoint, CTY_CODE=- (world),
    one request per flow per industry (NAICS=<code>); see _by_industry.
The reply is a JSON table: a header row, then one row per (key, month);
predicates are echoed as extra columns. The key goes only into the request
(`key` query parameter) and is never part of an error message.
"""

from __future__ import annotations

import json
import urllib.parse

from ..model import Observation
from ..registry import delivered_ids
from ._util import get, num, require, text

FIRST_MONTH = "2010-01"  # the hs timeseries starts here (probe 2026-09-28)


def table(body: bytes, need: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows as {column: value}; an empty reply is no rows."""
    if not body.strip():
        return []
    try:
        t = json.loads(text(body))
    except ValueError:
        t = None
    require(isinstance(t, list) and t and isinstance(t[0], list), "Census JSON table")
    head = t[0]
    require(all(h in head for h in need), f"Census header {'/'.join(need)}")
    out = []
    for r in t[1:]:
        require(len(r) == len(head), "Census row width")
        row = dict(zip(head, r))
        require(len(row["time"]) == 7 and row["time"][4] == "-", "Census time YYYY-MM")
        out.append(row)
    return out


def parse(body: bytes, field: str, by: str = "CTY_CODE") -> list[tuple[str, str, float]]:
    """(code, YYYY-MM-01, value) for the `by` column (partner or industry)."""
    return [(r[by], f"{r['time']}-01", v) for r in table(body, (by, field, "time"))
            if (v := num(r[field])) is not None]


def _url(ctx, flow: dict, endpoint: str, get_cols: str, time: str, predicates: list[tuple[str, str]]) -> str:
    url = ctx.src["url"].format(flow=flow["flow"], endpoint=endpoint, get=get_cols, time=time)
    return url + "".join(f"&{k}=" + urllib.parse.quote(v, safe="") for k, v in predicates)


def _by_partner(ctx, s: dict, key: str, start: str) -> list[Observation]:
    flow = s["flows"][key]
    code_to_partner = {code: p for p, code in s["partners"].items()}
    preds = [("CTY_CODE", c) for c in code_to_partner]
    if "commodity" in s:
        preds = [("COMM_LVL", "HS4"), (flow["commodity"], s["commodity"])] + preds
        get_cols = f"CTY_CODE,{flow['field']},{flow['commodity']}"
    else:
        get_cols = f"CTY_CODE,CTY_NAME,{flow['field']}"
    got = parse(get(ctx, _url(ctx, flow, "hs", get_cols, f"from+{start}", preds)), flow["field"])
    if "commodity" in s:
        require(all(c in code_to_partner for c, _, _ in got), "Census reply only has requested partners")
        months = {d for _, d, _ in got}
        have = {(c, d) for c, d, _ in got}
        got += [(c, d, 0.0) for c in code_to_partner for d in months if (c, d) not in have]
    else:
        seen = {c for c, _, _ in got}
        missing = sorted(code_to_partner[c] for c in set(code_to_partner) - seen)
        require(not got or not missing, f"Census partners in reply (missing {missing})")
    ids = dict(zip(s["measures"], delivered_ids(s, key)))
    return [Observation(ids[code_to_partner[c]], d, v, ctx.source) for c, d, v in got if c in code_to_partner]


def _by_industry(ctx, s: dict, key: str, start: str) -> list[Observation]:
    """One request per industry: `NAICS=<code>&CTY_CODE=-` answers the whole
    history in under a second, while any COMM_LVL-filtered exports query
    timed out at 110 s even for one month (probe 2026-09-29). A month the flow
    publishes (seen for any industry) but that is missing for an industry after
    its first appearance is 0: Census omits code-months with no trade."""
    flow = s["flows"][key]
    ids = dict(zip(s["measures"], delivered_ids(s, key)))
    got: dict[str, list[tuple[str, float]]] = {}
    for code in s["measures"]:
        url = _url(ctx, flow, "naics", f"NAICS,{flow['field']}", f"from+{start}", [("NAICS", code), ("CTY_CODE", "-")])
        rows = parse(get(ctx, url), flow["field"], by="NAICS")
        require(all(c == code for c, _, _ in rows), f"Census NAICS reply only for {code}")
        got[code] = [(d, v) for _, d, v in rows]
    published = {d for rows in got.values() for d, _ in rows}
    obs = []
    for code, rows in got.items():
        have = {d for d, _ in rows}
        first = min(have, default=None)
        rows += [(d, 0.0) for d in published if first and d > first and d not in have]
        obs += [Observation(ids[code], d, v, ctx.source) for d, v in rows]
    return obs


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        start = max(ctx.since.strftime("%Y-%m"), s.get("first_month", FIRST_MONTH))
        for key in s["key"]:
            obs += (_by_industry if s.get("endpoint") == "naics" else _by_partner)(ctx, s, key, start)
    return obs
