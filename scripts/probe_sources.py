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
from datetime import date
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "almanac" / "series.toml"
UA = "almanac-source-probe/0.1 (+https://github.com/mortalbeing1987-ux/almanac)"
TIMEOUT = 45


def probe_urls(reg: dict) -> list[tuple[str, str]]:
    """(label, url) pairs: one per source, plus one per FRED key family."""
    out: list[tuple[str, str]] = []
    year = date.today().year
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
        out.append((name, url.format(key=key, year=year)))
    # candidate keys on sources that already have a url (e.g. SARON on snb)
    # sources whose series have `areas`: one probe per (key, area)
    for s in reg.get("series", []):
        if "areas" in s:
            url = reg["sources"][s["source"]]["url"]
            for k in s["key"]:
                for a in s["areas"]:
                    out.append((f"{s['source']}:{k}:{a}",
                                url.format(key=k, area=a, year=year, prev_year=year - 1)))
    for s in reg.get("series", []):
        for c in s.get("candidates", []):
            url = reg["sources"][s["source"]]["url"]
            out.append((f"{s['id']}?{c}", url.format(key=c, year=year)))
    # a few extra FRED probes so a partial block is visible
    for k in ("BAMLH0A0HYM2", "BAMLC0A4CBBB", "DFII10", "T10Y3M", "DTWEXBGS", "DTWEXAFEGS", "DTWEXEMEGS", "ICSA", "NFCI", "CPIAUCSL"):
        out.append((f"fred:{k}", reg["sources"]["fred"]["url"].format(key=k)))
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


# Research pages: list links matching a pattern (hrefs only).
LINKS = {
    "trade_release?1": r"trad[^\"']*\.(xlsx|pdf)",
}
# XLSX files: report sheet names and the workbook's text labels (shared
# strings = headers/row names), never numeric cells.
XLSX_LABELS = {"trade_release?2", "trade_release?3", "trade_release?4"}


def xlsx_labels(body: bytes) -> str:
    import io
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(body))
        book = z.read("xl/workbook.xml").decode("utf-8", "replace")
        sheets = re.findall(r'<sheet [^>]*name="([^"]+)"', book)
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            ss = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            strings = [re.sub(r"<[^>]+>", "", s).strip()
                       for s in re.findall(r"<si>(.*?)</si>", ss, re.S)]
    except (zipfile.BadZipFile, KeyError) as e:
        return f"unreadable ({type(e).__name__})"
    labels = [s for s in dict.fromkeys(strings) if s and not re.fullmatch(r"[\d.,()\s-]+", s)]
    return (f"{len(sheets)} sheets: " + "; ".join(sheets)[:700]
            + f" || {len(labels)} labels, first: " + " | ".join(labels[:60])[:1800])


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
    for label, url in probe_urls(reg):
        if not url:
            lines.append(f"| {label} | TBD (no url yet) | | | | | |")
            continue
        src = reg["sources"].get(label.split("?")[0].split(":")[0], {})
        creds = auth(src)
        if creds is None:
            lines.append(f"| {label} | needs secret {src['secret']} (not set; not requested) | | | | | |")
            continue
        cells, body = probe(url, marker_for(label), *creds)
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(label, *cells))
        if label in TITLES and cells[3] == "html":
            m = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S)
            details.append(f"- {label} page title: {m.group(1).decode('utf-8', 'replace').strip()[:120] if m else 'none'}")
        if label in LINKS:
            hrefs = dict.fromkeys(re.findall(r'href="([^"]*' + LINKS[label] + r')"', body.decode("utf-8", "replace"), re.I))
            details.append(f"- {label} links: " + (", ".join(h if isinstance(h, str) else h[0] for h in hrefs)[:900] or "none"))
        if label in XLSX_LABELS and body:
            details.append(f"- {label} xlsx: {xlsx_labels(body)}")
        if label in CENSUS_ROWS and cells[3] == "json":
            details.append(f"- {label}: {census_rows(body, CENSUS_ROWS[label])}")
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
