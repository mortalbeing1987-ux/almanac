"""Step 0: probe every source in almanac/series.toml for reachability.

Prints a markdown table (and appends it to $GITHUB_STEP_SUMMARY when run in
Actions). Records only status, size, content type and timing -- never the
data itself, so the public workflow log carries no restricted values.

    python scripts/probe_sources.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "almanac" / "series.toml"
UA = "almanac-source-probe/0.1 (+https://github.com/mortalbeing1987-ux/almanac)"
TIMEOUT = 45


def probe_urls(reg: dict) -> list[tuple[str, str]]:
    """(label, url) pairs: one per source, plus one per FRED key family."""
    out: list[tuple[str, str]] = []
    year = date.today().year
    fmt = {"year": year, "prev_year": year - 1, "n": 5,
           "since": (date.today() - timedelta(days=14)).isoformat()}
    first_key: dict[str, str] = {}
    for s in reg.get("series", []):
        k = s["key"][0] if isinstance(s["key"], list) else s["key"]
        if k != "TBD":
            first_key.setdefault(s["source"], k)
    for name, src in reg["sources"].items():
        url = src["url"]
        if "{area}" in url:
            continue  # probed per (key, area) below
        if "{flow}" in url:
            url = "TBD"  # template needs a month; probed via its candidates
        if url == "TBD":
            cands = src.get("candidates", [])
            out.extend((f"{name}?{i}", c) for i, c in enumerate(cands, 1))
            if not cands:
                out.append((name, ""))
            continue
        key = first_key.get(name, "")
        if name == "cboe":
            key = "VIX"
        out.append((name, url.format(key=key, **fmt)))
    # candidate keys on sources that already have a url (e.g. SARON on snb)
    # sources whose series have `areas`: one probe per (key, area)
    for s in reg.get("series", []):
        if "areas" in s:
            url = reg["sources"][s["source"]]["url"]
            for k in s["key"]:
                for a in s["areas"]:
                    out.append((f"{s['source']}:{k}:{a}",
                                url.format(key=k, area=a, **fmt)))
    for s in reg.get("series", []):
        for c in s.get("candidates", []):
            url = reg["sources"][s["source"]]["url"]
            out.append((f"{s['id']}?{c}", url.format(key=c, **fmt)))
    # a few extra FRED probes so a partial block is visible
    for k in ("BAMLH0A0HYM2", "BAMLC0A4CBBB", "DFII10", "T10Y3M", "DTWEXBGS", "DTWEXAFEGS", "DTWEXEMEGS", "ICSA", "NFCI", "CPIAUCSL"):
        out.append((f"fred:{k}", reg["sources"]["fred"]["url"].format(key=k, **fmt)))
    # every CBOE file (VIX is already probed above as the source's first key)
    for k in ("VIX3M", "VVIX"):
        out.append((f"cboe:{k}", reg["sources"]["cboe"]["url"].format(key=k, **fmt)))
    # full-history FRED files for the weekly / lagged series (cosd from 1900)
    for k in ("NFCI", "ICSA", "DTWEXBGS", "T10Y3M"):
        out.append((f"fredfull:{k}", reg["sources"]["fred"]["url"].format(key=k, **dict(fmt, since="1900-01-01"))))
    # PR 8 plan: BEA ITA parameter values (names only) and a multi-area call;
    # Census partner codes and history depth
    bea_base = "https://apps.bea.gov/api/data?method=GetParameterValues&DataSetName=ITA&ResultFormat=JSON&ParameterName="
    out.append(("beameta:areas", bea_base + "AreaOrCountry"))
    out.append(("beameta:indicators", bea_base + "Indicator"))
    out.append(("beameta:multi", "https://apps.bea.gov/api/data?method=GetData&DataSetName=ITA&Frequency=QSA&Year=ALL"
                "&ResultFormat=JSON&Indicator=ExpServ&AreaOrCountry=Canada,Japan,Switzerland,Australia,EuroArea,EuropeanUnion"))
    cen = "https://api.census.gov/data/timeseries/intltrade/exports/hs?get=CTY_CODE,CTY_NAME,ALL_VAL_MO&time="
    out.append(("census:partners", cen + "2026-06"))
    out.append(("census:span", "https://api.census.gov/data/timeseries/intltrade/exports/hs?get=CTY_CODE,ALL_VAL_MO&CTY_CODE=5800&time=from+2000-01"))
    # quarterly freshness: when did each quarter first appear in the geo workbook?
    months = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
              "october", "november", "december"]
    for yr, last in ((2025, 12), (2026, 7)):
        for m in range(1, last + 1):
            # the page for data month m is published in the year the release happens
            pub = yr if m < 11 or yr == 2026 else yr + 1
            for py in sorted({yr, pub}):
                out.append((f"beahist:{yr}-{m:02d}@{py}",
                            f"https://www.bea.gov/news/{py}/us-international-trade-goods-and-services-{months[m - 1]}-{yr}"))
    # euro-area composition: EA vs the sum of its members, picked by name (booleans only)
    out.append(("census:eacheck", "https://api.census.gov/data/timeseries/intltrade/exports/hs?get=CTY_CODE,CTY_NAME,ALL_VAL_MO"
                "&time=from+2022-11"))
    cen2 = ("https://api.census.gov/data/timeseries/intltrade/exports/hs?get=CTY_CODE,CTY_NAME,ALL_VAL_MO"
            "&time=from+2026-04&CTY_CODE=-&CTY_CODE=0025&CTY_CODE=6021")
    out.append(("census:multi", cen2))
    out.append(("census:ea-notice", "https://www.census.gov/foreign-trade/statistics/notices/20230105_euro_area_change.pdf"))
    out.append(("census:groupings", "https://www.census.gov/foreign-trade/guide/sec5.html"))
    out.append(("census:statsinfo", "https://www.census.gov/foreign-trade/reference/guides/tradestatsinfo.html"))
    out.append(("beameta:error", "https://apps.bea.gov/api/data?method=GetData&DataSetName=ITA&Frequency=QSA&Year=2025"
                "&ResultFormat=JSON&Indicator=NoSuchIndicator&AreaOrCountry=Japan"))
    out.append(("beameta:years", "https://apps.bea.gov/api/data?method=GetData&DataSetName=ITA&Frequency=QSA&Year=2024,2025,2026"
                "&ResultFormat=JSON&Indicator=ExpGds&AreaOrCountry=EU,Japan"))
    # PR 7: CORRA (Bank of Canada Valet) and SONIA (Bank of England IADB), plus
    # each bank's terms pages (text excerpts only)
    out.append(("corra:csv", "https://www.bankofcanada.ca/valet/observations/AVG.INTWO/csv?start_date=" + fmt["since"]))
    out.append(("corra:json", "https://www.bankofcanada.ca/valet/observations/AVG.INTWO/json?start_date=" + fmt["since"]))
    out.append(("corra:full", "https://www.bankofcanada.ca/valet/observations/AVG.INTWO/csv"))
    out.append(("corra:terms", "https://www.bankofcanada.ca/terms/"))
    iadb = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes"
            "&Datefrom={frm}&Dateto=now&SeriesCodes=IUDSOIA&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
    since = date.fromisoformat(fmt["since"])
    out.append(("sonia:csv", iadb.format(frm=since.strftime("%d/%b/%Y"))))
    out.append(("sonia:full", iadb.format(frm="01/Jan/1990")))
    out.append(("sonia:terms", "https://www.bankofengland.co.uk/markets/sonia-benchmark/sonia-key-features-and-policies"))
    out.append(("sonia:legal", "https://www.bankofengland.co.uk/legal"))
    out.append(("sonia:dbterms", "https://www.bankofengland.co.uk/statistics/details/further-details-about-sonia-data"))
    # use case F: FRED release calendars (release DATES only) to measure how old
    # the latest monthly value gets before the next release; full-history spans
    for rid in ("10", "50"):  # CPI, Employment Situation
        for y in range(2019, year + 1):
            out.append((f"fredcal:{rid}:{y}", f"https://fred.stlouisfed.org/releases/calendar?rid={rid}&y={y}"))
    for k in ("CPIAUCSL", "CPIAUCNS", "CPILFESL", "UNRATE", "PAYEMS"):
        out.append((f"fredfull:{k}", reg["sources"]["fred"]["url"].format(key=k, **dict(fmt, since="1900-01-01"))))
    # use case E: CFTC legacy futures-only history (page + yearly zips), SNB
    # sight-deposit cube dimensions
    out.append(("cftc:history", "https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm"))
    for y in (f"{year}", "2015", "2014", "2000", "1990"):
        out.append((f"cftczip:{y}", f"https://www.cftc.gov/files/dea/history/deacot{y}.zip"))
    out.append(("snb:dims", "https://data.snb.ch/api/cube/snbgwdchfsgw/dimensions/en"))
    out.append(("snb:snbgwdchfsgw", reg["sources"]["snb"]["url"].format(key="snbgwdchfsgw", **fmt)))
    return out


# Marker strings a body must contain for the right series to be there.
# Only "yes"/"no" is reported, never the surrounding values.
MARKERS = {
    "boj": "STRDCLUCON",
    "rba": "FIRMMCRTD",
    "mas": "sora",
    "census?1": "5800",
    "census?2": "5800",
}

# Catalog/metadata calls: list the distinct values of one JSON field (dataset,
# frequency, indicator or country NAMES) matching a filter. Never data values.
LISTS = {
}
# Census answers are JSON arrays (header row + rows): list "code name" of rows
# whose CTY_NAME matches -- partner codes/names only, never the value column.
CENSUS_ROWS = {
    "census?1": "TOTAL FOR ALL|EUROPEAN UNION|CANADA|MEXICO|^CHINA$|JAPAN|GERMANY|UNITED KINGDOM|KOREA|TAIWAN|VIETNAM|INDIA|SWITZERLAND|SINGAPORE",
    "census?2": "TOTAL FOR ALL|EUROPEAN UNION|CANADA|MEXICO|^CHINA$|JAPAN|GERMANY|UNITED KINGDOM|KOREA|TAIWAN|VIETNAM|INDIA|SWITZERLAND|SINGAPORE",
}


def census_rows(body: bytes, pattern: str) -> str:
    try:
        rows = json.loads(body)
        head = rows[0]
        ci, ni = head.index("CTY_CODE"), head.index("CTY_NAME")
    except (ValueError, IndexError, TypeError, KeyError):
        return "not a Census table"
    hits = [f"{r[ci]} {r[ni]}" for r in rows[1:] if re.search(pattern, str(r[ni]))]
    return f"{len(rows) - 1} rows; " + ", ".join(hits)


def xlsx_contents(body: bytes) -> tuple[list[str], list[str]] | None:
    """(sheet names, text labels) of an .xlsx -- shared strings only, which hold
    titles/headers/row names; numeric cells are never read."""
    import io
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(body))
        book = z.read("xl/workbook.xml").decode("utf-8", "replace")
        ss = (z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
              if "xl/sharedStrings.xml" in z.namelist() else "")
    except (zipfile.BadZipFile, KeyError):
        return None
    sheets = re.findall(r'<sheet [^>]*name="([^"]+)"', book)
    labels = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<si>(.*?)</si>", ss, re.S)]
    return sheets, labels


def xlsx_cells(body: bytes, sheet: str, strings_only: bool = True) -> dict:
    """{(col, row): text} of one sheet's STRING cells; with strings_only=False
    numeric cells map to "#" (presence only -- values are never read)."""
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(body))
    ss = z.read("xl/sharedStrings.xml").decode("utf-8", "replace") if "xl/sharedStrings.xml" in z.namelist() else ""
    strings = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<si>(.*?)</si>", ss, re.S)]
    book = z.read("xl/workbook.xml").decode("utf-8", "replace")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
    target = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
    target.update({k: v for v, k in re.findall(r'Target="([^"]+)"[^>]*Id="([^"]+)"', rels)})
    rid = dict(re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', book))[sheet]
    xml = z.read("xl/" + target[rid].lstrip("/").replace("xl/", "")).decode("utf-8", "replace")
    out = {}
    for attrs, inner in re.findall(r"<c ([^>]*?)(?:/>|>(.*?)</c>)", xml, re.S):
        ref = re.search(r'r="([A-Z]+)(\d+)"', attrs)
        if not ref:
            continue
        key = (ref.group(1), int(ref.group(2)))
        v = re.search(r"<v>(.*?)</v>", inner or "")
        if 't="s"' in attrs and v:
            out[key] = strings[int(v.group(1))]
        elif 't="inlineStr"' in attrs:
            out[key] = re.sub(r"<[^>]+>", "", inner)
        elif v and not strings_only:
            out[key] = "#"
    return out


def xlsx_detail(body: bytes, sheets: list) -> str:
    """Header rows (1-10) with cell refs, the last 6 column-A labels with the
    columns that hold a number in that row (presence only), footnote rows."""
    out = []
    for sh in sheets:
        try:
            cells = xlsx_cells(body, sh, strings_only=False)
        except KeyError:
            out.append(f"{sh}: missing")
            continue
        head = [f"{c}{r}={v}" for (c, r), v in sorted(cells.items(), key=lambda x: (x[0][1], len(x[0][0]), x[0][0]))
                if r <= 10 and v != "#"]
        rows = sorted({r for _, r in cells})
        a = [(r, cells[("A", r)]) for r in rows if ("A", r) in cells and cells[("A", r)] != "#"]
        tail = []
        for r, lab in a[-8:]:
            nums = "".join(c + "," for (c, rr), v in sorted(cells.items(), key=lambda x: (len(x[0][0]), x[0][0])) if rr == r and v == "#")
            tail.append(f"r{r} {lab!r} numbers in [{nums}]")
        out.append(f"{sh}: header: {' ; '.join(head)[:1500]}\n      last A: {' | '.join(tail)[:1500]}")
    return "\n    ".join(out)


def pdf_text(body: bytes) -> str:
    """Crude text of a PDF: inflate each stream, collect (strings) shown with Tj/TJ."""
    import zlib
    parts = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", body, re.S):
        try:
            data = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        for t in re.findall(rb"\[(.*?)\]\s*TJ|\((.*?)\)\s*Tj", data, re.S):
            chunk = t[0] or t[1]
            parts.append(b"".join(re.findall(rb"\(((?:\\.|[^\\)])*)\)", chunk)) if t[0] else chunk)
    return b" ".join(parts).decode("latin-1", "replace")


def xlsx_layout(body: bytes, max_sheets: int = 60) -> str:
    """Per sheet: name, dimension, the title strings, column-A labels and the
    header row -- from STRING cells only (t="s"); numeric cells are skipped."""
    import io
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        return "not a zip"
    ss = z.read("xl/sharedStrings.xml").decode("utf-8", "replace") if "xl/sharedStrings.xml" in z.namelist() else ""
    strings = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<si>(.*?)</si>", ss, re.S)]
    book = z.read("xl/workbook.xml").decode("utf-8", "replace")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
    target = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
    target.update({k: v for v, k in re.findall(r'Target="([^"]+)"[^>]*Id="([^"]+)"', rels)})
    out = []
    for name, rid in re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', book)[:max_sheets]:
        path = "xl/" + target.get(rid, "").lstrip("/").replace("xl/", "")
        try:
            xml = z.read(path).decode("utf-8", "replace")
        except KeyError:
            out.append(f"{name}: (sheet file missing)")
            continue
        dim = (re.search(r'<dimension ref="([^"]+)"', xml) or [None, "?"])[1]
        cells: dict[tuple[str, int], str] = {}
        for ref, idx in re.findall(r'<c r="([A-Z]+\d+)"[^>]*t="s"[^>]*>\s*<v>(\d+)</v>', xml):
            col, row = re.match(r"([A-Z]+)(\d+)", ref).groups()
            cells[(col, int(row))] = strings[int(idx)] if int(idx) < len(strings) else "?"
        rows = sorted({r for _, r in cells})
        colA = [cells[("A", r)] for r in rows if ("A", r) in cells]
        by_row = {r: [v for (c, rr), v in sorted(cells.items(), key=lambda x: (len(x[0][0]), x[0][0])) if rr == r] for r in rows[:15]}
        head = max(by_row.values(), key=len, default=[])
        out.append(f"{name} [{dim}] title: {' | '.join(colA[:3])[:200]}\n      A: {' | '.join(colA[3:40])[:900]}"
                   f"\n      header: {' | '.join(head[:40])[:900]}")
    return "\n    ".join(out)


def release_files(reg: dict, name: str, page: bytes) -> list[tuple[str, str, list[str]]]:
    """For a source with `files` (link patterns on its release page): the
    (file name, absolute url, expected labels) of each current file."""
    src = reg["sources"][name]
    html = page.decode("utf-8", "replace")
    out = []
    for fname, pattern in src["files"].items():
        m = re.search(r'href="([^"]*' + pattern + r')"', html)
        expect = [e for s in reg["series"] if s["source"] == name and s.get("file") == fname
                  for e in s.get("expect", [])]
        url = (m.group(1) if m.group(1).startswith("http") else src["base"] + m.group(1)) if m else ""
        out.append((fname, url, expect))
    return out


# HTML answers where an API was expected: print the page <title> only.
TITLES = {"census?1", "census?2"}


def list_values(body: bytes, field: str, pattern: str) -> str:
    try:
        doc = json.loads(body)
    except ValueError:
        return "not json"
    seen: dict[str, None] = {}

    def walk(o: object) -> None:
        if isinstance(o, dict):
            v = o.get(field)
            if isinstance(v, str) and (not pattern or re.search(pattern, v)):
                seen[v] = None
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(doc)
    return ", ".join(seen)[:1500] or "none"


# Layout ("shape") of a response, for writing parsers and fixtures: every
# digit is replaced by 9, so no value can be read back. Labels, codes and
# header names stay visible. Enabled for the step-1 sources.
SHAPE = {"cftc", "snb:snbgwdchfsgw", "corra:csv", "corra:json", "sonia:csv"}
# Latest and earliest observation DATES only (first CSV column), to measure
# publication lag and history depth. Dates are not values.
DATE_SPAN = {"fredfull:CPIAUCSL", "fredfull:CPIAUCNS", "fredfull:CPILFESL", "fredfull:UNRATE",
             "fredfull:PAYEMS"}


# CFTC legacy COT rows are "name",YYMMDD,YYYY-MM-DD,code,... (deafut.txt has
# no header; the yearly zips' annual.txt has one). Only names, codes, the
# column count and DATES are reported -- positions are never read.
COT_NAMES = r"SWISS FRANC|JAPANESE YEN|EURO FX|AUSTRALIAN DOLLAR|BRITISH POUND|CANADIAN DOLLAR|POUND STERLING"


def cot_rows(text: str) -> str:
    import csv
    import io
    rows = list(csv.reader(io.StringIO(text)))
    out: list[str] = []
    header = rows[0] if rows and not rows[0][1].strip().isdigit() else None
    if header:
        out.append(f"header ({len(header)} cols): " + " | ".join(h.strip() for h in header[:12]) + " | ...")
        out.append("  cols 13-20: " + " | ".join(h.strip() for h in header[12:20]))
        rows = rows[1:]
    spans: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        if len(r) > 3 and re.search(COT_NAMES, r[0].upper()):
            spans.setdefault((r[0].strip(), r[3].strip()), []).append(r[2].strip())
    out.append(f"{len(rows)} rows, {len(rows[0]) if rows else 0} cols")
    for (name, code), ds in sorted(spans.items(), key=lambda x: x[0][1]):
        out.append(f"  {code} {name}: {len(ds)} rows, {min(ds)}..{max(ds)}")
    return "\n    ".join(out)


def cot_zip(body: bytes) -> str:
    import io
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        return "not a zip"
    names = [f"{i.filename} ({i.file_size:,} B)" for i in z.infolist()]
    first = z.infolist()[0]
    text = z.read(first).decode("latin-1")
    sample = [mask(l)[:160] for l in text.splitlines() if "092741" in l][:1]
    return ("members: " + ", ".join(names) + "\n    first line: " + mask(text.splitlines()[0])[:300]
            + "\n    CHF line: " + (sample[0] if sample else "none") + "\n    " + cot_rows(text))


def snb_dims(body: bytes) -> str:
    """Every id/name pair in the cube's dimension tree (metadata, no values)."""
    try:
        doc = json.loads(body)
    except ValueError:
        return "not json"
    out: list[str] = []

    def walk(o: object, depth: int) -> None:
        if isinstance(o, dict):
            label = " ".join(str(o[k]) for k in ("id", "name") if k in o and isinstance(o[k], str))
            if label:
                out.append("  " * depth + mask(label))
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth)

    walk(doc, 0)
    return "\n    ".join(out[:200])


