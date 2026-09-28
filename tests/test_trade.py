"""Use case G: BEA release workbooks (monthly totals, services categories,
quarterly by partner), Census goods by partner, cross-checks (FRED, BEA ITA),
key safety. All offline, synthetic data in the real layouts."""

import io
import json
import urllib.error
from datetime import date, datetime, timezone
from email.message import Message
from pathlib import Path

import pytest

import trade_fixtures as tf
from almanac import deliver, freshness
from almanac.http import Http
from almanac.model import FetchError
from almanac.registry import Registry, delivered_ids, keys, load
from almanac.run import Ctx
from almanac.sources import bea_ita, bea_release, census

REG = load()
TODAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 22, 30, tzinfo=timezone.utc)
P14 = ["AU", "CA", "CH", "CN", "DE", "EU", "GB", "IN", "JP", "KR", "MX", "SG", "TW", "VN"]
P16 = ["WORLD", "EU", "EA"] + [p for p in P14 if p != "EU"]
CEN_CODES = {"WORLD": "-", "EU": "0003", "EA": "0025", "AU": "6021", "CA": "1220", "CH": "4419", "CN": "5700",
             "DE": "4280", "GB": "4120", "IN": "5330", "JP": "5880", "KR": "5800", "MX": "2010", "SG": "5590",
             "TW": "5830", "VN": "5520"}
ITA_AREAS = {"AU": "Australia", "CA": "Canada", "CH": "Switzerland", "CN": "China", "DE": "Germany", "EU": "EU",
             "GB": "UnitedKingdom", "IN": "India", "JP": "Japan", "KR": "SouthKorea", "MX": "Mexico",
             "SG": "Singapore", "TW": "Taiwan", "VN": "Vietnam"}
GEO_COL = {"AU": "Australia", "CA": "Canada", "CH": "Switzerland", "CN": "China", "DE": "Germany",
           "EU": "European Union", "GB": "United Kingdom", "IN": "India", "JP": "Japan", "KR": "Korea, South",
           "MX": "Mexico", "SG": "Singapore", "TW": "Taiwan", "VN": "Vietnam"}
FAKE_BEA, FAKE_CENSUS = "BEAKEYzz9fake0001", "CENSUSKEYzz9fake0002"


def series(sid):
    return next(s for s in REG.series if s["id"] == sid)


def census_reply(field, months=("2026-06", "2026-07"), drop=()):
    rows = [["CTY_CODE", "CTY_NAME", field, "time", "CTY_CODE"]]
    for p, code in CEN_CODES.items():
        if p in drop:
            continue
        for m in months:
            rows.append([code, f"NAME {p}", str(int(tf.value("cen", field, p, m) * 1000)), m, code])
    return json.dumps(rows).encode()


def ita_reply(indicator, years=(2025, 2026), shift=None):
    data = []
    t = {"ExpGds": 4, "ImpGds": 5, "ExpServ": 7, "ImpServ": 8}[indicator]
    for p, area in ITA_AREAS.items():
        for y, q in [(2026, 1), (2026, 2)]:
            v = tf.value("geo", t, y, q, GEO_COL[p])
            if shift and shift == (p, y, q):
                v += 50.0
            data.append({"AreaOrCountry": area, "TimePeriod": f"{y}Q{q}", "DataValue": f"{v:,.1f}",
                         "UNIT_MULT": "6", "CL_UNIT": "USD", "Indicator": indicator})
    return json.dumps({"BEAAPI": {"Request": {"RequestParam": [{"ParameterName": "USERID",
                                                               "ParameterValue": FAKE_BEA}]},
                                  "Results": {"Data": data}}}).encode()


