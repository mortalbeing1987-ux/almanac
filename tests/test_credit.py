"""Long-history credit spreads: Moody's Baa/Aaa minus 10y (FRED, restricted)
and the Fed's Gilchrist-Zakrajsek file. Fixtures are synthetic."""

from datetime import date
from pathlib import Path

import pytest

from almanac import deliver, freshness
from almanac.model import FetchError
from almanac.registry import load
from almanac.run import Ctx
from almanac.sources import fed_gz, fred

FIX = Path(__file__).parent / "fixtures"
REG = load()
TODAY = date(2026, 9, 29)


def series(sid):
    return next(s for s in REG.series if s["id"] == sid)


class Http:
    def __init__(self, fixture):
        self.fixture, self.calls = fixture, []

    def get(self, url, headers=None, params=None):
        self.calls.append(url)
        return (FIX / self.fixture).read_bytes()


def test_moodys_registry_entry():
    s = series("CREDIT_MOODYS")
    assert s["source"] == "fred" and s["key"] == ["BAA10Y", "AAA10Y"] and s["use"] == "A"
    assert s["redistribution"] == "restricted" and s["backfill"] == "full"
    assert s["units"] == "percentage points" and s["freq"] == "daily"
    assert deliver.backfill_start(s, TODAY) == deliver.FULL_HISTORY


def test_moodys_full_history_request_and_parse():
    http = Http("fred_BAA10Y.csv")
    s = dict(series("CREDIT_MOODYS"), key=["BAA10Y"])
    obs = fred.fetch(Ctx(http, "fred", REG.sources["fred"], [s], deliver.FULL_HISTORY, TODAY))
    assert "id=BAA10Y&cosd=1900-01-01" in http.calls[0]
    assert [(o.series_id, o.obs_date) for o in obs] == [
        ("CREDIT_MOODYS_BAA10Y", "1986-01-02"), ("CREDIT_MOODYS_BAA10Y", "1986-01-03"),
        ("CREDIT_MOODYS_BAA10Y", "2026-09-24")]  # empty latest value skipped


def test_gz_parse_all_three_keys_first_of_month():
    s = series("CREDIT_GZ")
    obs = fed_gz.fetch(Ctx(Http("fed_gz_ebp.csv"), "fed_gz", REG.sources["fed_gz"], [s], deliver.FULL_HISTORY, TODAY))
    got = {(o.series_id, o.obs_date): o.value for o in obs}
    assert got[("CREDIT_GZ_GZ_SPREAD", "1973-01-01")] == 1.111
    assert got[("CREDIT_GZ_EBP", "2026-07-01")] == 0.133
    assert got[("CREDIT_GZ_EST_PROB", "2026-06-01")] == 0.1244
    assert ("CREDIT_GZ_EBP", "2026-06-01") not in got  # empty cell skipped
    assert {o.series_id for o in obs} == {"CREDIT_GZ_GZ_SPREAD", "CREDIT_GZ_EBP", "CREDIT_GZ_EST_PROB"}


@pytest.mark.parametrize("body,match", [
    (b"date,gz_spread,excess_bond_premium,est_prob\n1/1/1973,1,2,3\n", "column 'ebp'"),
    (b"month,gz_spread,ebp,est_prob\n1/1/1973,1,2,3\n", "first column 'date'"),
    (b"date,gz_spread,ebp,est_prob\n1973-01-01,1,2,3\n", "M/D/YYYY"),
    (b"date,gz_spread,ebp,est_prob\n1/15/1973,1,2,3\n", "first of the month"),
    (b"date,gz_spread,ebp,est_prob\n2/1/1973,1,2,3\n1/1/1973,1,2,3\n", "ascending"),
    (b"<html>Page not found</html>", "first column 'date'"),
])
def test_gz_layout_changes_are_errors(body, match):
    with pytest.raises(FetchError, match=match):
        fed_gz.parse(body, series("CREDIT_GZ")["columns"])


def test_gz_settings():
    s = series("CREDIT_GZ")
    assert s["redistribution"] == "open" and s["backfill"] == "full" and s["freq"] == "monthly"
    assert deliver.lookback_days(s) >= 20000  # the whole history may revise monthly
    assert freshness.max_age(s) == s["max_age_days"]
    assert len(freshness.expected_ids(s)) == 3