def snb_codes(body: bytes) -> str:
    """Distinct dimension-code combinations with row counts and date spans."""
    lines = body.decode("utf-8-sig", "replace").splitlines()
    head = next((i for i, l in enumerate(lines) if l.startswith('"Date"')), None)
    if head is None:
        return "no Date header"
    cols = [c.strip('"') for c in lines[head].split(";")]
    combos: dict[str, list[str]] = {}
    for l in lines[head + 1:]:
        f = [c.strip('"') for c in l.split(";")]
        if len(f) == len(cols):
            combos.setdefault("/".join(f[1:-1]), []).append(f[0])
    import datetime as dt
    days: dict[str, dict[str, int]] = {}
    for l in lines[head + 1:]:
        f = [c.strip('"') for c in l.split(";")]
        if len(f) == len(cols) and f[-1]:
            wd = dt.date.fromisoformat(f[0]).strftime("%a")
            days.setdefault(f[1], {}).setdefault(wd, 0)
            days[f[1]][wd] += 1
    return (f"header {cols}\n    non-empty by weekday: {days}\n    " + "\n    ".join(
        f"{k}: {len(v)} rows, {min(v)}..{max(v)}" for k, v in sorted(combos.items())))


# Terms pages: sentences mentioning these words (legal text, not data).
TERMS = {"corra:terms": r"data|reproduc|licen|permission|commercial",
         "sonia:terms": r"licen|redistribut|attribut|Open Government|free",
         "sonia:legal": r"licen|Open Government|reproduc|database|statistic",
         "sonia:dbterms": r"licen|redistribut|attribut|Open Government|free"}


