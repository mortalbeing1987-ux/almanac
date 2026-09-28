"""Use case F: US CPI (adjusted and not), unemployment, payrolls from FRED --
per-key revision look-back, per-key freshness for monthly data."""

from datetime import date, datetime, timedelta, timezone
from itertools import count
from pathlib import Path

from almanac import deliver, freshness
from almanac.registry import Registry, key_settings, keys, load
from almanac.run import Ctx, collect
from almanac.sources import fred

FIX = Path(__file__).parent / "fixtures"
REG = load()
NOW = datetime(2026, 9, 28, 22, 30, tzinfo=timezone.utc)
SECONDS = count()
SA = ("CPIAUCSL", "CPILFESL", "UNRATE", "PAYEMS")  # seasonally adjusted: 5-year revisions


def cpi():
    return next(s for s in REG.series if s["id"] == "CPI")


def test_cpi_keys_and_full_history():
    s = cpi()
    assert keys(s) == ["CPIAUCSL", "CPIAUCNS", "CPILFESL", "UNRATE", "PAYEMS"]
    assert s["backfill"] == "full" and s["use"] == "F" and s["freq"] == "monthly"


def test_unadjusted_cpi_parses_in_fred_layout():
    got = fred.parse((FIX / "fred_CPIAUCNS.csv").read_bytes(), "CPIAUCNS")
    assert got == [("1913-01-01", 9.8), ("1913-02-01", 9.8), ("2026-06-01", 311.111),
                   ("2026-07-01", 312.222)]  # empty latest value skipped


def test_lookback_per_key_follows_how_each_series_is_revised():
    s = cpi()
    for k in SA:  # seasonal factors re-estimated yearly, revising 5 years back
        assert deliver.lookback_days(s, k) >= 5 * 366, k
    assert deliver.lookback_days(s, "CPIAUCNS") == 400  # final when issued: monthly default


def test_freshness_per_key_allows_the_monthly_release_lag():
    s = cpi()
    for k in keys(s):
        limit = freshness.max_age(s, k)
        # August value (dated 1 Aug) is superseded mid-October: ~75 days is normal
        assert 75 <= limit <= 80, (k, limit)
        assert key_settings(s, k)["freq"] == "monthly"


# ---- a simulated seasonal-factor revision ------------------------------------

def months(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


class Fred:
    """Serves fredgraph.csv bodies in FRED's layout, honouring cosd."""

    def __init__(self):
        self.values = {k: {d.isoformat(): 100.0 + i for i, d in
                           enumerate(months(date(2015, 1, 1), date(2026, 8, 1)))}
                       for k in keys(cpi())}

    def get(self, url, headers=None, params=None):
        code = url.split("id=")[1].split("&")[0]
        since = url.split("cosd=")[1][:10]
        rows = [f"{d},{v}" for d, v in sorted(self.values[code].items()) if d >= since]
        return ("observation_date," + code + "\n" + "\n".join(rows) + "\n").encode()


def run(tmp_path, http):
    reg = Registry(sources=REG.sources, series=[cpi()])
    collector = lambda r, u, h, since, today: collect(r, u, h, since, today, {"fred": fred.fetch})
    now = NOW + timedelta(seconds=next(SECONDS))
    return deliver.run(reg, tmp_path, "F", http, now, collector=collector)


def test_seasonal_factor_revision_is_delivered_inside_the_lookback_only(tmp_path):
    http = Fred()
    first = run(tmp_path, http)
    assert first["rows_delivered"] == 5 * 140 and first["revisions_delivered"] == 0
    assert first["freshness"]["stale"] == []  # 2026-08-01 is 58 days old, within the limit

    # New seasonal factors: values 3 years back change (inside the ~5-year
    # look-back) and 6 years back (outside it). The unadjusted CPI is not revised.
    http.values["CPIAUCSL"]["2023-09-01"] += 0.5
    http.values["CPIAUCSL"]["2020-06-01"] += 0.5
    http.values["UNRATE"]["2023-09-01"] += 0.1
    second = run(tmp_path, http)
    assert second["revisions_delivered"] == 2 and second["rows_delivered"] == 2
    bundle = tmp_path / "bundles" / second["bundle"] / "observations.parquet"
    import pyarrow.parquet as pq
    rows = sorted((r["series_id"], r["obs_date"], r["revision"]) for r in pq.read_table(bundle).to_pylist())
    assert rows == [("CPI_CPIAUCSL", "2023-09-01", 1), ("CPI_UNRATE", "2023-09-01", 1)]
    since = date.fromisoformat(second["sources"]["fred"]["since"])
    assert date(2020, 6, 1) < since <= date(2021, 8, 1)  # 2020-06 is outside the look-back


def test_backfilled_history_is_latest_vintage_note_in_spec():
    spec = (Path(__file__).parents[1] / "docs" / "ALMANAC_SPEC.md").read_text()
    assert "latest vintage" in spec
