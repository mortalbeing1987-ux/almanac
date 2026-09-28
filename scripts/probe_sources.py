"""Step 0: probe every source in almanac/series.toml for reachability.

Prints a markdown table (and appends it to $GITHUB_STEP_SUMMARY when run in
Actions). Records only status, size, content type and timing -- never the
data itself, so the public workflow log carries no restricted values.

    python scripts/probe_sources.py
"""

from __future__ import annotations

import os
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
    for k in ("BAMLH0A0HYM2", "BAMLC0A4CBBB", "DFII10", "T10Y3M", "DTWEXBGS", "DTWEXAFEGS", "DTWEXEMEGS", "ICSA", "NFCI", "CPIAUCSL"):
        out.append((f"fred:{k}", reg["sources"]["fred"]["url"].format(key=k)))
    return out


# Marker strings a body must contain for the right series to be there.
# Only "yes"/"no" is reported, never the surrounding values.
MARKERS = {
    "boj": "STRDCLUCON",
    "rba": "FIRMMCRTD",
    "mas": "sora",
}


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


def auth_headers(src: dict) -> dict[str, str] | None:
    """Header carrying a source's API key, read from the Actions secret's env var.

    None means the source needs a secret that is not set. The key is only ever
    placed in the request header -- never printed, logged or returned elsewhere.
    """
    secret = src.get("secret")
    if not secret:
        return {}
    key = os.environ.get(secret, "")
    return {src["auth_header"]: key} if key else None


def probe(url: str, marker: str = "", extra: dict[str, str] | None = None) -> tuple[str, ...]:
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(extra or {})})
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
            f"{marker} {found}".strip(), f"{time.monotonic() - t0:.1f}s")


def main() -> int:
    reg = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    lines = ["| source | status | bytes | type | body | marker | time |",
             "|---|---|---|---|---|---|---|"]
    for label, url in probe_urls(reg):
        if not url:
            lines.append(f"| {label} | TBD (no url yet) | | | | | |")
            continue
        src = reg["sources"].get(label.split("?")[0].split(":")[0], {})
        extra = auth_headers(src)
        if extra is None:
            lines.append(f"| {label} | needs secret {src['secret']} (not set; not requested) | | | | | |")
            continue
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            label, *probe(url, MARKERS.get(label, ""), extra)))
        time.sleep(1)  # one request at a time, politely
    table = "\n".join(lines)
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Almanac source probe\n\n" + table + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
