"""Use case E: CFTC COT (weekly file + yearly zips), SNB sight deposits,
measures per key, weekly freshness limits."""

import io
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from almanac import deliver, freshness, state as state_mod
from almanac.model import FetchError
from almanac.registry import delivered_ids, key_of, load
from almanac.run import Ctx, collect
from almanac.sources import cftc, snb

FIX = Path(__file__).parent / "fixtures"
REG = load()
TODAY = date(2026, 9, 28)
CODES = {"092741", "097741", "099741", "232741", "096742", "090741"}


def series(sid):
    return next(s for s in REG.series if s["id"] == sid)


def zipped(name, member="annual.txt"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, (FIX / name).read_bytes())
    return buf.getvalue()


class Cftc:
    """deafut.txt and deacot<year>.zip from fixtures; other years answer with
    the 2025 layout (or 404, when listed in `missing`)."""

    def __init__(self, missing=(), weekly=None):
        self.calls, self.missing = [], set(missing)
        self.weekly = weekly or (FIX / "cftc_deafut.txt").read_bytes()

    def get(self, url, headers=None, params=None):
        self.calls.append(url)
        if url.endswith("deafut.txt"):
            return self.weekly
        year = int(url.rsplit("deacot", 1)[1][:4])
        if year in self.missing:
            raise FetchError("error", "HTTP 404 from www.cftc.gov/files/dea/history/x.zip")
        if year == 1990:  # older layout: padded fields, member Annual.TXT
            return zipped("cftc_annual_1990.txt", member="Annual.TXT")
        return zipped("cftc_annual_2026.txt" if year == 2026 else "cftc_annual_2025.txt")


def cot_ctx(http, since, today=TODAY):
    return Ctx(http, "cftc", REG.sources["cftc"], [series("COT_FX")], since, today)


def values(obs, sid):
    return {o.obs_date: o.value for o in obs if o.series_id == sid}


# ---- CFTC ------------------------------------------------------------------

def test_cot_contracts_are_identified_by_code_not_name():
    cot = series("COT_FX")
    assert cot["contracts"] == {"CHF": "092741", "JPY": "097741", "EUR": "099741",
                                "AUD": "232741", "GBP": "096742", "CAD": "090741"}
    # the headerless weekly file: only our six codes; wheat and the EUR/GBP cross are skipped
    got = cftc.parse(io.StringIO((FIX / "cftc_deafut.txt").read_text()), CODES, header=False)
    assert {c for c, _ in got} == CODES
    assert got[("092741", "2026-09-22")] == (131700.0, 43900.0, 21957.0)


def test_cot_weekly_and_yearly_zips_measures_and_net():
    http = Cftc()
    obs = cftc.fetch(cot_ctx(http, date(2025, 11, 1)))
    assert [u.rsplit("/", 1)[1] for u in http.calls] == ["deacot2025.zip", "deacot2026.zip", "deafut.txt"]
    ids = {o.series_id for o in obs}
    assert ids == {i for k in series("COT_FX")["key"] for i in delivered_ids(series("COT_FX"), k)}
    assert len(ids) == 24 and "COT_FX_CHF_NC_NET" in ids and "COT_FX_GBP_OI" in ids
    longs, shorts = values(obs, "COT_FX_CHF_NC_LONG"), values(obs, "COT_FX_CHF_NC_SHORT")
    net, oi = values(obs, "COT_FX_CHF_NC_NET"), values(obs, "COT_FX_CHF_OI")
    assert sorted(net) == ["2025-12-23", "2025-12-30", "2026-09-15", "2026-09-22"]  # as-of Tuesdays
    assert all(net[d] == longs[d] - shorts[d] for d in net)
    assert longs["2025-12-30"] == 43100 and oi["2025-12-23"] == 3 * 43101
    # the weekly file wins over the yearly zip for the latest week
    assert longs["2026-09-22"] == 43900 and longs["2026-09-15"] == 43201


def test_cot_full_backfill_reads_every_year_from_1986():
    http = Cftc()
    cftc.fetch(cot_ctx(http, date(1900, 1, 1)))
    years = [int(u.rsplit("deacot", 1)[1][:4]) for u in http.calls if u.endswith(".zip")]
    assert years == list(range(1986, 2027))
    assert http.calls[-1].endswith("deafut.txt")


def test_cot_old_padded_layout_is_read_not_skipped():
    # before ~2015 every field is space-padded ("092741 ,") -- a strict ",092741,"
    # match would silently drop those years
    got = cftc.parse_zip(zipped("cftc_annual_1990.txt", member="Annual.TXT"), CODES)
    assert {c for c, _ in got} == CODES - {"099741"}  # no Euro FX before 1999
    assert got[("092741", "1990-01-15")] == (15312.0, 5104.0, 1701.0)
    assert got[("092741", "1990-01-30")] == (15309.0, 5103.0, 1701.0)
    obs = cftc.fetch(cot_ctx(Cftc(), date(1900, 1, 1)))
    assert values(obs, "COT_FX_CHF_NC_NET")["1990-01-15"] == 5104 - 1701
    assert "1990-01-15" not in values(obs, "COT_FX_EUR_OI")


