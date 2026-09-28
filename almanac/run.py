"""Run the fetchers for the selected series and collect per-source results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

from .http import Http
from .model import FetchError, Observation, SourceResult
from .registry import Registry


@dataclass(frozen=True)
class Ctx:
    http: Http
    source: str
    src: dict  # the [sources.<name>] table
    series: list[dict]  # active registry entries for this source
    since: date  # earliest observation date wanted
    today: date


Fetcher = Callable[[Ctx], list[Observation]]


def fetchers() -> dict[str, Fetcher]:
    from .sources import boj, ecb, fred, mas, nyfed, rba, snb, treasury
    return {"fred": fred.fetch, "treasury": treasury.fetch, "nyfed": nyfed.fetch,
            "ecb": ecb.fetch, "snb": snb.fetch, "boj": boj.fetch, "rba": rba.fetch,
            "mas": mas.fetch}


def collect(reg: Registry, uses: str, http: Http, since: date,
            today: date | None = None, table: dict[str, Fetcher] | None = None) -> list[SourceResult]:
    today = today or datetime.now(timezone.utc).date()
    table = fetchers() if table is None else table
    results = []
    for source, series in sorted(reg.by_source(reg.select(uses)).items()):
        fn = table.get(source)
        if fn is None:
            results.append(SourceResult(source, "error", "no fetcher for this source yet", _now()))
            continue
        try:
            obs = [o for o in fn(Ctx(http, source, reg.sources[source], series, since, today))
                   if o.obs_date >= since.isoformat()]
        except FetchError as e:
            results.append(SourceResult(source, e.kind, e.reason, _now()))
            continue
        except Exception as e:  # a parser bug or layout change: record, never "up to date"
            results.append(SourceResult(source, "error", f"{type(e).__name__}: {e}"[:300], _now()))
            continue
        status = "ok" if obs else "empty"
        reason = "" if obs else f"no observations since {since.isoformat()}"
        results.append(SourceResult(source, status, reason, _now(), obs))
    return results


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
