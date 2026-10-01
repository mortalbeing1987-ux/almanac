"""PUTCALL: Cboe put/call volume ratios from history CSVs + one daily JSON per trading day (hand-made fixtures)."""

import json
from datetime import date

import pytest

from almanac import deliver
from almanac.model import FetchError
from almanac.registry import keys, load
from almanac.run import Ctx
from almanac.sources import cboe_pc

REG = load()
TODAY = date(2026, 9, 30)


def series():
    return next(x for x in REG.series if x["id"] == "PUTCALL")


def day_json(call=100.0, put=80.0):
    sec = lambda c, p: [{"name": "VOLUME", "call": c, "put": p, "total": c + p},
                        {"name": "OPEN INTEREST", "call": 1, "put": 1, "total": 2}]
    doc = {"ratios": [{"name": "TOTAL PUT/CALL RATIO", "value": "0.80"}]}
    for section, _f in cboe_pc.GROUPS.values():
        doc[section] = sec(call, put)
    doc["EQUITY OPTIONS"] = sec(200.0, 100.0)
    doc["CBOE VOLATILITY INDEX (VIX)"] = sec(0, 5)     # no call volume that day: no ratio
    return json.dumps(doc).encode()


CSV_TOTAL = (b"Volume and Put/Call Ratio data is compiled for the convenience of site visitors\r\n"
             b", PRODUCT: TOTAL,,EXCHANGE: Cboe,\r\nDATE,CALLS,PUTS,TOTAL,P/C Ratio\r\n"
             b"11/1/2006,1401036,1271445,2672481,0.91\r\n10/04/2019, 2175006, 2289715, 4464721, 1.05\r\n")
CSV_SPX = (b"Cboe Volume and Put/Call Ratio data is provided for informational purposes only.\r\n"
           b"Date,SPX Put/Call Ratio,SPX Put Volume,SPX Call Volume,Total SPX Options Volume\r\n"
           b"7/6/2010,1.91,446308,233843,680151\r\n10/04/2019, 2.32, 1015727, 437756, 1453483\r\n")


class Http:
    """Serves day_json() on the given dates, 403 AccessDenied elsewhere; records every URL."""

    def __init__(self, days=(), csv=None):
        self.days, self.csv, self.calls = set(days), csv or {}, []

    def get(self, url, headers=None, params=None):
        self.calls.append(url)
        if url.endswith(".csv"):
            return self.csv.get(url.rsplit("/", 1)[1][:-4], CSV_TOTAL)
        d = url.rsplit("/", 1)[1][:10]
        if d in self.days:
            return day_json()
        raise FetchError("error", "HTTP 403 from cdn.cboe.com/data/us/options/market_statistics/daily/x")


def ctx(http, since, today=TODAY):
    return Ctx(http, "cboe_pc", REG.sources["cboe_pc"], [series()], since, today)


def test_registry_entry():
    s = series()
    assert keys(s) == ["TOTAL", "INDEX", "ETP", "EQUITY", "VIX", "SPX"]
    assert s["source"] == "cboe_pc" and s["use"] == "C" and s["backfill"] == "full"
    assert deliver.backfill_start(s, TODAY) == deliver.FULL_HISTORY


def test_csv_ratio_is_put_over_call_volume_for_both_header_shapes():
    assert cboe_pc.parse_csv(CSV_TOTAL, "TOTAL") == [("2006-11-01", 1271445 / 1401036), ("2019-10-04", 2289715 / 2175006)]
    assert cboe_pc.parse_csv(CSV_SPX, "SPX") == [("2010-07-06", 446308 / 233843), ("2019-10-04", 1015727 / 437756)]
    with pytest.raises(FetchError):
        cboe_pc.parse_csv(b"nothing,here\r\n1,2\r\n", "X")


def test_daily_json_ratios_and_groups_without_calls_are_left_out():
    got = cboe_pc.parse_day(day_json())
    assert got["TOTAL"] == 0.8 and got["EQUITY"] == 0.5 and "VIX" not in got
    with pytest.raises(FetchError):
        cboe_pc.parse_day(b"<Error>AccessDenied</Error>")
    with pytest.raises(FetchError):
        cboe_pc.parse_day(json.dumps({"ratios": []}).encode())


def test_weekends_are_never_requested_and_a_holiday_403_is_skipped():
    http = Http(days={"2026-09-28", "2026-09-30"})                  # Tue 29th has no file: a "holiday"
    obs = cboe_pc.fetch(ctx(http, date(2026, 9, 26)))                # Sat 26th .. Wed 30th
    asked = [u.rsplit("/", 1)[1][:10] for u in http.calls]
    assert asked == ["2026-09-28", "2026-09-29", "2026-09-30"]       # Sat/Sun not asked
    assert {o.obs_date for o in obs} == {"2026-09-28", "2026-09-30"}
    assert {o.series_id for o in obs} == {f"PUTCALL_{k}" for k in ("TOTAL", "INDEX", "ETP", "EQUITY", "SPX")}


def test_history_csvs_stop_where_the_json_starts():
    http = Http(days={"2019-10-07"}, csv={"spxpc": CSV_SPX})
    obs = cboe_pc.fetch(ctx(http, date(2019, 10, 1), today=date(2019, 10, 7)))
    dates = {o.obs_date for o in obs}
    assert "2019-10-04" in dates and "2019-10-07" in dates and "2006-11-01" not in dates   # since filter
    csv_calls = [u for u in http.calls if u.endswith(".csv")]
    assert len(csv_calls) == 6
    assert not any("2019-10-04_daily" in u for u in http.calls)                          # JSON era starts 10-07


def test_five_weekdays_in_a_row_without_a_file_is_an_error_not_up_to_date():
    http = Http(days=())
    with pytest.raises(FetchError) as e:
        cboe_pc.fetch(ctx(http, date(2026, 9, 21)))
    assert e.value.kind == "error" and "5 weekdays in a row" in e.value.reason


def test_other_failures_propagate_and_the_run_is_capped(monkeypatch):
    class Down(Http):
        def get(self, url, headers=None, params=None):
            raise FetchError("outage", "TimeoutError from cdn.cboe.com after 5 attempts")
    with pytest.raises(FetchError) as e:
        cboe_pc.fetch(ctx(Down(), date(2026, 9, 28)))
    assert e.value.kind == "outage"

    monkeypatch.setattr(cboe_pc, "MAX_DAYS_PER_RUN", 2)
    http = Http(days={f"2026-09-{d:02d}" for d in range(21, 31)})
    obs = cboe_pc.fetch(ctx(http, date(2026, 9, 21)))
    assert sorted({o.obs_date for o in obs}) == ["2026-09-21", "2026-09-22"]               # oldest first, resumes next run
