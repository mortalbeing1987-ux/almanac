"""Offline fetcher tests against hand-written fixtures (tests/fixtures)."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from almanac.bundle import write_macro_bundle
from almanac.model import FetchError
from almanac.registry import load
from almanac.run import Ctx, collect
from almanac.sources import boj, ecb, fred, mas, nyfed, rba, snb, treasury

FIX = Path(__file__).parent / "fixtures"
REG = load()
TODAY = date(2026, 9, 28)
SINCE = date(2026, 9, 20)


class FakeHttp:
    """Serves fixtures by URL substring; records requests (url, headers, params)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, headers=None, params=None):
        self.calls.append((url, headers or {}, params or {}))
        for part, item in self.routes.items():
            if part in url:
                if isinstance(item, Exception):
                    raise item
                return (FIX / item).read_bytes()
        raise AssertionError(f"unexpected url {url}")


def ctx(source, http, series_ids=None):
    series = [s for s in REG.select("ABCDEFG") if s["source"] == source
              and (series_ids is None or s["id"] in series_ids)]
    return Ctx(http, source, REG.sources[source], series, SINCE, TODAY)


def pairs(obs):
    return sorted((o.series_id, o.obs_date, o.value) for o in obs)


def test_fred_skips_missing_and_limits_dates():
    http = FakeHttp({"id=DGS10": "fred_DGS10.csv"})
    s = {"id": "CMT", "key": ["DGS10"], "source": "fred"}
    obs = fred.fetch(Ctx(http, "fred", REG.sources["fred"], [s], SINCE, TODAY))
    assert pairs(obs) == [("CMT_DGS10", "2026-09-23", 1.11), ("CMT_DGS10", "2026-09-26", 1.14)]
    assert "cosd=2026-09-20" in http.calls[0][0]


def test_fred_header_mismatch_is_an_error():
    http = FakeHttp({"id=DGS30": "fred_DGS10.csv"})
    s = {"id": "CMT", "key": ["DGS30"], "source": "fred"}
    with pytest.raises(FetchError) as e:
        fred.fetch(Ctx(http, "fred", REG.sources["fred"], [s], SINCE, TODAY))
    assert e.value.kind == "error"


def test_treasury_tenors_including_6_week_bill():
    http = FakeHttp({"daily-treasury-rates.csv/2026": "treasury_2026.csv"})
    obs = treasury.fetch(ctx("treasury", http))
    got = {(o.series_id, o.obs_date): o.value for o in obs}
    assert got[("UST_PAR_CURVE_1M", "2026-09-26")] == 1.01
    assert got[("UST_PAR_CURVE_6W", "2026-09-26")] == 1.02
    assert got[("UST_PAR_CURVE_30Y", "2026-09-25")] == 2.14
    assert ("UST_PAR_CURVE_20Y", "2026-09-25") not in got  # blank cell
    assert len(obs) == 27


def test_treasury_fetches_both_years_across_new_year():
    http = FakeHttp({"daily-treasury-rates.csv/": "treasury_2026.csv"})
    c = ctx("treasury", http)
    c = Ctx(c.http, c.source, c.src, c.series, date(2025, 12, 20), date(2026, 1, 5))
    treasury.fetch(c)
    assert [u.split(".csv/")[1][:4] for u, _, _ in http.calls] == ["2025", "2026"]


def test_treasury_unknown_column_is_an_error():
    with pytest.raises(FetchError):
        treasury.tenor("Overnight")


def test_nyfed_sofr_and_effr():
    http = FakeHttp({"/api/rates/": "nyfed_sofr.json"})
    obs = nyfed.fetch(ctx("nyfed", http))
    assert ("SOFR", "2026-09-26", 1.21) in pairs(obs)
    assert {o.series_id for o in obs} == {"SOFR", "EFFR"}
    assert http.calls[0][0].endswith("/last/9.json")  # since..today inclusive


def test_ecb_estr():
    http = FakeHttp({"data-api.ecb.europa.eu": "ecb_estr.csv"})
    obs = ecb.fetch(ctx("ecb", http))
    assert pairs(obs) == [("ESTR", "2026-09-25", 1.31), ("ESTR", "2026-09-26", 1.32)]
    assert "startPeriod=2026-09-20" in http.calls[0][0]


def test_snb_saron_only_skips_empty():
    http = FakeHttp({"cube/snbgwdzid": "snb_snbgwdzid.csv"})
    obs = snb.fetch(ctx("snb", http, {"SARON"}))
    assert pairs(obs) == [("SARON", "2026-09-25", 1.41), ("SARON", "2026-09-26", 1.42)]


