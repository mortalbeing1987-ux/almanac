"""Census detail: world trade by 3-digit NAICS industry and HS 7108 gold by
partner. Hand-written replies in the API's real layout (probe 2026-09-29)."""

import json
from datetime import date

import pytest

from almanac import deliver, freshness
from almanac.model import FetchError
from almanac.registry import load
from almanac.run import Ctx
from almanac.sources import census

REG = load()
TODAY = date(2026, 9, 29)
KEY = "CENSUSKEYzz9fake0003"


@pytest.fixture(autouse=True)
def census_key(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", KEY)


def series(sid):
    return next(s for s in REG.series if s["id"] == sid)


class Census:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def get(self, url, headers=None, params=None):
        self.calls.append((url, params or {}))
        return self.replies(url)


def naics(url):
    field = "ALL_VAL_MO" if "/exports/" in url else "GEN_VAL_MO"
    code = url.split("&NAICS=")[1].split("&")[0]
    rows = [["NAICS", field, "NAICS", "CTY_CODE", "time"]]
    months = ["2026-05", "2026-06", "2026-07"]
    if code == "115":
        months = ["2026-06"]  # appears late, then no trade in July
    if code == "980" and field == "GEN_VAL_MO":
        months = []  # never traded in the window
    rows += [[code, "1000", code, "-", m] for m in months]
    return json.dumps(rows).encode()


def gold(url):
    field, com = (("ALL_VAL_MO", "E_COMMODITY") if "/exports/" in url else ("GEN_VAL_MO", "I_COMMODITY"))
    rows = [["CTY_CODE", field, com, com, "COMM_LVL", "time", "CTY_CODE"]]
    for code in series("TRADE_CEN_GOLD")["partners"].values():
        for m in ("2026-06", "2026-07"):
            if not (code == "5520" and m == "2026-07"):  # Vietnam: no gold trade in July
                rows.append([code, "2500" if code == "4419" else "100", "7108", "7108", "HS4", m, code])
    return json.dumps(rows).encode()


def ctx(sid, http, since=deliver.FULL_HISTORY):
    s = series(sid)
    return Ctx(http, s["source"], REG.sources[s["source"]], [s], since, TODAY)


def test_registry_entries():
    n, g = series("TRADE_CEN_NAICS"), series("TRADE_CEN_GOLD")
    assert len(n["measures"]) == 32 and set(n["names"]) == set(n["measures"])
    assert n["names"]["334"] == "COMPUTER & ELECTRONIC PRODUCTS"
    assert len(freshness.expected_ids(n)) == 64 and len(freshness.expected_ids(g)) == 32
    for s in (n, g):
        assert s["redistribution"] == "open" and s["backfill"] == "full" and s["use"] == "G"
        assert deliver.lookback_days(s) == 1300 and freshness.max_age(s) == 105
        assert "not seasonally adjusted" in s["units"] and "Census basis" in s["units"]
        assert "FAS" in s["units"] and "customs" in s["units"]
    assert g["partners"] == series("TRADE_CEN_M")["partners"]  # the same 16 partners


def test_naics_one_request_per_flow_and_industry_no_level_filter():
    http = Census(naics)
    obs = census.fetch(ctx("TRADE_CEN_NAICS", http))
    assert len(http.calls) == 64
    url, params = http.calls[0]
    assert url.startswith("https://api.census.gov/data/timeseries/intltrade/exports/naics?get=NAICS,ALL_VAL_MO"
                          "&time=from+2013-01&NAICS=111&CTY_CODE=-")  # history clamped to 2013-01
    assert "COMM_LVL" not in url and params == {"key": KEY} and KEY not in url
    assert any("/imports/naics?get=NAICS,GEN_VAL_MO" in u for u, _ in http.calls)
    ids = {o.series_id for o in obs}
    assert "TRADE_CEN_NAICS_EXP_334" in ids and "TRADE_CEN_NAICS_IMP_336" in ids


def test_naics_zero_fill_only_after_first_appearance():
    obs = census.fetch(ctx("TRADE_CEN_NAICS", Census(naics)))
    got = {(o.series_id, o.obs_date): o.value for o in obs}
    assert got[("TRADE_CEN_NAICS_EXP_115", "2026-06-01")] == 1000.0
    assert got[("TRADE_CEN_NAICS_EXP_115", "2026-07-01")] == 0.0   # published month, no trade
    assert ("TRADE_CEN_NAICS_EXP_115", "2026-05-01") not in got    # before it first appears
    assert not any(k[0] == "TRADE_CEN_NAICS_IMP_980" for k in got)  # nothing ever: no rows


def test_naics_reply_for_another_code_is_an_error():
    def wrong(url):
        return naics(url.replace("&NAICS=111", "&NAICS=112"))
    with pytest.raises(FetchError, match="only for 111"):
        census.fetch(ctx("TRADE_CEN_NAICS", Census(wrong)))


def test_gold_request_shape_and_zero_fill():
    http = Census(gold)
    obs = census.fetch(ctx("TRADE_CEN_GOLD", http, since=date(2026, 6, 1)))
    assert len(http.calls) == 2
    url = http.calls[0][0]
    assert ("/exports/hs?get=CTY_CODE,ALL_VAL_MO,E_COMMODITY&time=from+2026-06"
            "&COMM_LVL=HS4&E_COMMODITY=7108&CTY_CODE=-") in url
    assert url.count("&CTY_CODE=") == 16
    assert "&I_COMMODITY=7108" in http.calls[1][0]
    got = {(o.series_id, o.obs_date): o.value for o in obs}
    assert len({o.series_id for o in obs}) == 32
    assert got[("TRADE_CEN_GOLD_EXP_CH", "2026-07-01")] == 2500.0
    assert got[("TRADE_CEN_GOLD_IMP_VN", "2026-07-01")] == 0.0  # omitted by Census -> 0


def test_gold_reply_with_an_unrequested_partner_is_an_error():
    def extra(url):
        rows = json.loads(gold(url))
        rows.append(["9999", "1", "7108", "7108", "HS4", "2026-07", "9999"])
        return json.dumps(rows).encode()
    with pytest.raises(FetchError, match="only has requested partners"):
        census.fetch(ctx("TRADE_CEN_GOLD", Census(extra), since=date(2026, 6, 1)))


def test_by_partner_totals_request_is_unchanged():
    http = Census(lambda url: b"")
    census.fetch(ctx("TRADE_CEN_M", http, since=date(2023, 3, 15)))
    assert "/exports/hs?get=CTY_CODE,CTY_NAME,ALL_VAL_MO&time=from+2023-03&CTY_CODE=-" in http.calls[0][0]
