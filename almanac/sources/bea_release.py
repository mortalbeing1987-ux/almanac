"""BEA's monthly "U.S. International Trade in Goods and Services" workbooks.

The release page links the current files, whose names carry the release
number: trad<MM><YY>-time-series.xlsx and trad<MM><YY>-geo-time-series.xlsx
(MMYY = the newest data month). If the page links more than one release, the
newest release number of each file is used. The time-series workbook's
latest month must be its release month; the geo workbook is only reissued
with January, April, July and October data, so its release must be the one
for the time series' quarter, and its latest quarter the last one completed
by its release month. Anything else is an error, never "up to date".

Sheets: a title block, a unit line ("[Millions of dollars, months|quarters
seasonally adjusted]"), a header row (Table 1 of the time series has group
labels -- Balance, Exports, Imports -- over Total/Goods/Services blocks), then
"Annual" rows (skipped) and "Monthly"/"Quarterly" rows labelled "1992 Jan" or
"1999 1", then footnotes. Columns are found by their labels, never by
position. "n.a." marks a value that does not exist.
"""

from __future__ import annotations

import re
import weakref
from datetime import date

from ..model import Observation
from ..registry import delivered_ids
from ._dates import month_number
from ._util import get, require, text
from ._xlsx import Workbook

MISSING = {"n.a.", "(D)", "(*)", "(X)", "-", ""}
_memo: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _get_once(ctx, url: str) -> bytes:
    """One download per run for the page and each workbook (three series share them)."""
    cache = _memo.setdefault(ctx.http, {})
    if url not in cache:
        cache[url] = get(ctx, url)
    return cache[url]


def release_links(page: str, files: dict[str, str], base: str) -> dict[str, tuple[date, str]]:
    """{file: (release month, absolute url)}: per file, the newest release linked."""
    out: dict[str, tuple[date, str]] = {}
    for name, pattern in files.items():
        found: dict[date, str] = {}
        for href, fname in re.findall(r'href="([^"]*?(' + pattern + r'))"', page):
            m = re.search(r"trad(\d{2})(\d{2})", fname)
            require(m is not None and 1 <= int(m.group(1)) <= 12, f"BEA release number in {fname[:40]!r}")
            found[date(2000 + int(m.group(2)), int(m.group(1)), 1)] = href if href.startswith("http") else base + href
        require(bool(found), f"BEA release page links the {name} workbook")
        newest = max(found)
        out[name] = (newest, found[newest])
    return out


def geo_release_for(month: date) -> date:
    """The geo workbook is reissued with January, April, July and October data
    (probe 2026-09-28: the February and March 2026 pages still link trad0126)."""
    return date(month.year, month.month - (month.month - 1) % 3, 1)


def period_date(label: str) -> str | None:
    """"1992 Jan" -> 1992-01-01; "1999 2" -> 1999-04-01; anything else None.
    Revised periods carry a marker ("2026 Jan (R)", "2026 1 (R)"; probe
    2026-09-28), which is dropped."""
    label = re.sub(r"\s*\((?:R|P)\)$", "", label.strip())
    m = re.fullmatch(r"(\d{4}) ([A-Za-z]{3})", label)
    if m:
        try:
            return date(int(m.group(1)), month_number(m.group(2)), 1).isoformat()
        except KeyError:
            return None
    m = re.fullmatch(r"(\d{4}) ([1-4])", label)
    if m:
        return date(int(m.group(1)), 3 * int(m.group(2)) - 2, 1).isoformat()
    return None


def _label(v) -> str:
    """Header text without footnote markers: "Goods 1" -> "Goods", "Travel 1" -> "Travel"."""
    return re.sub(r"\s+\d+$", "", str(v)).strip() if isinstance(v, str) else ""


