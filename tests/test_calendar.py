"""Step 4: calendar parsers, event ids, horizon, calendar_ahead and cal- bundles -- offline."""

import json
from functools import partial
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from almanac import calendar as cal
from almanac.model import FetchError
from almanac.registry import Registry, load
from almanac.sources import cal_bea, cal_boj, cal_ecb, cal_fomc, cal_fred, cal_rba, cal_snb

FIX = Path(__file__).parent / "fixtures"
REG = load()
TODAY = date(2026, 9, 28)


def body(name):
    return (FIX / name).read_bytes()


def test_fomc_decision_is_last_meeting_day_across_years_and_months():
    got = cal_fomc.parse(body("cal_fomc.html"))
    assert got == [date(2026, 1, 21), date(2026, 10, 21), date(2026, 12, 1), date(2027, 1, 27)]
    # notation vote row skipped; "Nov/Dec 30-1" -> Dec 1


def test_ecb_keeps_day_two_of_monetary_policy_meetings_only():
    assert cal_ecb.parse(body("cal_ecb.html")) == [date(2026, 11, 5), date(2026, 12, 10)]


def test_boj_last_day_per_year_section_not_past_meetings():
    assert cal_boj.parse(body("cal_boj.html")) == [
        date(2026, 1, 14), date(2026, 10, 27), date(2026, 12, 1), date(2027, 1, 20)]


def test_rba_second_day_including_month_boundary():
    assert cal_rba.parse(body("cal_rba.html")) == [
        date(2026, 2, 10), date(2026, 10, 1), date(2026, 11, 17)]


@pytest.mark.parametrize("parser", [cal_fomc.parse, cal_ecb.parse, cal_boj.parse, cal_rba.parse,
                                    cal_snb.parse, cal_bea.parse, partial(cal_fred.parse, rid="10")])
def test_layout_change_is_an_error(parser):
    with pytest.raises(FetchError) as e:
        parser(b"<html><body>page redesigned</body></html>")
    assert e.value.kind == "error"


def test_event_id_and_published_time_in_utc():
    fomc = next(s for s in REG.series if s["id"] == "FOMC")
    assert cal.time_utc(fomc, date(2026, 10, 21)) == "18:00"  # EDT
    assert cal.time_utc(fomc, date(2026, 12, 1)) == "19:00"  # EST
    boj = next(s for s in REG.series if s["id"] == "BOJ_DECISION")
    assert cal.time_utc(boj, date(2026, 10, 27)) is None
    e = cal.Event("2026-10-21", "18:00", "US", "cb_decision", "FOMC decision", "fed_fomc")
    assert e.event_id == "fed_fomc:cb_decision:2026-10-21"


def mini(*sources):
    series = [{"id": s.upper(), "source": s, "key": "page", "freq": "event", "use": "D",
               "status": "active", "kind": "cb_decision", "country": "XX", "name": f"{s} decision"}
              for s in sources]
    return Registry(sources={s: {"url": f"https://{s}.test/"} for s in sources}, series=series)


def test_horizon_is_30_days_back_to_60_days_ahead():
    days = [date(2026, 8, 28), date(2026, 8, 29), date(2026, 11, 27), date(2026, 11, 28), date(2027, 3, 1)]
    res = cal.collect(mini("a"), None, TODAY, {"a": lambda ctx: days})[0]
    assert [e.event_date for e in res.events] == ["2026-08-29", "2026-11-27"]
    assert res.furthest == "2027-03-01" and res.status == "ok"


def test_calendar_ahead_flags_sources_short_of_60_days():
    table = {"a": lambda ctx: [date(2026, 10, 1), date(2026, 12, 15)],  # reaches past Nov 27
             "b": lambda ctx: [date(2026, 10, 20)],  # only 22 days ahead
             "c": lambda ctx: (_ for _ in ()).throw(FetchError("outage", "HTTP 503"))}
    res = cal.collect(mini("a", "b", "c"), None, TODAY, table)
    ahead = cal.calendar_ahead(res, TODAY)
    assert ahead["sources"]["a"] == {"furthest_event": "2026-12-15", "days_ahead": 78, "ok": True, "status": "ok"}
    assert ahead["sources"]["b"]["days_ahead"] == 22 and not ahead["sources"]["b"]["ok"]
    assert ahead["sources"]["c"]["status"] == "outage"
    assert ahead["short"] == ["b", "c"]


