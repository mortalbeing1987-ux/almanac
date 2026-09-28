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
SHAPE = {"cftc", "snb:snbgwdchfsgw"}
# Latest and earliest observation DATES only (first CSV column), to measure
# publication lag and history depth. Dates are not values.
DATE_SPAN: set[str] = set()


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
    for label, url in probe_urls(reg):
        if only and not any(label.startswith(p) for p in only):
            continue
        if not url:
            lines.append(f"| {label} | TBD (no url yet) | | | | | |")
            continue
        src = reg["sources"].get(label.split("?")[0].split(":")[0].replace("fredfull", "fred")
                                 .replace("cftczip", "cftc"), {})
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
                details.append(f"- {flabel}: {furl.rsplit('/', 1)[-1]}, {len(sheets)} sheets, "
                               f"expected labels {len(expect) - len(missing)}/{len(expect)}"
                               + (f", MISSING: {missing}" if missing else ""))
        if label in CENSUS_ROWS and cells[3] == "json":
            details.append(f"- {label}: {census_rows(body, CENSUS_ROWS[label])}")
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