def sentences(body: bytes, pattern: str, limit: int = 14) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body.decode("utf-8", "replace"), flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", re.sub(r"\s+", " ", text))
    hits = [x.strip() for x in re.split(r"(?<=[.;:])\s+", text) if re.search(pattern, x, re.I) and 30 < len(x) < 600]
    return "\n    ".join(dict.fromkeys(hits[:limit])) or "no matching sentences"


CENSUS_PARTNERS = (r"TOTAL FOR ALL|EUROPEAN UNION|EURO AREA|CANADA|MEXICO|^CHINA$|JAPAN|UNITED KINGDOM|"
                   r"KOREA|TAIWAN|SWITZERLAND|SINGAPORE|AUSTRALIA|GERMANY|INDIA|VIETNAM")


EA_NOW = {"AUSTRIA", "BELGIUM", "BULGARIA", "CROATIA", "CYPRUS", "ESTONIA", "FINLAND", "FRANCE", "GERMANY",
          "GREECE", "IRELAND", "ITALY", "LATVIA", "LITHUANIA", "LUXEMBOURG", "MALTA", "NETHERLANDS",
          "PORTUGAL", "SLOVAKIA", "SLOVENIA", "SPAIN"}


def ea_check(body: bytes) -> str:
    """Per month: does EURO AREA equal the sum of today's members, or of the
    members at the time (Croatia from 2023-01, Bulgaria from 2026-01)? Booleans only."""
    rows = json.loads(body)
    head = rows[0]
    ci, ni, vi, ti = head.index("CTY_CODE"), head.index("CTY_NAME"), head.index("ALL_VAL_MO"), head.index("time")
    by: dict[str, dict[str, float]] = {}
    ea_code = next((r[ci] for r in rows[1:] if r[ni] == "EURO AREA"), None)
    for r in rows[1:]:
        if r[ni] in EA_NOW or r[ci] == ea_code:
            by.setdefault(r[ti], {})[r[ni] if r[ci] != ea_code else "EA"] = float(r[vi] or 0)
    found = sorted({n for v in by.values() for n in v} - {"EA"})
    out = [f"EA code {ea_code}; members found {len(found)}/21: missing {sorted(EA_NOW - set(found))}"]
    close = lambda a, b: abs(a - b) <= max(2.0, abs(b) * 1e-6)
    for t in sorted(by):
        v = by[t]
        if "EA" not in v:
            continue
        today = sum(x for n, x in v.items() if n != "EA")
        at_time = today - (0 if t >= "2023-01" else v.get("CROATIA", 0)) - (0 if t >= "2026-01" else v.get("BULGARIA", 0))
        out.append(f"{t}: today's {close(v['EA'], today)}, at-the-time {close(v['EA'], at_time)}")
    return "; ".join(out)