def test_cot_yearly_zip_header_change_is_an_error():
    body = (FIX / "cftc_annual_2025.txt").read_text().replace(
        "Noncommercial Positions-Long (All)", "Noncommercial Long", 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("annual.txt", body)
    with pytest.raises(FetchError, match="CFTC column 8"):
        cftc.parse_zip(buf.getvalue(), CODES)
    with pytest.raises(FetchError, match="not a zip"):
        cftc.parse_zip(b"<html>moved</html>", CODES)


def test_cot_weekly_layout_change_is_an_error():
    line = (FIX / "cftc_deafut.txt").read_text().splitlines()[2]  # Swiss franc
    # a column inserted after the name: the YYMMDD/YYYY-MM-DD pair no longer lines up
    shifted = line.replace('",', '",NEW,', 1).replace(",092741,", ",", 1)
    with pytest.raises(FetchError, match="date columns"):
        cftc.parse(io.StringIO(shifted.replace("2026-09-22", "092741", 1)), CODES, header=False)
    with pytest.raises(FetchError, match="date columns"):
        cftc.parse(io.StringIO(line.replace("2026-09-22", "2026-09-29")), CODES, header=False)


def test_cot_contract_missing_from_weekly_report_is_an_error():
    weekly = b"\n".join(l for l in (FIX / "cftc_deafut.txt").read_bytes().splitlines()
                        if b",092741," not in l)
    with pytest.raises(FetchError, match="missing \\['CHF'\\]"):
        cftc.fetch(cot_ctx(Cftc(weekly=weekly), date(2026, 9, 1)))


def test_cot_new_year_zip_may_be_missing_only_in_early_january():
    ok = cftc.fetch(cot_ctx(Cftc(missing={2027}), date(2026, 11, 1), today=date(2027, 1, 8)))
    assert values(ok, "COT_FX_EUR_OI")  # 2026 zip + weekly file
    with pytest.raises(FetchError, match="404"):
        cftc.fetch(cot_ctx(Cftc(missing={2026}), date(2026, 7, 1)))


# ---- SNB -------------------------------------------------------------------

class Snb:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append(url)
        return (FIX / "snb_snbgwdchfsgw.csv").read_bytes()


def test_snb_sight_deposits_total_and_domestic_banks():
    http = Snb()
    obs = snb.fetch(Ctx(http, "snb", REG.sources["snb"], [series("SNB_SIGHT")], date(1900, 1, 1), TODAY))
    assert http.calls == ["https://data.snb.ch/api/cube/snbgwdchfsgw/data/csv/en"]  # one call per cube
    assert values(obs, "SNB_SIGHT_TG") == {"2026-09-18": 25555, "2026-09-25": 25744}  # empty skipped
    assert values(obs, "SNB_SIGHT_GI") == {"2009-01-09": 11111, "2026-09-18": 22222, "2026-09-25": 22444}
    assert {o.series_id for o in obs} == {"SNB_SIGHT_TG", "SNB_SIGHT_GI"}  # UEB not collected


def test_snb_missing_dimension_code_is_an_error():
    with pytest.raises(FetchError, match="D0 code TG"):
        snb.parse((FIX / "snb_snbgwdzid.csv").read_bytes(), "TG")


# ---- registry, freshness, start dates ----------------------------------------

def test_e_freshness_limits_and_ids():
    cot, sight = series("COT_FX"), series("SNB_SIGHT")
    assert len(freshness.expected_ids(cot)) == 24
    assert freshness.expected_ids(sight) == ["SNB_SIGHT_TG", "SNB_SIGHT_GI"]
    assert key_of(cot, "COT_FX_CHF_NC_NET") == "CHF" and key_of(cot, "COT_FX_AUD_OI") == "AUD"
    assert freshness.max_age(cot, "CHF") == 11  # Tuesday as-of, Friday release, holiday Monday
    assert freshness.max_age(sight, "TG") == 10
    assert cot["backfill"] == sight["backfill"] == "full"


def test_real_be_is_backfilled_in_full():
    assert series("REAL_BE")["backfill"] == "full"
    st = state_mod.State()
    since = deliver.since_by_series([series("REAL_BE")], st, TODAY)
    assert since["REAL_BE"] == deliver.FULL_HISTORY


def test_measures_start_from_delivered_ids_not_a_new_backfill():
    cot = series("COT_FX")
    st = state_mod.State()
    assert deliver.since_by_series([cot], st, TODAY)["COT_FX"] == deliver.FULL_HISTORY
    for k in cot["key"]:
        for sid in delivered_ids(cot, k):
            st.series[sid] = {"last_obs": "2026-09-22", "source": "cftc"}
    # weekly look-back (90 days), not a second full backfill
    assert deliver.since_by_series([cot], st, TODAY)["COT_FX"] == date(2026, 6, 24)


class Both:
    def __init__(self):
        self.cftc = Cftc()

    def get(self, url, headers=None, params=None):
        if "cftc.gov" in url:
            return self.cftc.get(url)
        return (FIX / "snb_snbgwdchfsgw.csv").read_bytes()


def test_deliver_e_twice_second_pass_delivers_nothing(tmp_path):
    now = datetime(2026, 9, 28, 22, 30, tzinfo=timezone.utc)
    first = deliver.run(REG, tmp_path, "E", Both(), now)
    assert first["sources"]["cftc"]["status"] == "ok" and first["sources"]["snb"]["status"] == "ok"
    assert first["sources"]["cftc"]["since"] == "1900-01-01"
    assert first["rows_delivered"] == 24 * 4 + 5 * 2 * 4 + 5 and first["revisions_delivered"] == 0
    assert first["freshness"]["stale"] == []
    again = deliver.run(REG, tmp_path, "E", Both(), now)
    assert again["rows_delivered"] == 0 and again["bundle"] is None
    assert again["sources"]["cftc"]["since"] == "2026-06-24"


def test_cot_is_its_own_canary():
    class Down:
        def get(self, url, headers=None, params=None):
            raise FetchError("outage", "HTTP 503 from www.cftc.gov after 5 attempts")

    res = {r.source: r for r in collect(REG, "E", Down(), date(2026, 9, 1), TODAY)}
    assert res["cftc"].status == "outage" and "canary COT_FX" in res["cftc"].reason
    assert res["cftc"].observations == []
