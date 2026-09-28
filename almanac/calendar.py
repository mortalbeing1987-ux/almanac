"""Event calendar (use case D): central-bank decisions and US release dates.

Each calendar source's parser returns the event DATES it finds on the official
page. Everything else about an event -- country, kind, name and the published
standard announcement time -- comes from the source's registry entry, so a new
calendar is one registry entry plus one parser.

Delivery is insert-only by event_id (`<source>:<kind>:<date>`): an event is
delivered the first time its id is seen. When an institution moves a date the
new date has a new id and is delivered as a new row; nothing earlier is
rewritten. Delivered ids are kept in the private data repo (state/events.json).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from .bundle import CONTRACT_VERSION, run_id_for
from .http import Http
from .model import FetchError
from .registry import Registry
from .run import Ctx

AHEAD_DAYS = 60  # horizon, and the calendar_ahead threshold
BEHIND_DAYS = 30  # recent past kept in view so moved dates are caught

SCHEMA = pa.schema([
    ("event_id", pa.string()),
    ("event_date", pa.string()),
    ("event_time_utc", pa.string()),  # nullable: HH:MM when a standard time is published
    ("country", pa.string()),
    ("kind", pa.string()),
    ("name", pa.string()),
    ("source", pa.string()),
    ("fetched_at", pa.string()),
])

CalFetcher = Callable[[Ctx], list[date]]


@dataclass(frozen=True)
class Event:
    event_date: str
    event_time_utc: str | None
    country: str
    kind: str
    name: str
    source: str

    @property
    def event_id(self) -> str:
        return f"{self.source}:{self.kind}:{self.event_date}"


@dataclass
class CalResult:
    source: str
    status: str  # ok | empty | outage | error
    reason: str = ""
    fetched_at: str = ""
    events: list[Event] = field(default_factory=list)
    furthest: str | None = None  # furthest event date on the page (any horizon)


def fetchers() -> dict[str, CalFetcher]:
    from .sources import cal_boj, cal_ecb, cal_fomc, cal_rba
    return {"fed_fomc": cal_fomc.fetch, "ecb_cal": cal_ecb.fetch,
            "boj_cal": cal_boj.fetch, "rba_cal": cal_rba.fetch}


def time_utc(series: dict, day: date) -> str | None:
    """The source's published standard announcement time, converted to UTC for that day."""
    if not series.get("time_local"):
        return None
    hh, mm = (int(x) for x in series["time_local"].split(":"))
    local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo(series["tz"]))
    return local.astimezone(timezone.utc).strftime("%H:%M")


def collect(reg: Registry, http: Http, today: date,
            table: dict[str, CalFetcher] | None = None) -> list[CalResult]:
    table = fetchers() if table is None else table
    lo, hi = today - timedelta(days=BEHIND_DAYS), today + timedelta(days=AHEAD_DAYS)
    results = []
    for s in reg.select("D"):
        src, now = s["source"], _now()
        fn = table.get(src)
        if fn is None:
            results.append(CalResult(src, "error", "no calendar parser for this source yet", now))
            continue
        try:
            days = sorted(set(fn(Ctx(http, src, reg.sources[src], [s], lo, today))))
        except FetchError as e:
            results.append(CalResult(src, e.kind, e.reason, _now()))
            continue
        except Exception as e:  # layout change or parser bug: an error, never "up to date"
            results.append(CalResult(src, "error", f"{type(e).__name__}: {e}"[:300], _now()))
            continue
        events = [Event(d.isoformat(), time_utc(s, d), s["country"], s["kind"], s["name"], src)
                  for d in days if lo <= d <= hi]
        furthest = days[-1].isoformat() if days else None
        status = "ok" if events else "empty"
        reason = "" if events else f"no events between {lo} and {hi}"
        results.append(CalResult(src, status, reason, _now(), events, furthest))
    return results


def calendar_ahead(results: list[CalResult], today: date) -> dict:
    """Per source: furthest event found and whether it reaches the 60-day horizon."""
    target = today + timedelta(days=AHEAD_DAYS)
    out = {}
    for r in results:
        days = (date.fromisoformat(r.furthest) - today).days if r.furthest else None
        out[r.source] = {"furthest_event": r.furthest, "days_ahead": days,
                         "ok": bool(r.furthest and date.fromisoformat(r.furthest) >= target),
                         "status": r.status}
    return {"horizon_days": AHEAD_DAYS, "sources": out,
            "short": sorted(k for k, v in out.items() if not v["ok"])}


# ---- delivery ----------------------------------------------------------------

def load_delivered(data_dir: Path) -> set[str]:
    p = data_dir / "state" / "events.json"
    return set(json.loads(p.read_text())) if p.exists() else set()


def save_delivered(ids: set[str], data_dir: Path) -> None:
    p = data_dir / "state" / "events.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(ids), indent=0) + "\n")


def new_rows(results: list[CalResult], delivered: set[str]) -> list[dict]:
    rows, seen = [], set()
    for r in results:
        for e in r.events:
            if e.event_id in delivered or e.event_id in seen:
                continue
            seen.add(e.event_id)
            rows.append({"event_id": e.event_id, "event_date": e.event_date,
                         "event_time_utc": e.event_time_utc, "country": e.country,
                         "kind": e.kind, "name": e.name, "source": e.source,
                         "fetched_at": r.fetched_at})
    rows.sort(key=lambda r: (r["event_date"], r["event_id"]))
    return rows


def write_cal_bundle(out_dir: Path, started: datetime, results: list[CalResult],
                     rows: list[dict], ahead: dict) -> Path:
    """`cal-<run_id>/events.parquet`, then manifest.json last; assembled in
    `.partial` and renamed so a reader never sees a half-written bundle."""
    run_id = run_id_for(started)
    final, work = out_dir / f"cal-{run_id}", out_dir / f"cal-{run_id}.partial"
    if final.exists():
        raise FileExistsError(final)
    work.mkdir(parents=True, exist_ok=False)
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    data = work / "events.parquet"
    pq.write_table(table, data)
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "kind": "cal",
        "run_id": run_id,
        "started_at": started.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "finished_at": _now(),
        "files": [{"name": data.name, "sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                   "rows": table.num_rows}],
        "sources": {r.source: {"status": r.status, "reason": r.reason, "fetched_at": r.fetched_at,
                               "events": len(r.events)}
                    for r in sorted(results, key=lambda r: r.source)},
        "freshness": {"calendar_ahead": ahead},
    }
    (work / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    work.rename(final)
    return final


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