def ita_params(body: bytes, pattern: str) -> str:
    try:
        vals = json.loads(body)["BEAAPI"]["Results"]["ParamValue"]
    except (ValueError, KeyError, TypeError):
        return f"unexpected: {mask(body[:200].decode('utf-8', 'replace'))}"
    hits = [f"{v.get('Key')}: {v.get('Desc')}" for v in vals
            if not pattern or re.search(pattern, str(v.get("Key")))]
    return f"{len(vals)} values; " + "; ".join(hits)[:6000]


def ita_rows(body: bytes) -> str:
    try:
        res = json.loads(body)["BEAAPI"]["Results"]
        data = res.get("Data", []) if isinstance(res, dict) else []
    except (ValueError, KeyError, TypeError):
        return f"unexpected: {mask(body[:300].decode('utf-8', 'replace'))}"
    if not data:
        return f"no data: {mask(json.dumps(res)[:400])}"
    areas = sorted({d.get("AreaOrCountry") for d in data})
    periods = sorted({d.get("TimePeriod") for d in data})
    return (f"{len(data)} rows, areas {areas}, periods {periods[0]}..{periods[-1]}, "
            f"fields {sorted(data[0])}")


def csv_dates(body: bytes) -> str:
    """First column date span of a CSV with any date format (SONIA IADB: 02 Jan 1997)."""
    rows = [l.split(",")[0].strip().strip('"') for l in body.decode("utf-8-sig", "replace").splitlines()[1:] if l.strip()]
    return f"{len(rows)} rows, first {rows[0]}, last {rows[-1]}" if rows else "no rows"


