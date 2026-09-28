"""NY Fed reference rates API: {"refRates": [{"effectiveDate", "type", "percentRate", ...}]}."""

from __future__ import annotations

import json

from ..model import Observation
from ..registry import delivered_id, keys
from ._util import get, require, text, url_for


def parse(body: bytes) -> list[tuple[str, float]]:
    doc = json.loads(text(body))
    require(isinstance(doc, dict) and isinstance(doc.get("refRates"), list), "NY Fed refRates")
    out = []
    for r in doc["refRates"]:
        require("effectiveDate" in r and "percentRate" in r, "NY Fed rate fields")
        if r["percentRate"] is not None:
            out.append((r["effectiveDate"], float(r["percentRate"])))
    return out


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        for k in keys(s):
            for d, v in parse(get(ctx, url_for(ctx, k))):
                obs.append(Observation(delivered_id(s, k), d, v, ctx.source))
    return obs