def test_moved_event_is_a_new_row_and_old_ids_are_not_redelivered():
    first = cal.collect(mini("a"), None, TODAY, {"a": lambda ctx: [date(2026, 10, 21)]})
    delivered = {r["event_id"] for r in cal.new_rows(first, set())}
    assert delivered == {"a:cb_decision:2026-10-21"}
    moved = cal.collect(mini("a"), None, TODAY, {"a": lambda ctx: [date(2026, 10, 22)]})
    rows = cal.new_rows(moved, delivered)
    assert [r["event_id"] for r in rows] == ["a:cb_decision:2026-10-22"]
    assert cal.new_rows(first, delivered) == []  # unchanged calendar -> nothing new


def test_cal_bundle_contract(tmp_path):
    res = cal.collect(mini("a"), None, TODAY, {"a": lambda ctx: [date(2026, 10, 21), date(2026, 12, 30)]})
    rows = cal.new_rows(res, set())
    ahead = cal.calendar_ahead(res, TODAY)
    path = cal.write_cal_bundle(tmp_path, datetime(2026, 9, 28, 22, 30, tzinfo=timezone.utc), res, rows, ahead)
    assert path.name == "cal-20260928T223000Z" and not (tmp_path / "cal-20260928T223000Z.partial").exists()
    m = json.loads((path / "manifest.json").read_text())
    assert m["contract_version"] == 1 and m["kind"] == "cal" and m["files"][0]["name"] == "events.parquet"
    assert m["freshness"]["calendar_ahead"]["sources"]["a"]["ok"]
    table = pq.read_table(path / "events.parquet")
    assert table.schema.equals(cal.SCHEMA)
    got = table.to_pylist()
    assert [r["event_id"] for r in got] == ["a:cb_decision:2026-10-21"]  # Dec 30 is past the horizon
    assert got[0]["event_time_utc"] is None and got[0]["country"] == "XX"


def test_every_calendar_series_has_contract_fields():
    for s in REG.select("D"):
        assert s["kind"] in ("cb_decision", "release"), s["id"]
        assert s["country"] and s["name"], s["id"]
        assert ("time_local" in s) == ("tz" in s), s["id"]


def test_snb_keeps_monetary_policy_assessments_only():
    assert cal_snb.parse(body("cal_snb.html")) == [date(2026, 12, 3)]


def test_fred_release_calendar_dates_for_one_release():
    assert cal_fred.parse(body("cal_fred_2026.html"), "10") == [
        date(2026, 9, 15), date(2026, 10, 15), date(2026, 12, 11)]
    with pytest.raises(FetchError):  # page for another release id: layout check fails
        cal_fred.parse(body("cal_fred_2026.html"), "50")


def test_fred_fetch_covers_every_year_the_horizon_touches():
    from almanac.run import Ctx

    class Http:
        calls = []

        def get(self, url, headers=None, params=None):
            self.calls.append(url)
            return body("cal_fred_2026.html")

    s = next(x for x in REG.series if x["id"] == "US_CPI_RELEASE")
    http = Http()
    cal_fred.fetch(Ctx(http, "us_cpi_cal", REG.sources["us_cpi_cal"], [s], date(2026, 10, 18), date(2026, 11, 17)))
    assert [u.rsplit("y=", 1)[1] for u in http.calls] == ["2026", "2027"]  # Nov 17 + 60 days is 2027


def test_bea_gdp_releases_only_with_year_per_table():
    assert cal_bea.parse(body("cal_bea.html")) == [
        date(2026, 10, 1), date(2026, 10, 29), date(2027, 1, 28)]


def test_all_active_calendars_have_a_parser_and_mas_is_tbd():
    assert {s["source"] for s in REG.select("D")} <= set(cal.fetchers())
    mas = next(s for s in REG.series if s["id"] == "MAS_DECISION")
    assert mas["status"] == "tbd"


def test_nothing_in_window_is_ok_but_no_dates_at_all_is_empty():
    res = cal.collect(mini("a", "b"), None, TODAY,
                      {"a": lambda ctx: [date(2026, 12, 10)], "b": lambda ctx: []})
    by = {r.source: r for r in res}
    assert by["a"].status == "ok" and by["a"].events == [] and "no events between" in by["a"].reason
    assert by["b"].status == "empty" and by["b"].reason == "no event dates found on the page"