def release_ages(dates: list) -> str:
    """For a monthly release: the latest value covers the month before the
    release month (obs_date = its 1st). Age just before the next release =
    next release date - that 1st. Months whose value came late (e.g. a
    shutdown) show up as outliers and are listed."""
    from datetime import date as d_
    ds = sorted(set(dates))
    ages = []
    for a, b in zip(ds, ds[1:]):
        m = a.month - 1 or 12
        y = a.year if a.month > 1 else a.year - 1
        ages.append(((b - d_(y, m, 1)).days, a, b))
    ages.sort(reverse=True)
    top = ", ".join(f"{n}d ({a}->{b})" for n, a, b in ages[:8])
    normal = sorted(n for n, _, _ in ages)
    return (f"{len(ds)} release dates {ds[0]}..{ds[-1]}; max ages: {top}; "
            f"median {normal[len(normal) // 2]}d, 95th pct {normal[int(len(normal) * 0.95)]}d")


def date_span(body: bytes) -> str:
    rows = [line.split(",")[0].strip() for line in body.decode("utf-8-sig", "replace").splitlines()[1:]
            if line.strip()]
    return f"{len(rows)} rows, first {rows[0]}, last {rows[-1]}" if rows else "no rows"

def mask(s: str) -> str:
    """Numbers and dates -> 9s; digits inside codes (DGS10, 1TGT) are kept."""
    return re.sub(r"(?<![A-Za-z_\d])[-+]?\d[\d.,:/-]*(?![A-Za-z_\d])",
                  lambda m: re.sub(r"\d", "9", m.group()), s)


