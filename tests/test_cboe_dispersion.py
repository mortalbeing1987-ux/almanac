"""CBOE_INDEX: SKEW, COR1M, COR3M, DSPX through the existing CBOE parser (hand-made fixtures)."""

from datetime import date

from almanac import deliver
from almanac.registry import keys, load
from almanac.run import Ctx
from almanac.sources import cboe
from almanac.sources.cboe import parse

REG = load()
TODAY = date(2026, 9, 30)


def series():
    return next(x for x in REG.series if x["id"] == "CBOE_INDEX")


OHLC = b"DATE,OPEN,HIGH,LOW,CLOSE\r\n01/03/2006,23.5,23.5,23.5,23.6\r\n01/04/2006,24.3,24.9,24.0,24.5\r\n"
SINGLE = b"DATE,SKEW\r\n01/02/1990,126.09\r\n01/03/1990,123.34\r\n"


class Http:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append(url)
        name = url.rsplit("/", 1)[1].split("_")[0]
        return OHLC if name.startswith("COR") else SINGLE.replace(b"SKEW", name.encode())


def test_registry_entry():
    s = series()
    assert keys(s) == ["SKEW", "COR1M", "COR3M", "DSPX"]
    assert s["source"] == "cboe" and s["backfill"] == "full" and s["redistribution"] == "restricted"
    assert deliver.backfill_start(s, date(2026, 9, 30)) == deliver.FULL_HISTORY


def test_ohlc_files_deliver_the_close_and_single_column_files_their_own_column():
    assert parse(OHLC, "COR1M") == [("2006-01-03", 23.6), ("2006-01-04", 24.5)]
    assert parse(SINGLE, "SKEW") == [("1990-01-02", 126.09), ("1990-01-03", 123.34)]
    assert parse(SINGLE.replace(b"SKEW", b"DSPX"), "DSPX")[0] == ("1990-01-02", 126.09)


def test_fetch_delivers_one_series_id_per_key():
    s = series()
    http = Http()
    obs = cboe.fetch(Ctx(http, "cboe", REG.sources["cboe"], [s], date(1900, 1, 1), TODAY))
    assert {o.series_id for o in obs} == {"CBOE_INDEX_SKEW", "CBOE_INDEX_COR1M", "CBOE_INDEX_COR3M", "CBOE_INDEX_DSPX"}
    assert [u.rsplit("/", 1)[1] for u in http.calls] == [f"{k}_History.csv" for k in ("SKEW", "COR1M", "COR3M", "DSPX")]
