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


def collect(reg: Registry, uses: str, http: Http, since: date | dict[str, date],
            today: date | None = None, table: dict[str, Fetcher] | None = None) -> list[SourceResult]:
    """Fetch every active series of the selected use cases, source by source.

    Canary: a source's first series is fetched on its own first. If that fails
    the source is recorded as outage/error and nothing is written for it. If a
    later series fails, the canary's rows are kept and the source is `error`.
    `since` is one date or a per-source mapping.
    """
    today = today or datetime.now(timezone.utc).date()
    table = fetchers() if table is None else table
    results = []
    for source, series in sorted(reg.by_source(reg.select(uses)).items()):
        start = since[source] if isinstance(since, dict) else since
        fn = table.get(source)
        if fn is None:
            results.append(SourceResult(source, "error", "no fetcher for this source yet", _now()))
            continue
        obs: list[Observation] = []
        for i, batch in enumerate(([series[0]], series[1:])):
            if not batch:
                continue
            try:
                got = fn(Ctx(http, source, reg.sources[source], batch, start, today))
            except FetchError as e:
                kind, reason = e.kind, e.reason
            except Exception as e:  # a parser bug or layout change: record, never "up to date"
                kind, reason = "error", f"{type(e).__name__}: {e}"[:300]
            else:
                obs += [o for o in got if o.obs_date >= start.isoformat()]
                continue
            if i == 0:  # canary failed: write nothing for this source
                results.append(SourceResult(source, kind, f"canary {series[0]['id']}: {reason}", _now()))
            else:
                results.append(SourceResult(source, "error", f"after canary: {reason}", _now(), obs))
            break
        else:
            status = "ok" if obs else "empty"
            reason = "" if obs else f"no observations since {start.isoformat()}"
            results.append(SourceResult(source, status, reason, _now(), obs))
    return results


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