def test_boj_follows_pagination_and_skips_null():
    http = FakeHttp({"startPosition=2": "boj_page2.csv", "getDataCode": "boj_page1.csv"})
    obs = boj.fetch(ctx("boj", http))
    assert pairs(obs) == [("TONA", "2026-09-24", 1.51), ("TONA", "2026-09-26", 1.522)]
    assert len(http.calls) == 2


def test_boj_api_error_status():
    http = FakeHttp({"getDataCode": "boj_error.csv"})
    with pytest.raises(FetchError) as e:
        boj.fetch(ctx("boj", http))
    assert "400" in e.value.reason


def test_rba_cash_rate_target():
    http = FakeHttp({"f1-data.csv": "rba_f1.csv"})
    obs = rba.fetch(ctx("rba", http))
    assert pairs(obs) == [("RBA_CASH", "2026-09-25", 1.61), ("RBA_CASH", "2026-09-26", 1.62)]


def test_mas_sora_sends_key_header(monkeypatch):
    monkeypatch.setenv("MAS_API_KEY", "test-key")
    http = FakeHttp({"eservices.mas.gov.sg": "mas_rates.json"})
    obs = mas.fetch(ctx("mas", http))
    assert pairs(obs) == [("SORA", "2026-09-25", 1.71), ("SORA", "2026-09-26", 1.72)]
    assert http.calls[0][1] == {"KeyId": "test-key"}


def test_mas_without_secret_is_an_error_not_a_request(monkeypatch):
    monkeypatch.delenv("MAS_API_KEY", raising=False)
    http = FakeHttp({})
    with pytest.raises(FetchError) as e:
        mas.fetch(ctx("mas", http))
    assert "MAS_API_KEY" in e.value.reason and http.calls == []


ROUTES = {
    "id=DGS": "fred_DGS10.csv", "id=": "fred_DGS10.csv",
    "daily-treasury-rates.csv/": "treasury_2026.csv", "/api/rates/": "nyfed_sofr.json",
    "data-api.ecb.europa.eu": "ecb_estr.csv", "cube/snbgwdzid": "snb_snbgwdzid.csv",
    "getDataCode": "boj_page2.csv", "f1-data.csv": "rba_f1.csv",
    "eservices.mas.gov.sg": "mas_rates.json", "bankofcanada.ca/valet/": "boc_corra.csv",
    "boeapps/database/": "boe_sonia.csv", "ebp_csv.csv": "fed_gz_ebp.csv",
}


def test_collect_A_and_B_end_to_end(tmp_path, monkeypatch):
    """Every A/B source through collect() and into a bundle. FRED's header check
    fails for codes other than DGS10 (one shared fixture), which exercises the
    error path; outages and missing secrets are recorded, never 'up to date'."""
    monkeypatch.setenv("MAS_API_KEY", "k")
    results = collect(REG, "AB", FakeHttp(ROUTES), SINCE, TODAY)
    status = {r.source: r.status for r in results}
    # fed_gz is monthly (latest 2026-07): nothing since 2026-09-20 is `empty`, never "up to date"
    assert status == {"boc": "ok", "boe": "ok", "boj": "ok", "ecb": "ok", "fed_gz": "empty", "fred": "error", "mas": "ok",
                      "nyfed": "ok", "rba": "ok", "snb": "ok", "treasury": "ok"}
    path = write_macro_bundle(tmp_path, datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc), results)
    table = pq.read_table(path / "observations.parquet").to_pylist()
    assert {r["series_id"] for r in table} >= {"SOFR", "EFFR", "ESTR", "SARON", "TONA",
                                                "RBA_CASH", "SORA", "CORRA", "SONIA",
                                                "UST_PAR_CURVE_10Y"}
    assert all(r["obs_date"] >= "2026-09-20" and r["revision"] == 0 for r in table)
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["sources"]["fred"]["status"] == "error"


def test_outage_and_empty_are_not_up_to_date():
    http = FakeHttp({"/api/rates/": FetchError("outage", "HTTP 503 after 5 attempts"),
                     "data-api.ecb.europa.eu": "ecb_estr.csv"})
    results = collect(REG, "B", http, date(2026, 9, 27), TODAY,
                      table={"nyfed": nyfed.fetch, "ecb": ecb.fetch})
    by = {r.source: r for r in results}
    assert by["nyfed"].status == "outage"
    assert by["ecb"].status == "empty"  # nothing on/after the 27th in the fixture
    assert by["boj"].status == "error" and "no fetcher" in by["boj"].reason
