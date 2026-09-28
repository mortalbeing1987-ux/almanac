"""Minimal .xlsx reader (standard library only): one sheet as {(row, col): value}.

Strings come from the shared-string table (or inline strings); numbers are
floats. Formulas' cached values are read like any other value. Only what the
BEA release workbooks use is supported -- anything else is a layout error.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

from ._util import require

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

Cells = dict[tuple[int, int], "str | float"]


def col_number(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(f"{{{NS['m']}}}t"))


class Workbook:
    def __init__(self, body: bytes):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(body))
            book = ET.fromstring(self.zip.read("xl/workbook.xml"))
            rels = ET.fromstring(self.zip.read("xl/_rels/workbook.xml.rels"))
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            require(False, "readable .xlsx workbook")
        target = {r.get("Id"): r.get("Target") for r in rels}
        self.sheets = {}
        for sh in book.iter(f"{{{NS['m']}}}sheet"):
            t = target.get(sh.get(REL), "")
            self.sheets[sh.get("name")] = "xl/" + t.lstrip("/").removeprefix("xl/")
        self.strings: list[str] = []
        if "xl/sharedStrings.xml" in self.zip.namelist():
            sst = ET.fromstring(self.zip.read("xl/sharedStrings.xml"))
            self.strings = [_text(si) for si in sst.findall("m:si", NS)]

    def cells(self, sheet: str) -> Cells:
        require(sheet in self.sheets, f"workbook sheet {sheet!r}")
        root = ET.fromstring(self.zip.read(self.sheets[sheet]))
        out: Cells = {}
        for c in root.iter(f"{{{NS['m']}}}c"):
            m = re.fullmatch(r"([A-Z]+)(\d+)", c.get("r", ""))
            if not m:
                continue
            key = (int(m.group(2)), col_number(m.group(1)))
            t, v = c.get("t"), c.find("m:v", NS)
            if t == "s" and v is not None:
                out[key] = self.strings[int(v.text)].strip()
            elif t == "inlineStr":
                out[key] = _text(c).strip()
            elif t == "str" and v is not None:
                out[key] = (v.text or "").strip()
            elif v is not None and v.text not in (None, ""):
                try:
                    out[key] = float(v.text)
                except ValueError:
                    require(False, f"{sheet} cell {c.get('r')} value")
        return out