class Sheet:
    def __init__(self, cells: dict, name: str, unit: str, frequency: str):
        self.cells, self.name = cells, name
        rows = sorted({r for r, _ in cells})
        texts = [str(cells.get((r, 1), "")) for r in rows[:8]]
        require(any("Millions of dollars" in t and unit in t for t in texts),
                f"{name} unit line 'Millions of dollars, {unit}'")
        start = next((r for r in rows if cells.get((r, 1)) == frequency), None)
        require(start is not None, f"{name} '{frequency}' section")
        self.header = next(r for r in rows if cells.get((r, 1)) == "Period")
        self.periods: list[tuple[int, str]] = []
        for r in rows:
            if r <= start or (r, 1) not in cells:
                continue
            d = period_date(str(cells[(r, 1)]))
            if d is None:
                break  # footnotes follow the last period
            self.periods.append((r, d))
        require(bool(self.periods), f"{name} has periods")
        dates = [d for _, d in self.periods]
        require(dates == sorted(set(dates)), f"{name} periods ascending, no duplicates")

    def column(self, label: str, row: int | None = None) -> int:
        row = row or self.header
        cols = [c for (r, c), v in self.cells.items() if r == row and _label(v) == label]
        require(len(cols) == 1, f"{self.name} column {label!r}")
        return cols[0]

    def grouped_column(self, group: str, item: str) -> int:
        """Table 1: a group label (row above the header) over Total/Goods/Services."""
        items = self.header + 1
        totals = sorted(c for (r, c), v in self.cells.items() if r == items and _label(v) == "Total")
        for t in totals:
            block = range(t, t + 3)
            labels = [_label(self.cells.get((self.header, c), "")) for c in block]
            require([_label(self.cells.get((items, c), "")) for c in block] == ["Total", "Goods", "Services"],
                    f"{self.name} Total/Goods/Services block")
            if group in labels:
                return t + ["Total", "Goods", "Services"].index(item)
        require(False, f"{self.name} group {group!r}")

    def values(self, col: int) -> list[tuple[str, float]]:
        out = []
        for r, d in self.periods:
            v = self.cells.get((r, col))
            if isinstance(v, float):
                out.append((d, v))
            else:
                require(str(v or "").strip() in MISSING, f"{self.name} value at {d}")
        return out


def _quarter_start(d: date) -> str:
    """First day of the last quarter completed by month d."""
    q_end = d.month - d.month % 3 if d.month % 3 else d.month
    y = d.year if q_end else d.year - 1
    q_end = q_end or 12
    return date(y, q_end - 2, 1).isoformat()


def fetch(ctx) -> list[Observation]:
    src = ctx.src
    page = text(_get_once(ctx, src["url"]))
    links = release_links(page, src["files"], src["base"])
    if "time_series" in links and "geo" in links:
        want = geo_release_for(links["time_series"][0])
        require(links["geo"][0] == want, f"geo workbook trad{links['geo'][0]:%m%y} is the one for "
                                         f"release trad{links['time_series'][0]:%m%y} (trad{want:%m%y})")
    obs = []
    for s in ctx.series:
        release, url = links[s["file"]]
        book = Workbook(_get_once(ctx, url))
        monthly = s["freq"] == "monthly"
        unit, section = (("months seasonally adjusted", "Monthly") if monthly
                         else ("quarters seasonally adjusted", "Quarterly"))
        sheets: dict[str, Sheet] = {}
        for key in s["key"]:
            name = s["sheets"][key] if "sheets" in s else s["sheet"]
            if name not in sheets:
                sh = Sheet(book.cells(name), name, unit, section)
                for want in s.get("expect", {}).get(name, []):
                    require(any(want == str(v) for (r, c), v in sh.cells.items() if c == 1 and r < sh.header),
                            f"{name} title {want!r}")
                latest = sh.periods[-1][1]
                expected = release.isoformat() if monthly else _quarter_start(release)
                require(latest == expected, f"{name} latest period {latest} is release trad{release:%m%y}'s {expected}")
                sheets[name] = sh
            sh = sheets[name]
            for m, sid in zip(s["measures"], delivered_ids(s, key)):
                if "groups" in s:
                    col = sh.grouped_column(s["groups"][key], s["items"][m])
                else:
                    col = sh.column(s["columns"][m])
                obs += [Observation(sid, d, v, ctx.source) for d, v in sh.values(col)]
    return obs
