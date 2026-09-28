"""PR 7: CORRA (Bank of Canada Valet) and SONIA (Bank of England IADB)."""

from datetime import date
from pathlib import Path

import pytest

from almanac import deliver, freshness
from almanac.model import FetchError
from almanac.registry import load
from almanac.run import Ctx
from almanac.sources import boc, boe

FIX = Path(__file__).parent / "fixtures"
REG = load()
TODAY = date(2026, 9, 28)


def series(sid):
    return next(s for s in REG.series if s["id"] == sid)


class Http:
    def __init__(self, fixture):
        self.fixture, self.calls = fixture, []

    def get(self, url, headers=None, params=None):
        self.calls.append((url, headers, params))
        return (FIX / self.fixture).read_bytes()


def test_corra_from_valet_csv():
    http = Http("boc_corra.csv")
    obs = boc.fetch(Ctx(http, "boc", REG.sources["boc"], [series("CORRA")], date(2026, 9, 1), TODAY))
    assert [(o.series_id, o.obs_date, o.value) for o in obs] == [
        ("CORRA", "2026-09-22", 1.1111), ("CORRA", "2026-09-24", 1.1122), ("CORRA", "2026-09-25", 1.1133)]
    url, headers, params = http.calls[0]
    assert url == "https://www.bankofcanada.ca/valet/observations/AVG.INTWO/csv?start_date=2026-09-01"
    assert not headers and not params  # keyless


def test_corra_layout_changes_are_errors():
    with pytest.raises(FetchError, match="OBSERVATIONS"):
        boc.parse(b'"date","AVG.INTWO"\n"2026-09-25","1.1"\n', "AVG.INTWO")
    body = (FIX / "boc_corra.csv").read_bytes().replace(b'"date","AVG.INTWO"', b'"date","V39079"')
    with pytest.raises(FetchError, match="header"):
        boc.parse(body, "AVG.INTWO")


def test_sonia_from_iadb_csv():
    http = Http("boe_sonia.csv")
    obs = boe.fetch(Ctx(http, "boe", REG.sources["boe"], [series("SONIA")], date(2026, 9, 1), TODAY))
    assert [(o.series_id, o.obs_date, o.value) for o in obs] == [
        ("SONIA", "2026-09-22", 2.2211), ("SONIA", "2026-09-23", 2.2222), ("SONIA", "2026-09-24", 2.2233)]
    url = http.calls[0][0]
    assert "SeriesCodes=IUDSOIA" in url and "Datefrom=01/Sep/2026" in url


def test_sonia_date_param_is_english_whatever_the_locale():
    assert boe.boe_date(date(2025, 9, 28)) == "28/Sep/2025"
    assert boe.boe_date(date(1997, 1, 2)) == "02/Jan/1997"


def test_sonia_layout_changes_are_errors():
    with pytest.raises(FetchError, match="header"):
        boe.parse(b"<html>Service unavailable</html>", "IUDSOIA")
    with pytest.raises(FetchError, match="IADB date"):
        boe.parse(b"DATE,IUDSOIA\n2026-09-24,2.2\n", "IUDSOIA")


def test_b_settings_backfill_and_freshness():
    corra, sonia = series("CORRA"), series("SONIA")
    for s in (corra, sonia):
        assert s["use"] == "B" and s["freq"] == "daily" and "backfill" not in s
        assert deliver.backfill_start(s, TODAY) == date(2025, 9, 28)  # 365 days, like the other B rates
    assert freshness.max_age(corra) == 4
    assert freshness.max_age(sonia) == 7  # the free IADB copy lags a working day; Easter
