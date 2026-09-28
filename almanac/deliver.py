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
from .registry import Registry, delivered_id, keys
from .run import collect

# Re-fetch window, by frequency, so revisions to already-delivered values are
# caught: daily rates settle within weeks, but monthly and quarterly data get
# revised much further back (payroll benchmarks, annual trade revisions).
# A series can override with `lookback_days` in the registry.
LOOKBACK_DAYS = {"daily": 30, "weekly": 90, "monthly": 400, "quarterly": 800}
DEFAULT_LOOKBACK = 30  # frequencies not listed above (e.g. "mixed")
BACKFILL_DAYS = 365  # first delivery of a series goes this far back


def lookback_days(series: dict) -> int:
    return int(series.get("lookback_days", LOOKBACK_DAYS.get(series["freq"], DEFAULT_LOOKBACK)))


def since_by_source(selected: list[dict], st: state_mod.State, today: date,
                    backfill: int = BACKFILL_DAYS) -> dict[str, date]:
    """Earliest date to fetch per source: for each series, its oldest 'last
    delivered' minus its own look-back; a series never delivered pulls the
    source back to the backfill horizon. A source takes the earliest of its series."""
    out: dict[str, date] = {}
    for s in selected:
        lasts = [st.last_obs(delivered_id(s, k)) for k in keys(s)]
        if s["source"] == "treasury":  # tenor ids aren't known up front
            lasts = [m.get("last_obs") for sid, m in st.series.items()
                     if sid.startswith(s["id"] + "_")] or [None]
        if any(v is None for v in lasts):
            start = today - timedelta(days=backfill)
        else:
            start = date.fromisoformat(min(lasts)) - timedelta(days=lookback_days(s))
        out[s["source"]] = min(out.get(s["source"], start), start)
    return out


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
    since = since_by_source(selected, st, today)
    results = collector(reg, macro_uses, http, since, today) if macro_uses else []

    rows = assign_revisions(results, st.delivered)
    ids_by_source: dict[str, set[str]] = {}
    for s in selected:
        ids_by_source.setdefault(s["source"], set()).update(freshness.expected_ids(s))
    st.apply(rows, results, ids_by_source, now.isoformat(timespec="seconds"))
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
        "cal_bundle": cal_bundle.name if cal_bundle else None,
        "events_delivered": len(cal_rows),
        "sources": {r.source: {"status": r.status, "reason": r.reason,
                               "observations": len(r.observations),
                               "since": since.get(r.source, today).isoformat()}
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