class Web:
    """Routes every trade URL to a synthetic body; records the requests."""

    def __init__(self, page=None, ts=None, geo=None, ita_shift=None, fail=()):
        self.page = page or tf.release_page()
        self.ts, self.geo = ts or tf.time_series(), geo or tf.geo()
        self.ita_shift, self.fail, self.calls = ita_shift, set(fail), []

    def get(self, url, headers=None, params=None):
        self.calls.append((url, headers or {}, params or {}))
        for f in self.fail:
            if f in url:
                raise FetchError("outage", f"HTTP 503 from {f} after 5 attempts")
        if "international-trade-goods-and-services" in url:
            return self.page
        if url.endswith("-geo-time-series.xlsx"):
            return self.geo
        if url.endswith("-time-series.xlsx"):
            return self.ts
        if "api.census.gov" in url:
            return census_reply("ALL_VAL_MO" if "/exports/" in url else "GEN_VAL_MO")
        if "apps.bea.gov" in url:
            ind = url.split("Indicator=")[1].split("&")[0]
            return ita_reply(ind, shift=self.ita_shift)
        if "fredgraph.csv" in url:
            code = url.split("id=")[1].split("&")[0]
            # Table 1 columns: B-D balance, E-G exports, H-J imports (Total, Goods, Services)
            target = {"BOPTEXP": 5, "BOPTIMP": 8, "BOPGSTB": 2, "BOPGTB": 3}[code]
            rows = [f"{y}-{m:02d}-01,{tf.value('t1', y, m, target)}" for y, m in tf.months()]
            return ("observation_date," + code + "\n" + "\n".join(rows) + "\n").encode()
        raise AssertionError(f"unexpected url {url}")


def ctx(sid, http, since=date(1900, 1, 1), src=None):
    s = series(sid)
    return Ctx(http, s["source"], REG.sources[src or s["source"]], [s], since, TODAY)


# ---- ids, units, registry ----------------------------------------------------------

def test_id_families_and_count():
    counts = {sid: len(freshness.expected_ids(series(sid)))
              for sid in ("TRADE_BEA_M", "TRADE_BEA_MS", "TRADE_BEA_Q", "TRADE_CEN_M")}
    assert counts == {"TRADE_BEA_M": 9, "TRADE_BEA_MS": 22, "TRADE_BEA_Q": 126, "TRADE_CEN_M": 32}
    assert sum(counts.values()) == 189
    ids = set(freshness.expected_ids(series("TRADE_BEA_Q")))
    assert {"TRADE_BEA_Q_EXP_GS_CH", "TRADE_BEA_Q_BAL_S_EU", "TRADE_BEA_Q_IMP_G_SG"} <= ids
    assert "TRADE_CEN_M_EXP_G_WORLD" in freshness.expected_ids(series("TRADE_CEN_M"))
    assert "TRADE_BEA_MS_IMP_TELECOM" in freshness.expected_ids(series("TRADE_BEA_MS"))
    for sid in ("TRADE_BEA_M", "TRADE_BEA_MS", "TRADE_BEA_Q", "TRADE_CEN_M"):
        u = series(sid)["units"]
        assert "USD" in u and ("seasonally adjusted" in u)
    assert "not seasonally adjusted" in series("TRADE_CEN_M")["units"] and "Census basis" in series("TRADE_CEN_M")["units"]
    assert "balance of payments" in series("TRADE_BEA_M")["units"]
    old = {"TRADE_MONTHLY", "TRADE_BEA_EU", "TRADE_BEA", "TRADE_CENSUS"}
    assert not old & {s["id"] for s in REG.series}


def test_freshness_and_lookback_settings():
    assert freshness.max_age(series("TRADE_BEA_M")) == 105
    assert freshness.max_age(series("TRADE_CEN_M")) == 105
    assert freshness.max_age(series("TRADE_BEA_Q")) == 260
    assert deliver.lookback_days(series("TRADE_CEN_M")) == 1300
    assert deliver.lookback_days(series("TRADE_BEA_MS")) >= 20000


# ---- workbook discovery ---------------------------------------------------------------

def test_newest_release_is_picked_per_file():
    links = bea_release.release_links(tf.release_page().decode(), REG.sources["bea_release"]["files"],
                                      REG.sources["bea_release"]["base"])
    assert links["time_series"][0] == date(2026, 7, 1) and links["time_series"][1].endswith("trad0726-time-series.xlsx")
    assert links["geo"][0] == date(2026, 7, 1)


def test_stale_workbook_for_its_release_number_is_an_error():
    web = Web(page=tf.release_page(ts=("0826",)), ts=tf.time_series(last=(2026, 7)))
    with pytest.raises(FetchError, match="latest period 2026-07-01 is release trad0826"):
        bea_release.fetch(ctx("TRADE_BEA_M", web))


