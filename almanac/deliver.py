"""One scheduled delivery pass into a checked-out PRIVATE data repo.

    <data-dir>/bundles/macro-<run_id>/   new bundle (only when there are new rows)
    <data-dir>/state/                    delivery state (see state.py)
    <data-dir>/status.json               freshness + per-source outcome of this run

Committing and pushing the data repo is left to the workflow.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import freshness, state as state_mod
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
        now: datetime | None = None, collector=collect) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    st = state_mod.load(data_dir)
    selected = reg.select(uses)
    since = since_by_source(selected, st, today)
    results = collector(reg, uses, http or Http(), since, today)

    rows = assign_revisions(results, st.delivered)
    ids_by_source: dict[str, set[str]] = {}
    for s in selected:
        ids_by_source.setdefault(s["source"], set()).update(freshness.expected_ids(s))
    st.apply(rows, results, ids_by_source, now.isoformat(timespec="seconds"))
    fresh = freshness.summary(selected, st, today)

    bundle = None
    if rows:
        bundle = write_macro_bundle(data_dir / "bundles", now, results, rows=rows, freshness=fresh)
    state_mod.save(st, data_dir)

    status = {
        "run_id": run_id_for(now),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "uses": uses,
        "bundle": bundle.name if bundle else None,
        "rows_delivered": len(rows),
        "sources": {r.source: {"status": r.status, "reason": r.reason,
                               "observations": len(r.observations),
                               "since": since.get(r.source, today).isoformat()}
                    for r in results},
        "freshness": fresh,
    }
    (data_dir / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    return status
