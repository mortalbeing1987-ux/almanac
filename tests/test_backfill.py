"""Deepening: raising `backfill` on a series that was already delivered fetches
the older history once, records the horizon, then returns to the look-back."""

import json
from datetime import date, datetime, timedelta, timezone
from itertools import count

from almanac import deliver, state as state_mod
from almanac.model import FetchError, Observation
from almanac.registry import Registry, load
from almanac.run import collect

NOW = datetime(2026, 9, 28, 22, 30, tzinfo=timezone.utc)
TODAY = NOW.date()
SECONDS = count()  # each run a second later, same day (distinct run ids)
# The source's whole history: it starts in 2003, like DFII10.
HISTORY = {"2003-01-02": 1.0, "2010-06-01": 2.0, "2020-03-02": 3.0,
           "2025-12-01": 4.0, "2026-09-24": 5.0, "2026-09-25": 6.0}


def reg(**extra):
    return Registry(sources={"f": {"url": "x"}}, series=[
        {"id": "R", "source": "f", "key": ["x", "y"], "freq": "daily", "use": "A",
         "status": "active", **extra}])


class Source:
    """Fake fetcher: serves HISTORY from ctx.since; records each start date."""

    def __init__(self):
        self.starts, self.down = [], False

    def __call__(self, ctx):
        self.starts.append(ctx.since)
        if self.down:
            raise FetchError("outage", "HTTP 503 after 5 attempts")
        return [Observation(f"R_{k}", d, v, "f") for k in ("x", "y")
                for d, v in HISTORY.items() if d >= ctx.since.isoformat()]


def run(tmp_path, registry, src):
    collector = lambda r, u, http, since, today: collect(r, u, http, since, today, {"f": src})
    now = NOW + timedelta(seconds=next(SECONDS))
    return deliver.run(registry, tmp_path, "A", object(), now, collector=collector,
                       cal_collector=None)


def records(tmp_path):
    series = json.loads((tmp_path / "state" / "series.json").read_text())
    return {sid: m.get("backfilled_from") for sid, m in series.items()}


def delivered_at_old_default(tmp_path):
    """State as production has it: delivered with the 365-day default before
    `backfilled_from` existed."""
    src = Source()
    first = run(tmp_path, reg(), src)
    assert src.starts == [date(2025, 9, 28)] and first["rows_delivered"] == 2 * 3
    assert records(tmp_path) == {"R_x": "2025-09-28", "R_y": "2025-09-28"}  # first delivery records
    series = json.loads((tmp_path / "state" / "series.json").read_text())
    for m in series.values():
        del m["backfilled_from"]
    (tmp_path / "state" / "series.json").write_text(json.dumps(series))


def test_raising_backfill_to_full_deepens_once_then_looks_back(tmp_path):
    delivered_at_old_default(tmp_path)
    src = Source()
    deeper = run(tmp_path, reg(backfill="full"), src)
    assert src.starts == [deliver.FULL_HISTORY]
    assert deeper["sources"]["f"]["since"] == "1900-01-01"
    # insert-only: the already delivered values are dropped as unchanged; only older rows are new
    assert deeper["rows_delivered"] == 2 * 3 and deeper["revisions_delivered"] == 0
    st = state_mod.load(tmp_path)
    assert st.series["R_x"]["first_obs"] == "2003-01-02"
    assert records(tmp_path) == {"R_x": "full", "R_y": "full"}
    again = run(tmp_path, reg(backfill="full"), src)
    assert src.starts[-1] == date(2026, 8, 26)  # last obs minus the daily 30-day look-back
    assert again["rows_delivered"] == 0


def test_history_that_starts_late_is_not_deepened_every_run(tmp_path):
    # Asked for 1900, the source starts in 2003: first_obs stays 2003, but the
    # recorded horizon ("full") says the full fetch was done.
    src = Source()
    run(tmp_path, reg(backfill="full"), src)
    for _ in range(3):
        run(tmp_path, reg(backfill="full"), src)
    assert src.starts == [deliver.FULL_HISTORY] + [date(2026, 8, 26)] * 3
    assert state_mod.load(tmp_path).series["R_x"]["first_obs"] == "2003-01-02"


def test_outage_during_deepening_keeps_the_old_record_and_retries(tmp_path):
    delivered_at_old_default(tmp_path)
    src = Source()
    src.down = True
    failed = run(tmp_path, reg(backfill="full"), src)
    assert failed["sources"]["f"]["status"] == "outage" and failed["rows_delivered"] == 0
    assert records(tmp_path) == {"R_x": None, "R_y": None}  # still the old default
    src.down = False
    retry = run(tmp_path, reg(backfill="full"), src)
    assert src.starts == [deliver.FULL_HISTORY, deliver.FULL_HISTORY]
    assert retry["rows_delivered"] == 2 * 3
    assert records(tmp_path) == {"R_x": "full", "R_y": "full"}


def test_error_after_canary_does_not_record_the_new_horizon(tmp_path):
    delivered_at_old_default(tmp_path)
    ok = Source()
    partial = Registry(sources={"f": {"url": "x"}}, series=reg(backfill="full").series + [
        {"id": "Z", "source": "f", "key": "z", "freq": "daily", "use": "A", "status": "active"}])

    def table(ctx):
        if ctx.series[0]["id"] == "Z":
            raise FetchError("error", "HTTP 400")
        return ok(ctx)

    res = run(tmp_path, partial, table)
    assert res["sources"]["f"]["status"] == "error"
    assert records(tmp_path)["R_x"] is None  # retried next run
    assert deliver.since_by_series(partial.select("A"), state_mod.load(tmp_path), TODAY)["R"] \
        == deliver.FULL_HISTORY


def test_a_deeper_number_of_days_also_deepens_once(tmp_path):
    delivered_at_old_default(tmp_path)
    src = Source()
    deeper = run(tmp_path, reg(backfill=3650), src)
    assert src.starts == [date(2016, 9, 30)] and deeper["rows_delivered"] == 2  # 2020-03-02 only
    assert records(tmp_path) == {"R_x": "2016-09-30", "R_y": "2016-09-30"}
    run(tmp_path, reg(backfill=3650), src)
    assert src.starts[-1] == date(2026, 8, 26)


def test_a_shallower_or_equal_backfill_never_refetches():
    st = state_mod.State(series={"R_x": {"last_obs": "2026-09-25", "backfilled_from": "full"},
                                 "R_y": {"last_obs": "2026-09-25", "backfilled_from": "full"}})
    for b in ("full", 365, 3650):
        assert deliver.since_by_series(reg(backfill=b).select("A"), st, TODAY)["R"] == date(2026, 8, 26)
    # no record = the old 365-day default: 365 is not deeper, "full" is
    legacy = state_mod.State(series={"R_x": {"last_obs": "2026-09-25"}, "R_y": {"last_obs": "2026-09-25"}})
    assert deliver.since_by_series(reg().select("A"), legacy, TODAY)["R"] == date(2026, 8, 26)
    assert deliver.since_by_series(reg(backfill="full").select("A"), legacy, TODAY)["R"] \
        == deliver.FULL_HISTORY


def test_real_be_delivered_at_365_days_is_deepened():
    real_be = next(s for s in load().series if s["id"] == "REAL_BE")
    legacy = state_mod.State(series={f"REAL_BE_{k}": {"last_obs": "2026-09-25", "source": "fred"}
                                     for k in real_be["key"]})
    assert deliver.since_by_series([real_be], legacy, TODAY)["REAL_BE"] == deliver.FULL_HISTORY
