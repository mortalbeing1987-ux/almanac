"""One scheduled delivery pass into a checked-out PRIVATE data repo.

    <data-dir>/bundles/macro-<run_id>/   new observations (only when there are new rows)
    <data-dir>/bundles/cal-<run_id>/     new calendar events (use case D; only new event ids)
    <data-dir>/state/                    delivery state (see state.py; events.json for D)
    <data-dir>/status.json               freshness + per-source outcome of this run

Committing and pushing the data repo is left to the workflow.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import calendar, freshness, state as state_mod
from .bundle import assign_revisions, run_id_for, write_macro_bundle
from .http import Http
from .registry import Registry, delivered_ids, key_settings, keys
from .run import collect

# Re-fetch window, by frequency, so revisions to already-delivered values are
# caught: daily rates settle within weeks, but monthly and quarterly data get
# revised much further back (payroll benchmarks, annual trade revisions).
# A series can override with `lookback_days` in the registry.
LOOKBACK_DAYS = {"daily": 30, "weekly": 90, "monthly": 400, "quarterly": 800}
DEFAULT_LOOKBACK = 30  # frequencies not listed above
BACKFILL_DAYS = 365  # default first-delivery depth; a series can set `backfill`
FULL_HISTORY = date(1900, 1, 1)  # backfill = "full": ask the source for everything


def lookback_days(series: dict, key: str | None = None) -> int:
    ks = key_settings(series, key)
    return int(ks.get("lookback_days", LOOKBACK_DAYS.get(ks.get("freq", ""), DEFAULT_LOOKBACK)))


def backfill_start(series: dict, today: date, default: int = BACKFILL_DAYS) -> date:
    """First-delivery horizon: `backfill = "full"` or a number of days (default 365)."""
    b = series.get("backfill", default)
    return FULL_HISTORY if b == "full" else today - timedelta(days=int(b))


def backfill_record(series: dict, today: date, default: int = BACKFILL_DAYS) -> str:
    """What state/series.json records as `backfilled_from` once a fetch from the
    registry's horizon succeeded: "full", or the date the fetch started from."""
    return "full" if series.get("backfill", default) == "full" \
        else backfill_start(series, today, default).isoformat()


def recorded_horizon(meta: dict, today: date, default: int = BACKFILL_DAYS) -> date:
    """How deep an id has been fetched: its `backfilled_from` record; an id
    delivered before the record existed counts as the old default (365 days)."""
    rec = meta.get("backfilled_from")
    if rec is None:
        return today - timedelta(days=default)
    return FULL_HISTORY if rec == "full" else date.fromisoformat(rec)


def _ids(s: dict, st: state_mod.State) -> list[tuple[str | None, str]]:
    """(key, delivered id) pairs of a series; treasury tenor ids come from state."""
    if s["source"] == "treasury":
        return [(None, sid) for sid in sorted(st.series) if sid.startswith(s["id"] + "_")]
    return [(k if isinstance(s["key"], list) else None, i) for k in keys(s) for i in delivered_ids(s, k)]


def since_by_series(selected: list[dict], st: state_mod.State, today: date,
                    backfill: int = BACKFILL_DAYS) -> dict[str, date]:
    """Earliest date to fetch, per registry series: for each delivered id, its
    last delivered date minus that key's own look-back (weekly codes look back
    further than daily ones). An id never delivered pulls the series back to its
    backfill horizon; so does an id whose recorded horizon (`backfilled_from`)
    is shallower than the registry's `backfill` now asks for -- deepening, once:
    the record is updated after a successful fetch (see record_backfill). The
    record, not first_obs, is compared, so a source whose history starts later
    than asked (DFII10: 2003) is not re-fetched every run. The series takes the
    earliest of its ids. Being per series, one series' backfill never re-pulls
    another series' history."""
    out: dict[str, date] = {}
    for s in selected:
        horizon = backfill_start(s, today, backfill)
        starts = []
        for k, sid in _ids(s, st) or [(None, None)]:
            meta = st.series.get(sid, {}) if sid else {}
            last = meta.get("last_obs")
            if last is None or horizon < recorded_horizon(meta, today, backfill):
                starts.append(horizon)
            else:
                starts.append(date.fromisoformat(last) - timedelta(days=lookback_days(s, k)))
        out[s["id"]] = min(starts)
    return out