def test_geo_workbook_must_be_the_one_for_the_quarter():
    # time series trad1026 needs geo trad1026; the page still links trad0726
    web = Web(page=tf.release_page(ts=("1026",), geo_releases=("0726",)), ts=tf.time_series(last=(2026, 10)))
    with pytest.raises(FetchError, match="geo workbook trad0726 is the one for release trad1026"):
        bea_release.fetch(ctx("TRADE_BEA_M", web))
    # between quarters the geo workbook legitimately lags (trad0826 -> trad0726)
    ok = Web(page=tf.release_page(ts=("0826",), geo_releases=("0726",)), ts=tf.time_series(last=(2026, 8)))
    assert bea_release.fetch(ctx("TRADE_BEA_Q", ok))


def test_page_without_workbook_links_is_an_error():
    with pytest.raises(FetchError, match="links the"):
        bea_release.fetch(ctx("TRADE_BEA_M", Web(page=b"<html>maintenance</html>")))


# ---- workbook contents ---------------------------------------------------------------

def test_table1_groups_by_label_monthly_dating_annual_rows_skipped():
    obs = bea_release.fetch(ctx("TRADE_BEA_M", Web()))
    got = {(o.series_id, o.obs_date): o.value for o in obs}
    assert len({o.series_id for o in obs}) == 9
    # Balance is columns B-D, Exports E-G, Imports H-J (label, not position)
    assert got[("TRADE_BEA_M_BAL_GS", "1992-01-01")] == tf.value("t1", 1992, 1, 2)
    assert got[("TRADE_BEA_M_EXP_G", "2026-07-01")] == tf.value("t1", 2026, 7, 6)
    assert got[("TRADE_BEA_M_IMP_S", "2026-06-01")] == tf.value("t1", 2026, 6, 10)
    assert all(o.value != 999999.0 for o in obs)  # the annual row
    assert {d for _, d in got} == {"1992-01-01", "1992-02-01", "2026-06-01", "2026-07-01"}


def test_services_categories_both_tables():
    obs = bea_release.fetch(ctx("TRADE_BEA_MS", Web()))
    ids = {o.series_id for o in obs}
    assert len(ids) == 22 and "TRADE_BEA_MS_EXP_TRAVEL" in ids  # "Travel 1" footnote marker stripped
    got = {(o.series_id, o.obs_date): o.value for o in obs}
    assert got[("TRADE_BEA_MS_IMP_TRANSPORT", "1999-01-01")] == tf.value("t3", 1999, 1, 4)


def test_renamed_or_split_category_is_an_error_not_a_splice():
    web = Web(ts=tf.time_series(drop_category="Financial Services"))
    with pytest.raises(FetchError, match="column 'Financial Services'"):
        bea_release.fetch(ctx("TRADE_BEA_MS", web))


def test_quarterly_by_partner_quarter_start_dates_and_na():
    obs = bea_release.fetch(ctx("TRADE_BEA_Q", Web()))
    ids = {o.series_id for o in obs}
    assert len(ids) == 126
    got = {(o.series_id, o.obs_date): o.value for o in obs}
    assert got[("TRADE_BEA_Q_EXP_S_CH", "2026-04-01")] == tf.value("geo", 7, 2026, 2, "Switzerland")  # Q2
    assert got[("TRADE_BEA_Q_BAL_GS_EU", "1999-01-01")] == tf.value("geo", 3, 1999, 1, "European Union")
    assert ("TRADE_BEA_Q_EXP_GS_VN", "1999-01-01") not in got  # n.a.
    assert ("TRADE_BEA_Q_EXP_GS_VN", "2026-04-01") in got


def test_workbook_layout_changes_fail_loudly():
    with pytest.raises(FetchError, match="unit line"):
        bea_release.fetch(ctx("TRADE_BEA_Q", Web(geo=tf.geo(unit="[Billions of dollars, quarters seasonally adjusted]"))))
    with pytest.raises(FetchError, match="column 'Korea, South'"):
        bea_release.fetch(ctx("TRADE_BEA_Q", Web(geo=tf.geo(partners=[p for p in tf.PARTNERS if p != "Korea, South"]))))
    with pytest.raises(FetchError, match="readable .xlsx"):
        bea_release.fetch(ctx("TRADE_BEA_M", Web(ts=b"<html>error</html>")))


