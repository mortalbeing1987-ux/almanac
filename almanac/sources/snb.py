"""SNB data portal cube CSV (semicolon): metadata lines, a blank line, then
"Date";"D0";"Value" with one row per dimension per date. The series' `column`
names the D0 code (e.g. SARON); rows with an empty value are skipped."""

from __future__ import annotations

from ..model import Observation
from ._util import get, num, require, rows, url_for


def parse(body: bytes, dim: str) -> list[tuple[str, float]]:
    table = rows(body, delimiter=";")
    head = next((i for i, r in enumerate(table) if r[:2] == ["Date", "D0"]), None)
    require(head is not None, "SNB Date/D0 header")
    out = []
    for r in table[head + 1:]:
        if len(r) >= 3 and r[1] == dim and (v := num(r[2])) is not None:
            out.append((r[0], v))
    return out


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        for d, v in parse(get(ctx, url_for(ctx, s["key"])), s["column"]):
            obs.append(Observation(s["id"], d, v, ctx.source))
    return obs