def record_backfill(selected: list[dict], st: state_mod.State, since: dict[str, date],
                    results: list, today: date, backfill: int = BACKFILL_DAYS) -> None:
    """After a run: every id of a series fetched from its registry horizon, by a
    source whose status is `ok`, records that horizon. An outage, error or empty
    result records nothing, so a deepening run is retried next time."""
    ok = {r.source for r in results if r.status == "ok"}
    for s in selected:
        if s["source"] in ok and since[s["id"]] <= backfill_start(s, today, backfill):
            rec = backfill_record(s, today, backfill)
            for _, sid in _ids(s, st):
                if sid in st.series:
                    st.series[sid]["backfilled_from"] = rec


def run(reg: Registry, data_dir: Path, uses: str, http: Http | None = None,
        now: datetime | None = None, collector=collect, cal_collector=calendar.collect) -> dict:
    """One pass. Use case D (the event calendar) goes to a `cal-` bundle; every
    other use case goes to the `macro-` bundle."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    http = http or Http()
    macro_uses = uses.replace("D", "")
    st = state_mod.load(data_dir)
    selected = reg.select(macro_uses) if macro_uses else []
    since = since_by_series(selected, st, today)
    results = collector(reg, macro_uses, http, since, today) if macro_uses else []

    rows = assign_revisions(results, st.delivered)
    ids_by_source: dict[str, set[str]] = {}
    for s in selected:
        ids_by_source.setdefault(s["source"], set()).update(freshness.expected_ids(s))
    st.apply(rows, results, ids_by_source, now.isoformat(timespec="seconds"))
    record_backfill(selected, st, since, results, today)
    fresh = freshness.summary(selected, st, today)

    cal_results, cal_rows, cal_bundle = [], [], None
    if "D" in uses:
        cal_results = cal_collector(reg, http, today)
        delivered_events = calendar.load_delivered(data_dir)
        cal_rows = calendar.new_rows(cal_results, delivered_events)
        fresh["calendar_ahead"] = calendar.calendar_ahead(cal_results, today)

    bundle = None
    if rows:
        bundle = write_macro_bundle(data_dir / "bundles", now, results, rows=rows, freshness=fresh)
    if cal_rows:
        cal_bundle = calendar.write_cal_bundle(data_dir / "bundles", now, cal_results, cal_rows,
                                               fresh["calendar_ahead"])
        calendar.save_delivered(delivered_events | {r["event_id"] for r in cal_rows}, data_dir)
    state_mod.save(st, data_dir)

    status = {
        "run_id": run_id_for(now),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "uses": uses,
        "bundle": bundle.name if bundle else None,
        "rows_delivered": len(rows),
        "revisions_delivered": sum(1 for r in rows if r["revision"] > 0),
        "cal_bundle": cal_bundle.name if cal_bundle else None,
        "events_delivered": len(cal_rows),
        "sources": {r.source: {"status": r.status, "reason": r.reason,
                               "observations": len(r.observations),
                               "since": min((since[s["id"]] for s in selected if s["source"] == r.source),
                                            default=today).isoformat()}
                    for r in results},
        "calendar_sources": {r.source: _cal_source_status(r) for r in cal_results},
        "freshness": fresh,
    }
    (data_dir / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    return status


def _cal_source_status(r: calendar.CalResult) -> dict:
    out = {"status": r.status, "reason": r.reason, "events": len(r.events), "furthest_event": r.furthest}
    if r.status == "ok":  # only a successful fetch may say which events are (no longer) listed
        out.update(event_ids_in_window=r.ids_in_window, window_from=r.window_from,
                   window_to=r.window_to)
    return out