def test_one_download_per_file_per_run():
    web = Web()
    for sid in ("TRADE_BEA_M", "TRADE_BEA_MS", "TRADE_BEA_Q"):
        bea_release.fetch(ctx(sid, web))
    urls = [u for u, _, _ in web.calls]
    assert len(urls) == 3 and len(set(urls)) == 3  # page, time series, geo


# ---- Census ---------------------------------------------------------------------------

def test_census_one_request_per_flow_all_partners(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", FAKE_CENSUS)
    web = Web()
    obs = census.fetch(ctx("TRADE_CEN_M", web, since=date(2023, 3, 15)))
    assert len(web.calls) == 2
    url, _, params = web.calls[0]
    assert "/exports/hs?get=CTY_CODE,CTY_NAME,ALL_VAL_MO&time=from+2023-03" in url
    assert url.count("&CTY_CODE=") == 16 and "&CTY_CODE=-" in url and "&CTY_CODE=0025" in url
    assert params == {"key": FAKE_CENSUS} and FAKE_CENSUS not in url
    got = {(o.series_id, o.obs_date): o.value for o in obs}
    assert len({o.series_id for o in obs}) == 32
    assert got[("TRADE_CEN_M_IMP_G_EA", "2026-07-01")] == float(int(tf.value("cen", "GEN_VAL_MO", "EA", "2026-07") * 1000))


def test_census_start_is_clamped_to_2010(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", FAKE_CENSUS)
    web = Web()
    census.fetch(ctx("TRADE_CEN_M", web))
    assert "time=from+2010-01" in web.calls[0][0]


def test_census_missing_partner_or_header_is_an_error(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", FAKE_CENSUS)
    with pytest.raises(FetchError, match="Census header"):
        census.parse(b'[["CTY_CODE","CTY_NAME","time"]]', "ALL_VAL_MO")
    with pytest.raises(FetchError, match="Census JSON table"):
        census.parse(b"<html>Invalid Key</html>", "ALL_VAL_MO")
    assert census.parse(b"", "ALL_VAL_MO") == []

    class Short(Web):
        def get(self, url, headers=None, params=None):
            return census_reply("ALL_VAL_MO" if "/exports/" in url else "GEN_VAL_MO", drop={"TW"})

    with pytest.raises(FetchError, match=r"missing \['TW'\]"):
        census.fetch(ctx("TRADE_CEN_M", Short(), since=date(2026, 6, 1)))


# ---- ITA ------------------------------------------------------------------------------

def test_ita_quarters_and_error_code_only():
    got = bea_ita.parse(ita_reply("ExpServ"))
    assert ("Switzerland", "2026-04-01", tf.value("geo", 7, 2026, 2, "Switzerland")) in got
    echo = json.dumps({"BEAAPI": {"Request": {"RequestParam": [{"ParameterName": "USERID", "ParameterValue": FAKE_BEA}]},
                                  "Results": {"Error": {"APIErrorCode": "40", "APIErrorDescription":
                                                        f"Invalid Indicator for UserID {FAKE_BEA}"}}}}).encode()
    with pytest.raises(FetchError) as e:
        bea_ita.parse(echo)
    assert e.value.reason == "BEA API error code 40" and FAKE_BEA not in e.value.reason


# ---- cross-checks and a full G pass -----------------------------------------------------

def g_run(tmp_path, web, monkeypatch, now=NOW):
    monkeypatch.setenv("BEA_API_KEY", FAKE_BEA)
    monkeypatch.setenv("CENSUS_API_KEY", FAKE_CENSUS)
    return deliver.run(REG, tmp_path, "G", web, now)


def test_g_pass_delivers_189_ids_crosschecks_ok_and_second_pass_is_empty(tmp_path, monkeypatch):
    web = Web()
    first = g_run(tmp_path, web, monkeypatch)
    assert {k: v["status"] for k, v in first["sources"].items()} == {"bea_release": "ok", "census": "ok"}
    delivered = set(first["freshness"]["series"])
    assert len(delivered) == 189
    x = first["crosschecks"]
    assert x["TRADE_TOTAL_XCHECK"]["status"] == "ok" and x["TRADE_TOTAL_XCHECK"]["compared"] == 4 * 2  # window: 1100 days
    assert x["TRADE_ITA_XCHECK"]["status"] == "ok" and x["TRADE_ITA_XCHECK"]["compared"] == 4 * 14 * 2
    assert first["revisions_delivered"] == 0
    again = g_run(tmp_path, web, monkeypatch, datetime(2026, 9, 29, 2, 30, tzinfo=timezone.utc))
    assert again["rows_delivered"] == 0


def test_crosscheck_mismatch_is_a_warning_with_ids_and_dates_only(tmp_path, monkeypatch):
    status = g_run(tmp_path, Web(ita_shift=("JP", 2026, 2)), monkeypatch)
    x = status["crosschecks"]["TRADE_ITA_XCHECK"]
    assert x["status"] == "mismatch" and x["mismatch_count"] == 4
    assert {"series_id": "TRADE_BEA_Q_EXP_S_JP", "obs_date": "2026-04-01"} in x["mismatches"]
    assert all(set(m) == {"series_id", "obs_date"} for m in x["mismatches"])  # no values
    assert status["rows_delivered"] > 0 and status["sources"]["bea_release"]["status"] == "ok"  # never a block


def test_crosscheck_source_outage_is_unavailable_not_a_block(tmp_path, monkeypatch):
    status = g_run(tmp_path, Web(fail={"apps.bea.gov", "fredgraph"}), monkeypatch)
    assert {x["status"] for x in status["crosschecks"].values()} == {"unavailable"}
    assert status["rows_delivered"] > 0


# ---- key safety -------------------------------------------------------------------------

class Opener:
    """urlopen stand-in: BEA answers with an error that echoes the key; Census
    fails with an HTTP error whose URL carries the key; the rest is served."""

    def __init__(self):
        self.web = Web()

    def __call__(self, req, timeout=None):
        url = req.full_url
        if "apps.bea.gov" in url:
            body = json.dumps({"BEAAPI": {"Request": {"RequestParam": [
                {"ParameterName": "USERID", "ParameterValue": FAKE_BEA}]},
                "Results": {"Error": {"APIErrorCode": "3", "APIErrorDescription": f"bad key {FAKE_BEA}"}}}})
            return io.BytesIO(body.encode())
        if "api.census.gov" in url:
            raise urllib.error.HTTPError(url, 400, f"Bad Request for {url}", Message(), None)
        return io.BytesIO(self.web.get(url))


def test_fake_keys_never_reach_reasons_logs_status_or_manifest(tmp_path, monkeypatch, capsys):
    from almanac import __main__ as cli
    monkeypatch.setenv("BEA_API_KEY", FAKE_BEA)
    monkeypatch.setenv("CENSUS_API_KEY", FAKE_CENSUS)
    monkeypatch.setattr(deliver, "Http", lambda: Http(opener=Opener(), sleep=lambda s: None))
    cli.main(["deliver", "--data-dir", str(tmp_path), "--uses", "G"])
    out = capsys.readouterr()
    status = (tmp_path / "status.json").read_text()
    texts = [out.out, out.err, status, (tmp_path / "state" / "series.json").read_text()]
    texts += [p.read_text() for p in tmp_path.glob("bundles/*/manifest.json")]
    assert list(tmp_path.glob("bundles/*/manifest.json"))  # the workbook series were delivered
    for t in texts:
        assert FAKE_BEA not in t and FAKE_CENSUS not in t
        assert "zz9fake" not in t  # not even partially
    st = json.loads(status)
    assert st["sources"]["census"]["status"] == "error" and "HTTP 400 from api.census.gov" in st["sources"]["census"]["reason"]
    assert st["crosschecks"]["TRADE_ITA_XCHECK"]["status"] == "unavailable"
    assert "BEA API error code 3" in st["crosschecks"]["TRADE_ITA_XCHECK"]["reason"]
