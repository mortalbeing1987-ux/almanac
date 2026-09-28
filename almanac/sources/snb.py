"""SNB data portal cube CSV (semicolon): metadata lines, a blank line, then
"Date";"D0";"Value" with one row per dimension per date. A series either names
the cube in `key` and one D0 code in `column` (SARON), or the cube in `cube`
and its D0 codes as `key` (SNB_SIGHT: TG, GI). Rows with an empty value are
skipped; a D0 code missing from the cube is a layout error."""

from __future__ import annotations

from ..model import Observation
from ..registry import delivered_id, keys
from ._util import get, num, require, rows, url_for


def parse(body: bytes, dim: str) -> list[tuple[str, float]]:
    table = rows(body, delimiter=";")
    head = next((i for i, r in enumerate(table) if r[:2] == ["Date", "D0"]), None)
    require(head is not None, "SNB Date/D0 header")
    out, seen = [], False
    for r in table[head + 1:]:
        if len(r) >= 3 and r[1] == dim:
            seen = True
            if (v := num(r[2])) is not None:
                out.append((r[0], v))
    require(seen, f"SNB D0 code {dim}")
    return out


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        if "cube" in s:
            body = get(ctx, url_for(ctx, s["cube"]))
            dims = [(delivered_id(s, k), k) for k in keys(s)]
        else:
            body = get(ctx, url_for(ctx, s["key"]))
            dims = [(s["id"], s["column"])]
        for sid, dim in dims:
            obs += [Observation(sid, d, v, ctx.source) for d, v in parse(body, dim)]
    return obs
