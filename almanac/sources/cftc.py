"""CFTC legacy Commitments of Traders, futures only, by contract market code.

  deafut.txt            latest report; no header, 129 comma-separated columns
  deacot<year>.zip      one per year from 1986: annual.txt, same columns with a header

Columns used (0-based, identical in both): 1 as-of date YYMMDD, 2 as-of date
YYYY-MM-DD, 3 CFTC contract market code, 7 open interest (all), 8/9
non-commercial long/short (all). The zips' header names are checked; in the
headerless weekly file the two date columns must agree. obs_date is the
as-of Tuesday. History is read from the yearly zips back to the fetch start;
the weekly file is read last and wins for the latest week. CFTC data is
public domain; the log still only shows counts.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime

from ..model import FetchError, Observation
from ..registry import delivered_id, keys
from ._util import get, num, require, text, url_for

FIRST_YEAR = 1986
HEADER = {2: "As of Date in Form YYYY-MM-DD", 3: "CFTC Contract Market Code",
          7: "Open Interest (All)", 8: "Noncommercial Positions-Long (All)",
          9: "Noncommercial Positions-Short (All)"}
# (code, as-of date) -> (open interest, non-commercial long, non-commercial short)
Positions = dict[tuple[str, str], tuple[float, float, float]]
# registry `measures` -> value from (open interest, long, short)
MEASURES = {"NC_LONG": lambda oi, lo, sh: lo, "NC_SHORT": lambda oi, lo, sh: sh,
            "NC_NET": lambda oi, lo, sh: lo - sh, "OI": lambda oi, lo, sh: oi}


def parse(lines, codes: set[str], header: bool) -> Positions:
    lines = iter(lines)
    head_line = next(lines, "") if header else None
    marks = tuple(f",{c}," for c in codes)
    # other markets' rows are skipped before CSV parsing (the full history is ~600k rows)
    reader = csv.reader(line for line in lines if any(m in line for m in marks))
    if header:
        head = [h.strip() for h in next(csv.reader([head_line]), [])]
        for i, name in HEADER.items():
            require(len(head) > i and head[i] == name, f"CFTC column {i} {name!r}")
    out: Positions = {}
    for r in reader:
        if len(r) < 10 or r[3].strip() not in codes:
            continue
        day = r[2].strip()
        try:
            same = datetime.strptime(r[1].strip(), "%y%m%d").date().isoformat() == day
        except ValueError:
            same = False
        require(same, "CFTC as-of date columns 1/2")
        vals = [num(r[i]) for i in (7, 8, 9)]
        if None not in vals:
            out[(r[3].strip(), day)] = tuple(vals)
    return out


def parse_zip(body: bytes, codes: set[str]) -> Positions:
    try:
        z = zipfile.ZipFile(io.BytesIO(body))
        members = [i for i in z.infolist() if i.filename.lower().endswith(".txt")]
        require(len(members) == 1, "CFTC yearly zip with one .txt member")
        with z.open(members[0]) as f:
            return parse(io.TextIOWrapper(f, encoding="latin-1", newline=""), codes, header=True)
    except zipfile.BadZipFile:
        raise FetchError("error", "unexpected layout: CFTC yearly file is not a zip") from None


def fetch(ctx) -> list[Observation]:
    obs = []
    for s in ctx.series:
        by_code = {s["contracts"][k]: k for k in keys(s)}
        codes = set(by_code)
        pos: Positions = {}
        for year in range(max(ctx.since.year, FIRST_YEAR), ctx.today.year + 1):
            try:
                pos.update(parse_zip(get(ctx, ctx.src["history_url"].format(year=year)), codes))
            except FetchError as e:
                # Early January, before the new year's first report, the
                # current year's zip may not exist yet; the weekly file covers it.
                if not (year == ctx.today.year and ctx.today.timetuple().tm_yday <= 20
                        and e.reason.startswith("HTTP 404")):
                    raise
        latest = parse(io.StringIO(text(get(ctx, url_for(ctx)))), codes, header=False)
        missing = sorted(by_code[c] for c in codes - {c for c, _ in latest})
        require(not missing, f"CFTC weekly report lists every contract (missing {missing})")
        pos.update(latest)
        for (code, day), p in sorted(pos.items()):
            base = delivered_id(s, by_code[code])
            obs += [Observation(f"{base}_{m}", day, MEASURES[m](*p), ctx.source)
                    for m in s["measures"]]
    return obs