def shape(body: bytes) -> str:
    text = body.decode("utf-8-sig", "replace")
    if text.lstrip()[:1] in ("{", "["):
        try:
            doc = json.loads(text)
        except ValueError:
            return "json (unparseable)"
        out: list[str] = []

        def walk(o: object, path: str) -> None:
            if len(out) > 60:
                return
            if isinstance(o, dict):
                for k, v in o.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(o, list):
                out.append(f"{path}: list[{len(o)}]")
                if o:
                    walk(o[0], f"{path}[0]")
            elif isinstance(o, str):
                out.append(f"{path}: str {mask(o)[:60]!r}")
            else:
                out.append(f"{path}: {type(o).__name__}")

        walk(doc, "$")
        return "\n    ".join(out)
    lines = text.splitlines()
    head = lines[:14]
    tail = lines[-2:] if len(lines) > 16 else []
    shown = [mask(line)[:220] for line in head] + (["..."] + [mask(line)[:220] for line in tail] if tail else [])
    return f"{len(lines)} lines:\n    " + "\n    ".join(shown)


def sniff(body: bytes) -> str:
    """Shape of the body only (csv/json/html/xls/...), never its values."""
    head = body[:512].lstrip().lower()
    if not head:
        return "empty"
    if head.startswith((b"<!doctype html", b"<html")) or b"<html" in head:
        return "html"
    if head[:1] in (b"{", b"["):
        return "json"
    if head.startswith(b"\xd0\xcf\x11\xe0") or head.startswith(b"pk"):
        return "xls/zip"
    if b"," in head or b";" in head or b"\t" in head:
        return "delimited"
    return "other"


