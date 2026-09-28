"""The gap score: is each series behind its expected release schedule?

fresh  - latest delivered observation is within the series' max age
depth  - earliest delivered observation (backfill progress)
calendar_ahead - event-calendar coverage; arrives with step 4 (null until then)
"""

from __future__ import annotations

from datetime import date

from .registry import delivered_id, key_of, key_settings, keys

# Default max age (days between the latest observation and today) by frequency;
# a series can override with `max_age_days` in the registry.
MAX_AGE_DAYS = {"daily": 4, "weekly": 10, "monthly": 45, "quarterly": 120}


def expected_ids(series: dict) -> list[str]:
    """Delivered ids a series is expected to produce, where knowable up front."""
    if series["source"] == "treasury":  # tenor ids come from the file's columns
        return []
    if "areas" in series or "flows" in series or "file" in series:
        return []  # expanded per area/flow/table by their fetchers (later steps)
    return [delivered_id(series, k) for k in keys(series)]


def max_age(series: dict, key: str | None = None) -> int | None:
    """Per key: a weekly code inside a daily series gets the weekly default,
    and `max_age_days` (series-level or per_key) overrides for publication lag."""
    ks = key_settings(series, key)
    return ks.get("max_age_days", MAX_AGE_DAYS.get(ks.get("freq", "")))


def summary(selected: list[dict], state, today: date) -> dict:
    """Freshness for every delivered id of the selected series."""
    out: dict[str, dict] = {}
    for s in selected:
        if s["freq"] == "event":
            continue
        ids = set(expected_ids(s)) | {sid for sid, m in state.series.items()
                                       if m.get("source") == s["source"]
                                       and (sid == s["id"] or sid.startswith(s["id"] + "_"))}
        for sid in sorted(ids):
            meta = state.series.get(sid, {})
            limit = max_age(s, key_of(s, sid))
            last = meta.get("last_obs")
            age = (today - date.fromisoformat(last)).days if last else None
            out[sid] = {
                "registry_id": s["id"],
                "last_obs": last,
                "first_obs": meta.get("first_obs"),
                "age_days": age,
                "max_age_days": limit,
                "fresh": bool(last and limit is not None and age <= limit),
                "last_status": meta.get("last_status"),
                "last_error": meta.get("last_error", ""),
                "last_success": meta.get("last_success"),
            }
    stale = sorted(k for k, v in out.items() if not v["fresh"])
    return {"as_of": today.isoformat(), "series": out, "stale": stale,
            "calendar_ahead": None}
