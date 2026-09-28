"""Step 2: canary, delivery state, freshness and a delivery pass -- all offline."""

import json
from datetime import date, datetime, timezone

from almanac import deliver, freshness, state as state_mod
from almanac.model import FetchError, Observation, SourceResult
from almanac.registry import Registry, load
from almanac.run import collect

REG = load()
NOW = datetime(2026, 9, 28, 22, 30, tzinfo=timezone.utc)


def mini_registry():
    return Registry(
        sources={"a": {"url": "x"}, "b": {"url": "y"}},
        series=[
            {"id": "A1", "source": "a", "key": "k1", "freq": "daily", "use": "B", "status": "active"},
            {"id": "A2", "source": "a", "key": "k2", "freq": "daily", "use": "B", "status": "active"},
            {"id": "B1", "source": "b", "key": ["x", "y"], "freq": "monthly", "use": "B", "status": "active"},
        ])


def fetcher(values, fail=()):
    """Fake fetcher: `values` maps series id -> [(date, value)]; ids in `fail` raise."""
    def fn(ctx):
        out = []
        for s in ctx.series:
            if s["id"] in fail:
                raise FetchError("outage", f"{s['id']} down")
            for d, v in values.get(s["id"], []):
                out.append(Observation(s["id"], d, v, ctx.source))
        return out
    return fn


def test_canary_failure_writes_nothing_for_that_source():
    reg = mini_registry()
    table = {"a": fetcher({"A2": [("2026-09-25", 1.0)]}, fail={"A1"}),
             "b": fetcher({"B1": [("2026-09-01", 2.0)]})}
    res = {r.source: r for r in collect(reg, "B", None, date(2026, 9, 1), date(2026, 9, 28), table)}
    assert res["a"].status == "outage" and res["a"].observations == []
    assert "canary A1" in res["a"].reason
    assert res["b"].status == "ok"


def test_failure_after_canary_keeps_canary_rows_and_is_an_error():
    reg = mini_registry()
    table = {"a": fetcher({"A1": [("2026-09-25", 1.0)]}, fail={"A2"}), "b": fetcher({})}
    res = {r.source: r for r in collect(reg, "B", None, date(2026, 9, 1), date(2026, 9, 28), table)}
    assert res["a"].status == "error" and len(res["a"].observations) == 1
    assert res["b"].status == "empty"


def test_state_roundtrip(tmp_path):
    st = state_mod.State()
    rows = [{"series_id": "A1", "obs_date": "2026-09-25", "value": 1.5, "revision": 0},
            {"series_id": "A1", "obs_date": "2026-09-26", "value": 1.6, "revision": 0}]
    results = [SourceResult("a", "ok", observations=[Observation("A1", "2026-09-26", 1.6, "a")])]
    st.apply(rows, results, {"a": {"A1", "A2"}}, "2026-09-28T22:30:00+00:00")
    state_mod.save(st, tmp_path)
    back = state_mod.load(tmp_path)
    assert back.delivered[("A1", "2026-09-26")] == (1.6, 0)
    assert back.series["A1"]["first_obs"] == "2026-09-25" and back.series["A1"]["last_obs"] == "2026-09-26"
    assert back.series["A1"]["last_success"] == "2026-09-28T22:30:00+00:00"
    assert "last_success" not in back.series["A2"]  # attempted, nothing delivered


def test_freshness_by_frequency_and_never_delivered():
    reg = mini_registry()
    st = state_mod.State(series={"A1": {"source": "a", "last_obs": "2026-09-25"},
                                 "A2": {"source": "a", "last_obs": "2026-09-20"},
                                 "B1_x": {"source": "b", "last_obs": "2026-08-01"}})
    f = freshness.summary(reg.select("B"), st, date(2026, 9, 28))
    assert f["series"]["A1"]["fresh"] and f["series"]["A1"]["age_days"] == 3
    assert not f["series"]["A2"]["fresh"]  # 8 days > 4 for daily
    assert f["series"]["B1_x"]["max_age_days"] == 45
    assert not f["series"]["B1_x"]["fresh"]  # 58 days > 45 for monthly
    assert f["series"]["B1_y"]["last_obs"] is None and not f["series"]["B1_y"]["fresh"]
    assert set(f["stale"]) == {"A2", "B1_x", "B1_y"}


def test_since_backfills_new_series_and_looks_back_otherwise():
    reg = mini_registry()
    today = date(2026, 9, 28)
    st = state_mod.State(series={"A1": {"last_obs": "2026-09-25"}, "A2": {"last_obs": "2026-09-26"}})
    since = deliver.since_by_source(reg.select("B"), st, today, lookback=30, backfill=365)
    assert since["a"] == date(2026, 8, 26)  # oldest last_obs minus 30 days
    assert since["b"] == date(2025, 9, 28)  # never delivered: backfill


def test_two_delivery_passes_insert_only(tmp_path):
    reg = mini_registry()
    first = {"A1": [("2026-09-24", 1.0), ("2026-09-25", 1.1)], "A2": [("2026-09-25", 2.0)],
             "B1": [("2026-08-01", 5.0)]}

    def col(values):
        return lambda reg_, uses, http, since, today: collect(
            reg_, uses, http, since, today, {"a": fetcher(values), "b": fetcher(values)})

    s1 = deliver.run(reg, tmp_path, "B", now=NOW, collector=col(first))
    assert s1["rows_delivered"] == 4 and s1["bundle"] == "macro-20260928T223000Z"

    second = dict(first, A1=[("2026-09-24", 1.0), ("2026-09-25", 1.15), ("2026-09-28", 1.2)])
    later = datetime(2026, 9, 29, 2, 30, tzinfo=timezone.utc)
    s2 = deliver.run(reg, tmp_path, "B", now=later, collector=col(second))
    assert s2["rows_delivered"] == 2  # one revision, one new date; unchanged values not re-sent

    import pyarrow.parquet as pq
    rows = pq.read_table(tmp_path / "bundles" / s2["bundle"] / "observations.parquet").to_pylist()
    assert {(r["obs_date"], r["revision"]) for r in rows} == {("2026-09-25", 1), ("2026-09-28", 0)}
    assert (tmp_path / "bundles" / s1["bundle"] / "manifest.json").exists()  # first bundle untouched

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["freshness"]["series"]["A1"]["last_obs"] == "2026-09-28"
    manifest = json.loads((tmp_path / "bundles" / s2["bundle"] / "manifest.json").read_text())
    assert manifest["freshness"]["series"] == len(status["freshness"]["series"])


def test_outage_run_writes_status_but_no_bundle(tmp_path):
    reg = mini_registry()
    col = lambda reg_, uses, http, since, today: collect(  # noqa: E731
        reg_, uses, http, since, today, {"a": fetcher({}, fail={"A1"}), "b": fetcher({}, fail={"B1"})})
    s = deliver.run(reg, tmp_path, "B", now=NOW, collector=col)
    assert s["bundle"] is None and s["rows_delivered"] == 0
    assert not (tmp_path / "bundles").exists()
    st = state_mod.load(tmp_path)
    assert st.series["A1"]["last_status"] == "outage"  # recorded, never "up to date"
    assert set(s["freshness"]["stale"]) == {"A1", "A2", "B1_x", "B1_y"}