def auth(src: dict) -> tuple[dict[str, str], dict[str, str]] | None:
    """(headers, query params) carrying a source's API key from its secret's env var.

    A source names `auth_header` (key sent as a header) or `auth_param` (key
    sent as a query parameter). None means the needed secret is not set. The
    key only ever goes into the request -- never printed or logged (the probe
    prints labels, not URLs).
    """
    secret = src.get("secret")
    if not secret:
        return {}, {}
    key = os.environ.get(secret, "")
    if not key:
        return None
    if "auth_param" in src:
        return {}, {src["auth_param"]: key}
    return {src["auth_header"]: key}, {}


def probe(url: str, marker: str = "", headers: dict[str, str] | None = None,
          params: dict[str, str] | None = None) -> tuple[tuple[str, ...], bytes]:
    """(table cells, body). The body is only inspected, never printed."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            status, ctype = str(r.status), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        status, ctype, body = str(e.code), e.headers.get("Content-Type", ""), b""
    except Exception as e:  # timeout, DNS, TLS, reset
        status, ctype, body = f"ERR {type(e).__name__}", "", b""
    found = ("yes" if marker.lower().encode() in body.lower() else "no") if marker else ""
    return (status, f"{len(body):,}", ctype.split(";")[0], sniff(body),
            f"{marker} {found}".strip(), f"{time.monotonic() - t0:.1f}s"), body


def marker_for(label: str) -> str:
    if label.startswith("bea:"):
        return "TimePeriod"  # at least one data row
    return MARKERS.get(label, "")


def main() -> int:
    reg = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    lines = ["| source | status | bytes | type | body | marker | time |",
             "|---|---|---|---|---|---|---|"]
    details: list[str] = []
    only = [p for p in os.environ.get("PROBE_ONLY", "").split(",") if p]
    release_dates: dict[str, list] = {}
    for label, url in probe_urls(reg):
        if only and not any(label.startswith(p) for p in only):
            continue
        if not url:
            lines.append(f"| {label} | TBD (no url yet) | | | | | |")
            continue
        src = reg["sources"].get(label.split("?")[0].split(":")[0].replace("fredfull", "fred")
                                 .replace("cftczip", "cftc").replace("fredcal", "fred"), {})
        if label.startswith(("corra", "sonia")):
            src = {}
        if label.startswith("census:") and not label.startswith(("census:partners", "census:span", "census:multi", "census:eacheck")):
            src = {}
        if label.startswith("beahist"):
            src = {}
        if label.startswith("beameta"):
            src = reg["sources"]["bea"]
        if label.startswith("census:"):
            src = reg["sources"]["census"]
        creds = auth(src)
        if creds is None:
            lines.append(f"| {label} | needs secret {src['secret']} (not set; not requested) | | | | | |")
            continue
        cells, body = probe(url, marker_for(label), *creds)
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(label, *cells))
        if label in SHAPE and body:
            details.append(f"- {label} shape: {shape(body)}")
        if label in DATE_SPAN and body:
            details.append(f"- {label} dates: {date_span(body)}")
        if label in TITLES and cells[3] == "html":
            m = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S)
            details.append(f"- {label} page title: {m.group(1).decode('utf-8', 'replace').strip()[:120] if m else 'none'}")
        if "files" in src and label in reg["sources"]:
            for fname, furl, expect in release_files(reg, label, body):
                flabel = f"{label}:{fname}"
                if not furl:
                    lines.append(f"| {flabel} | link not found on release page | | | | | |")
                    continue
                time.sleep(1)
                fcells, fbody = probe(furl)
                lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(flabel, *fcells))
                got = xlsx_contents(fbody)
                if got is None:
                    details.append(f"- {flabel}: not a readable xlsx")
                    continue
                sheets, labels = got
                text = "\n".join(labels)
                missing = [e for e in expect if e not in text]
                details.append(f"- {flabel} detail (strings; numbers as presence only):\n    "
                               + xlsx_detail(fbody, ["Table 1", "Table 2", "Table 3", "Table 7"]))
                details.append(f"- {flabel}: {furl.rsplit('/', 1)[-1]}, {len(sheets)} sheets, "
                               f"expected labels {len(expect) - len(missing)}/{len(expect)}"
                               + (f", MISSING: {missing}" if missing else ""))
        if label in CENSUS_ROWS and cells[3] == "json":
            details.append(f"- {label}: {census_rows(body, CENSUS_ROWS[label])}")
        if label.startswith("fredcal:") and body:
            sys.path.insert(0, str(REGISTRY.parents[1]))
            from almanac.sources import cal_fred
            rid = label.split(":")[1]
            try:
                release_dates.setdefault(rid, []).extend(cal_fred.parse(body, rid))
            except Exception as e:  # layout change: say so, keep probing
                details.append(f"- {label}: {type(e).__name__}")
        if label == "beameta:areas" and body:
            details.append(f"- ITA areas (Key: Desc): {ita_params(body, '')}")
        if label == "beameta:indicators" and body:
            details.append(f"- ITA indicators (goods/services/balance): {ita_params(body, r'Gds|Serv|Bal|Goods|Services')}")
        if label == "beameta:multi" and body:
            details.append(f"- ITA multi-area call: {ita_rows(body)}")
        if label == "census:partners" and body:
            details.append(f"- census partners: {census_rows(body, CENSUS_PARTNERS)}")
        if label.startswith("beahist:") and body:
            pub = re.search(rb'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})', body)
            if not pub:
                pub = re.search(rb"((?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, 20\d\d)", body)
            details.append(f"- {label}: published {pub.group(1).decode() if pub else '?'}")
            m = re.search(rb'href="([^"]*trad\d{4}[^"]*geo-time-series[^"]*\.xlsx)"', body)
            if not m:
                details.append(f"- {label}: geo workbook link not found")
            else:
                url = m.group(1).decode()
                url = url if url.startswith("http") else "https://www.bea.gov" + url
                time.sleep(1)
                _, wb = probe(url)
                try:
                    cells = xlsx_cells(wb, "Table 1")
                    per = [v for (c, r), v in sorted(cells.items(), key=lambda x: x[0][1]) if c == "A" and re.fullmatch(r"\d{4} [1-4]", v)]
                    details.append(f"- {label}: {url.rsplit('/', 1)[-1]} latest quarter {per[-1] if per else 'none'}")
                except Exception as e:
                    details.append(f"- {label}: {type(e).__name__}")
        if label == "census:eacheck" and body:
            try:
                details.append(f"- census euro-area composition: {ea_check(body)}")
            except (ValueError, IndexError, KeyError) as e:
                details.append(f"- census eacheck: {type(e).__name__}")
        if label == "census:multi" and body:
            try:
                t = json.loads(body)
                details.append(f"- census multi-code: header {t[0]}, {len(t) - 1} rows, "
                               f"codes {sorted({r[0] for r in t[1:]})}, months {sorted({r[-1] for r in t[1:]})}")
            except (ValueError, IndexError, TypeError):
                details.append(f"- census multi-code: not a table ({mask(body[:120].decode('utf-8', 'replace'))})")
        if label == "census:ea-notice" and body:
            details.append(f"- census EA notice text: {pdf_text(body)[:1500]}")
        if label in ("census:groupings", "census:statsinfo") and body:
            details.append(f"- {label} excerpts:\n    {sentences(body, r'Euro Area|euro area|European Union|historical|revis', 20)}")
        if label == "beameta:error" and body:
            def paths(o, p="$"):
                if isinstance(o, dict):
                    return [x for k, v in o.items() for x in paths(v, f"{p}.{k}")]
                if isinstance(o, list):
                    return [x for v in o[:2] for x in paths(v, p + "[]")]
                return [p]
            try:
                doc = json.loads(body)
                details.append(f"- ITA error JSON key paths (no values): {paths(doc)}")
                err = json.dumps(doc)
                details.append(f"- ITA error echoes the key: {os.environ.get('BEA_API_KEY', '#') in err}")
            except ValueError:
                details.append("- ITA error: not json")
        if label == "beameta:years" and body:
            details.append(f"- ITA year list + EU: {ita_rows(body)}")
        if label == "census:span" and body:
            try:
                t = [r[-1] for r in json.loads(body)[1:]]
                details.append(f"- census KR exports months: {len(t)}, {min(t)}..{max(t)}")
            except (ValueError, IndexError, TypeError):
                details.append(f"- census span: not a table ({body[:80]!r})")
        if label in TERMS and body:
            details.append(f"- {label} terms excerpts:\n    {sentences(body, TERMS[label])}")
        if label == "corra:full" and body:
            details.append(f"- corra:full dates: {csv_dates(body[body.find(b'date,'):] if b'date,' in body else body)}")
        if label == "sonia:full" and body:
            details.append(f"- sonia:full dates: {csv_dates(body)}")
        if label == "cftc" and body:
            details.append(f"- cftc (deafut.txt) currency rows: {cot_rows(body.decode('latin-1'))}")
        if label.startswith("cftczip:") and body:
            details.append(f"- {label}: {cot_zip(body)}")
        if label == "cftc:history" and body:
            links = sorted(set(re.findall(rb'href="([^"]*deacot[^"]*)"', body)))
            details.append(f"- cftc:history legacy futures-only links: {len(links)}: "
                           + ", ".join(l.decode()[-40:] for l in links))
        if label == "snb:dims" and body:
            details.append(f"- snb:dims:\n    {snb_dims(body)}")
        if label == "snb:snbgwdchfsgw" and body:
            details.append(f"- snb:snbgwdchfsgw codes: {snb_codes(body)}")
        if label in LISTS:
            details.append(f"- {label}: {list_values(body, *LISTS[label])}")
        time.sleep(1)  # one request at a time, politely
    for rid, ds in sorted(release_dates.items()):
        details.append(f"- release {rid} ages: {release_ages(ds)}")
    table = "\n".join(lines)
    if details:
        table += "\n\nMetadata (names only):\n" + "\n".join(details)
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Almanac source probe\n\n" + table + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
