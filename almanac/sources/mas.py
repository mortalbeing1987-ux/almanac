"""MAS API gateway (keyed): {"name": ..., "elements": [{"end_of_day": "YYYY-MM-DD",
"preliminary": 0|1, "<field>": "1.23" | null, ...}]}. The series key names the
field (e.g. sora). Preliminary values are delivered; a later final value that
differs arrives as a revision."""

from __future__ import annotations

import json

from ..model import Observation
from ._util import get, num, require, text, url_for


def parse(body: bytes, field: str) -> list[tuple[str, float]]:
    doc = json.loads(text(body))
    require(isinstance(doc, dict) and isinstance(doc.get("elements"), list), "MAS elements")
    out = []
    for e in doc["elements"]:
        require("end_of_day" in e and field in e, f"MAS end_of_day/{field}")
        v = e[field]
        if v is not None and (x := num(str(v))) is not None:
            out.append((e["end_of_day"], x))
    return out


def fetch(ctx) -> list[Observation]:
    body = get(ctx, url_for(ctx))
    return [Observation(s["id"], d, v, ctx.source)
            for s in ctx.series for d, v in parse(body, s["key"])]
