"""Synthetic BEA trade workbooks in the real layout (probe 2026-09-28), with
invented values. Built in memory as minimal .xlsx files: shared strings for
text, plain <v> numbers."""

from __future__ import annotations

import io
import zipfile
import zlib
from xml.sax.saxutils import escape

CATEGORIES = ["Maintenance and Repair Services n.i.e.", "Transport", "Travel 1", "Construction",
              "Insurance Services", "Financial Services",
              "Charges for the Use of Intellectual Property n.i.e.",
              "Telecom-munications, Computer, and Information Services", "Other Business Services",
              "Personal, Cultural, and Recreational Services", "Government Goods and Services n.i.e."]
PARTNERS = ["Australia", "Belgium", "Brazil", "Canada", "China", "France", "Germany", "Hong Kong", "India",
            "Ireland", "Israel", "Italy", "Japan", "Korea, South", "Malaysia", "Mexico", "Netherlands",
            "Saudi Arabia", "Singapore", "Switzerland", "Taiwan", "United Kingdom", "Vietnam",
            "All other countries", "CAFTA-DR", "European Union", "South/\nCentral America"]
GEO_TABLES = ["Exports of Goods and Services", "Imports of Goods and Services", "Balance on Goods and Services",
              "Exports of Goods", "Imports of Goods", "Balance on Goods", "Exports of Services",
              "Imports of Services", "Balance on Services"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def xlsx(sheets: dict[str, dict[tuple[int, int], object]]) -> bytes:
    strings: list[str] = []
    index: dict[str, int] = {}
    buf = io.BytesIO()
    z = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    for i, (name, cells) in enumerate(sheets.items(), 1):
        rows: dict[int, list[str]] = {}
        for (r, c), v in sorted(cells.items()):
            ref = f"{col(c)}{r}"
            if isinstance(v, str):
                if v not in index:
                    index[v] = len(strings)
                    strings.append(v)
                rows.setdefault(r, []).append(f'<c r="{ref}" t="s"><v>{index[v]}</v></c>')
            else:
                rows.setdefault(r, []).append(f'<c r="{ref}"><v>{v}</v></c>')
        data = "".join(f'<row r="{r}">{"".join(cs)}</row>' for r, cs in sorted(rows.items()))
        z.writestr(f"xl/worksheets/sheet{i}.xml", f"<worksheet {ns}><sheetData>{data}</sheetData></worksheet>")
    book = "".join(f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(sheets, 1))
    z.writestr("xl/workbook.xml", f"<workbook {ns} {rns}><sheets>{book}</sheets></workbook>")
    rels = "".join(f'<Relationship Id="rId{i}" Type="worksheet" Target="worksheets/sheet{i}.xml"/>'
                   for i in range(1, len(sheets) + 1))
    z.writestr("xl/_rels/workbook.xml.rels",
               f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
    sst = "".join(f"<si><t>{escape(t)}</t></si>" for t in strings)
    z.writestr("xl/sharedStrings.xml", f"<sst {ns}>{sst}</sst>")
    z.close()
    return buf.getvalue()


def _head(title: str, subtitle: str, unit: str) -> dict:
    return {(1, 1): "Last updated September 3, 2026", (2, 1): title, (3, 1): subtitle, (4, 1): unit}


def value(*parts) -> float:
    """Invented, reproducible values from a checksum of the coordinates."""
    return float(1000 + zlib.crc32(repr(parts).encode()) % 90000) / 10


def months(last=(2026, 7)):
    return [(1992, 1), (1992, 2), (last[0], last[1] - 1), last]


def time_series(last=(2026, 7), unit="[Millions of dollars, months seasonally adjusted]",
                drop_category: str | None = None) -> bytes:
    t1 = _head("Table 1. U.S. International Trade in Goods and Services", "Exports, Imports, and Balances", unit)
    t1.update({(5, 1): "Details may not equal totals due to seasonal adjustment and rounding.", (7, 1): "Period",
               (7, 3): "Balance", (7, 6): "Exports", (7, 9): "Imports", (9, 1): "Annual", (10, 1): "1992",
               (11, 1): "Monthly"})
    for c, item in zip(range(2, 11), ["Total", "Goods 1", "Services"] * 3):
        t1[(8, c)] = item
    t1.update({(10, c): 999999.0 for c in range(2, 11)})  # annual row: never delivered
    r = 12
    for y, m in months(last):
        t1[(r, 1)] = f"{y} {MONTHS[m - 1]}" + (" (R)" if (y, m) == (last[0], last[1] - 1) else "")
        for c in range(2, 11):
            t1[(r, c)] = value("t1", y, m, c)
        r += 1
    t1[(r + 1, 1)] = "(R) Revised"
    t1[(r + 2, 1)] = "1 Data are presented on a balance of payments basis."
    sheets = {"Readme": {(1, 1): "Last updated September 3, 2026"}, "Table 1": t1}
    for n, flow in ((2, "Exports"), (3, "Imports")):
        t = _head(f"Table {n}. U.S. International Trade in Services by Major Category", flow, unit)
        t.update({(7, 1): "Period", (7, 2): "Total", (8, 1): "Annual", (9, 1): "1999", (10, 1): "Monthly"})
        cats = [c for c in CATEGORIES if c != drop_category]
        for i, name in enumerate(cats, 3):
            t[(7, i)] = name
        r = 11
        for y, m in [(1999, 1)] + months(last)[2:]:
            t[(r, 1)] = f"{y} {MONTHS[m - 1]}" + (" (R)" if (y, m) == (last[0], last[1] - 1) else "")
            for c in range(2, len(cats) + 3):
                t[(r, c)] = value(f"t{n}", y, m, c)
            r += 1
        t[(r + 1, 1)] = "n.i.e. Not included elsewhere"
        sheets[f"Table {n}"] = t
    return xlsx(sheets)


def geo(last_quarter=(2026, 2), partners=PARTNERS, unit="[Millions of dollars, quarters seasonally adjusted]") -> bytes:
    sheets = {"Readme": {(1, 1): "Last updated September 3, 2026"}}
    quarters = [(1999, 1), (1999, 2), (last_quarter[0], last_quarter[1] - 1), last_quarter]
    for n, sub in enumerate(GEO_TABLES, 1):
        t = _head(f"Table {n}. U.S. International Trade by Selected Countries and Areas", sub, unit)
        t.update({(6, 1): "Period", (7, 1): "Annual", (8, 1): "1999", (9, 1): "Quarterly"})
        for i, p in enumerate(partners, 2):
            t[(6, i)] = p
        r = 10
        for y, q in quarters:
            t[(r, 1)] = f"{y} {q}" + (" (R)" if (y, q) == quarters[-2] else "")
            for i, p in enumerate(partners, 2):
                t[(r, i)] = "n.a." if (p == "Vietnam" and y == 1999) else value("geo", n, y, q, p)
            r += 1
        t[(r + 1, 1)] = "(R) Revised"
        t[(r + 2, 1)] = "n.a. Not available"
        sheets[f"Table {n}"] = t
    return xlsx(sheets)


def release_page(ts=("0626", "0726"), geo_releases=("0426", "0726")) -> bytes:
    links = [f'<a href="/sites/default/files/2026-08/trad{r}-time-series.xlsx">Time series</a>' for r in ts]
    links += [f'<a href="/sites/default/files/2026-09/trad{r}-geo-time-series.xlsx">By country</a>' for r in geo_releases]
    return ("<html><body><h1>U.S. International Trade in Goods and Services</h1>"
            + "".join(links) + "</body></html>").encode()
