"""Use case C: CBOE layouts, per-key frequency, full backfill, weekly FRED codes."""

from datetime import date
from pathlib import Path

import pytest

from almanac import deliver, freshness, state as state_mod
from almanac.model import FetchError
from almanac.registry import Registry, key_settings, keys, load
from almanac.run import Ctx, collect
from almanac.sources import cboe, fred

FIX = Path(__file__).parent / "fixtures"
REG = load()
TODAY = date(2026, 9, 28)


def series(sid):
    return next(s for s in REG.series if s["id"] == sid)


def test_cboe_ohlc_file_delivers_the_close():
    got = cboe.parse((FIX / "cboe_VIX.csv").read_bytes(), "VIX")
    assert got == [("1990-01-02", 11.15), ("2026-09-24", 21.5), ("2026-09-25", 21.25)]


def test_cboe_single_column_file_uses_the_index_column():
    got = cboe.parse((FIX / "cboe_VVIX.csv").read_bytes(), "VVIX")
    assert got == [("2006-03-06", 81.11), ("2026-09-25", 91.25)]


def test_cboe_layout_change_is_an_error():
    with pytest.raises(FetchError):
        cboe.parse(b"Date,Price\n09/25/2026,1\n", "VIX")
    with pytest.raises(FetchError):
        cboe.parse(b"DATE,OPEN,HIGH,LOW\n09/25/2026,1,2,0\n", "VIX3M")


class Files:
    def __init__(self, fail=()):
        self.calls, self.fail = [], fail

    def get(self, url, headers=None, params=None):
        self.calls.append(url)
        name = url.rsplit("/", 1)[1].split("_")[0] if "cboe" in url else url.split("id=")[1].split("&")[0]
        if name in self.fail:
            raise FetchError("outage", "HTTP 503")
        path = FIX / (f"cboe_{name}.csv" if "cboe" in url else f"fred_{name}.csv")
        return path.read_bytes() if path.exists() else (FIX / "cboe_VIX.csv").read_bytes()


def test_vol_index_ids_and_vix_is_the_canary():
    vol = series("VOL_INDEX")
    assert keys(vol)[0] == "VIX"
    http = Files()
    obs = cboe.fetch(Ctx(http, "cboe", REG.sources["cboe"], [vol], date(1900, 1, 1), TODAY))
    assert {o.series_id for o in obs} == {"VOL_INDEX_VIX", "VOL_INDEX_VIX3M", "VOL_INDEX_VVIX"}
    res = collect(REG, "C", Files(fail={"VIX"}), date(2026, 9, 1), TODAY,
                  table={"cboe": cboe.fetch, "fred": lambda ctx: []})
    by = {r.source: r for r in res}
    assert by["cboe"].status == "outage" and by["cboe"].observations == []
    assert by["cboe"].reason.startswith("canary VOL_INDEX")


def test_per_key_frequency_and_publication_lag():
    reg = series("REGIME_FRED")
    assert key_settings(reg, "T10Y3M")["freq"] == "daily"
    assert key_settings(reg, "NFCI") == {"freq": "weekly", "max_age_days": 14}
    assert freshness.max_age(reg, "T10Y2Y") == 4
    assert freshness.max_age(reg, "ICSA") == 14
    assert freshness.max_age(series("USD_INDEX"), "DTWEXEMEGS") == 12
    assert deliver.lookback_days(reg, "NFCI") == 90 and deliver.lookback_days(reg, "T10Y3M") == 30


def test_freshness_uses_each_keys_own_limit():
    st = state_mod.State(series={
        "REGIME_FRED_T10Y3M": {"source": "fred", "last_obs": "2026-09-25"},
        "REGIME_FRED_T10Y2Y": {"source": "fred", "last_obs": "2026-09-18"},
        "REGIME_FRED_NFCI": {"source": "fred", "last_obs": "2026-09-18"},
        "REGIME_FRED_ICSA": {"source": "fred", "last_obs": "2026-09-19"},
        "USD_INDEX_DTWEXBGS": {"source": "fred", "last_obs": "2026-09-18"}})
    f = freshness.summary([series("REGIME_FRED"), series("USD_INDEX")], st, TODAY)
    assert f["series"]["REGIME_FRED_NFCI"]["fresh"] and f["series"]["REGIME_FRED_ICSA"]["fresh"]
    assert f["series"]["USD_INDEX_DTWEXBGS"]["fresh"]  # 10 days <= 12
    assert not f["series"]["REGIME_FRED_T10Y2Y"]["fresh"]  # daily, 10 days > 4
    assert "REGIME_FRED_T10Y2Y" in f["stale"] and "REGIME_FRED_NFCI" not in f["stale"]


def test_weekly_key_looks_back_90_days_inside_a_daily_series():
    reg = series("REGIME_FRED")
    st = state_mod.State(series={f"REGIME_FRED_{k}": {"last_obs": "2026-09-25", "backfilled_from": "full"} for k in keys(reg)})
    assert deliver.since_by_series([reg], st, TODAY)["REGIME_FRED"] == date(2026, 6, 27)  # NFCI/ICSA: -90d


def test_backfill_full_and_default():
    assert deliver.backfill_start(series("VOL_INDEX"), TODAY) == deliver.FULL_HISTORY
    assert deliver.backfill_start(series("SOFR"), TODAY) == date(2025, 9, 28)
    assert deliver.backfill_start({"backfill": 3650}, TODAY) == date(2016, 9, 30)
    for sid in ("VOL_INDEX", "REGIME_FRED", "USD_INDEX"):
        assert series(sid)["backfill"] == "full"


def test_full_backfill_asks_fred_for_everything():
    http = Files()
    obs = fred.fetch(Ctx(http, "fred", REG.sources["fred"], [dict(series("REGIME_FRED"), key=["NFCI"])],
                         deliver.FULL_HISTORY, TODAY))
    assert "cosd=1900-01-01" in http.calls[0]
    assert [(o.series_id, o.obs_date) for o in obs][-1] == ("REGIME_FRED_NFCI", "2026-09-18")


def test_per_key_only_names_real_keys_and_known_fields():
    for s in REG.series:
        for k, overrides in s.get("per_key", {}).items():
            assert k in keys(s), (s["id"], k)
            assert set(overrides) <= {"freq", "max_age_days", "lookback_days"}, (s["id"], k)
        assert s["freq"] != "mixed (daily/weekly per key)", s["id"]
        if "backfill" in s:
            assert s["backfill"] == "full" or int(s["backfill"]) > 0, s["id"]
