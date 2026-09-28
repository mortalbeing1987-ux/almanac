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
    for s in reg.get("series", []):
        for c in s.get("candidates", []):
            url = reg["sources"][s["source"]]["url"]
            out.append((f"{s['id']}?{c}", url.format(key=c, year=year)))
    # a few extra FRED probes so a partial block is visible
    for k in ("BAMLH0A0HYM2", "BAMLC0A4CBBB", "DFII10", "T10Y3M", "DTWEXBGS", "ICSA", "NFCI", "CPIAUCSL"):
        out.append((f"fred:{k}", reg["sources"]["fred"]["url"].format(key=k)))
    return out


# Marker strings a candidate's body must contain for the right series to be
# there. Only "yes"/"no" is reported, never the surrounding values.
MARKERS = {
    "boj": "STRDCLUCON",
    "rba": "FIRMMCRTD",
    "mas?1": "sora",
    "mas?2": "sora",
    "mas?4": "sora",
    "mas?5": "sora",
    "mas?6": "sora",
    "cape?1": "ie_data",
}

# Catalog searches: print (id | title) of entries whose title mentions the
# term, so a dataset id can be picked. Catalog metadata only, never values.
DISCOVER = {"mas?5": "sora", "mas?6": "sora|overnight|interest rate"}
# Catalog listings that are paged (data.gov.sg v2 ignores ?query=): walk
# every page, politely, and search the titles.
PAGED = {"mas?6": 300}


# HTML pages whose layout (form controls, download links, table headers) is
# reported so a fetcher can be designed. Layout only, never cell values.
STRUCTURE = {"mas?2"}


def structure(body: bytes) -> str:
    html = body.decode("utf-8", "replace")
    controls = sorted(set(re.findall(r'<(?:select|input|button)[^>]*\bname="([^"]+)"', html, re.I)))
    links = sorted(set(h for h in re.findall(r'href="([^"]+)"', html, re.I)
                       if re.search(r"download|csv|xls|export", h, re.I)))
    headers = [re.sub(r"<[^>]+>|\s+", " ", h).strip()
               for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.I | re.S)]
    return "; ".join([
        f"tables={len(re.findall(r'<table', html, re.I))}",
        f"forms={len(re.findall(r'<form', html, re.I))}",
        f"viewstate={'yes' if '__VIEWSTATE' in html else 'no'}",
        "controls=" + ", ".join(c for c in controls if not c.startswith("__"))[:600],
        "links=" + ", ".join(links)[:400],
        "headers=" + " | ".join(dict.fromkeys(h for h in headers if h))[:600],
    ])


def discover(body: bytes, term: str) -> list[str]:
    try:
        doc = json.loads(body)
    except ValueError:
        return []
    found: set[str] = set()

    def walk(o: object) -> None:
        if isinstance(o, dict):
            title = str(o.get("title") or o.get("name") or "")
            ident = o.get("datasetId") or o.get("id")
            if ident and any(t in title.lower() for t in term.split("|")):
                found.add(f"{ident} | {title[:80]}")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    return sorted(found)


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


def probe(url: str, marker: str = "") -> tuple[tuple[str, ...], bytes]:
    """(table cells, body). The body is only inspected, never printed."""
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
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


def main() -> int:
    reg = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    lines = ["| source | status | bytes | type | body | marker | time |",
             "|---|---|---|---|---|---|---|"]
    catalog: list[str] = []
    for label, url in probe_urls(reg):
        if not url:
            lines.append(f"| {label} | TBD (no url yet) | | | | | |")
            continue
        cells, body = probe(url, MARKERS.get(label, ""))
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(label, *cells))
        if label in STRUCTURE:
            catalog.append(f"- {label} layout: {structure(body)}")
        if label in DISCOVER:
            hits = set(discover(body, DISCOVER[label]))
            note = ""
            for page in range(2, PAGED.get(label, 0) + 1):
                time.sleep(1)
                cells, body = probe(f"{url}&page={page}")
                if cells[0] != "200" or b'"datasetId"' not in body:
                    note = f" (listing ended at page {page}: {cells[0]})"
                    break
                hits.update(discover(body, DISCOVER[label]))
            shown = sorted(hits)[:60]
            catalog.append(f"- {label}{note}: "
                           + ("; ".join(shown) if shown else "no catalog matches"))
        time.sleep(1)  # one request at a time, politely
    table = "\n".join(lines)
    if catalog:
        table += "\n\nDetails (catalog matches, page layouts):\n" + "\n".join(catalog)
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Almanac source probe\n\n" + table + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
