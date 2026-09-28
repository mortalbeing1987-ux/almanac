"""Census international trade API (keyed): monthly goods by partner, not
seasonally adjusted, Census basis, in USD (dollars, not millions).

One request per flow returns every partner for every month from the start
month: `get=CTY_CODE,CTY_NAME,<field>&time=from+YYYY-MM&CTY_CODE=a&CTY_CODE=b...`.
The reply is a JSON table: a header row, then one row per (partner, month);
the CTY_CODE predicate is echoed as a last column. The key goes only into the
request (`key` query parameter) and is never part of an error message.
"""

from __future__ import annotations

import json
import urllib.parse

from ..model import Observation
from ..registry import delivered_ids
from ._util import get, num, require, text

FIRST_MONTH = "2010-01"  # the timeseries API starts here (probe 2026-09-28)


def parse(body: bytes, field: str) -> list[tuple[str, str, float]]:
    """(partner code, YYYY-MM-01, value); an empty reply is no rows."""
    if not body.strip():
        return []
    try:
        table = json.loads(text(body))
    except ValueError:
        table = None
    require(isinstance(table, list) and table and isinstance(table[0], list), "Census JSON table")
    head = table[0]
    require(all(h in head for h in ("CTY_CODE", field, "time")), f"Census header CTY_CODE/{field}/time")
    ci, vi, ti = head.index("CTY_CODE"), head.index(field), head.index("time")
    out = []
    for r in table[1:]:
        require(len(r) == len(head), "Census row width")
        month = r[ti]
        require(len(month) == 7 and month[4] == "-", "Census time YYYY-MM")
        if (v := num(r[vi])) is not None:
            out.append((r[ci], f"{month}-01", v))
    return out


def fetch(ctx) -> list[Observation]:
    obs = []
    start = max(ctx.since.strftime("%Y-%m"), FIRST_MONTH)
    for s in ctx.series:
        code_to_partner = {code: p for p, code in s["partners"].items()}
        for key in s["key"]:
            flow = s["flows"][key]
            url = ctx.src["url"].format(flow=flow["flow"], field=flow["field"], month=start)
            url += "".join("&CTY_CODE=" + urllib.parse.quote(c, safe="") for c in code_to_partner)
            got = parse(get(ctx, url), flow["field"])
            seen = {c for c, _, _ in got}
            missing = sorted(code_to_partner[c] for c in set(code_to_partner) - seen)
            require(not got or not missing, f"Census partners in reply (missing {missing})")
            ids = {p: i for p, i in zip(s["measures"], delivered_ids(s, key))}
            obs += [Observation(ids[code_to_partner[c]], d, v, ctx.source)
                    for c, d, v in got if c in code_to_partner]
    return obs
