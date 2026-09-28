"""Delivery state, kept in the PRIVATE data repo next to the bundles.

state/delivered.parquet  latest delivered value and revision per (series_id, obs_date)
state/series.json        per series: first/last obs delivered, last attempt/success, last status/error
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .bundle import Delivered
from .model import SourceResult

DELIVERED_SCHEMA = pa.schema([("series_id", pa.string()), ("obs_date", pa.string()),
                              ("value", pa.float64()), ("revision", pa.int64())])


@dataclass
class State:
    delivered: Delivered = field(default_factory=dict)
    series: dict[str, dict] = field(default_factory=dict)

    def last_obs(self, series_id: str) -> str | None:
        return self.series.get(series_id, {}).get("last_obs")

    def apply(self, rows: list[dict], results: list[SourceResult], ids_by_source: dict[str, set[str]],
              run_at: str) -> None:
        """Record delivered rows, then each source's outcome on its series."""
        for r in rows:
            self.delivered[(r["series_id"], r["obs_date"])] = (r["value"], r["revision"])
            meta = self.series.setdefault(r["series_id"], {})
            meta["first_obs"] = min(filter(None, [meta.get("first_obs"), r["obs_date"]]))
            meta["last_obs"] = max(filter(None, [meta.get("last_obs"), r["obs_date"]]))
        for res in results:
            seen = {o.series_id for o in res.observations}
            for sid in ids_by_source.get(res.source, set()) | seen:
                meta = self.series.setdefault(sid, {})
                meta["source"] = res.source
                meta["last_attempt"] = run_at
                meta["last_status"] = res.status
                meta["last_error"] = res.reason if res.status != "ok" else ""
                if res.status == "ok" and sid in seen:
                    meta["last_success"] = run_at


def load(data_dir: Path) -> State:
    d = data_dir / "state"
    delivered: Delivered = {}
    if (d / "delivered.parquet").exists():
        for r in pq.read_table(d / "delivered.parquet").to_pylist():
            delivered[(r["series_id"], r["obs_date"])] = (r["value"], r["revision"])
    series = json.loads((d / "series.json").read_text()) if (d / "series.json").exists() else {}
    return State(delivered, series)


def save(state: State, data_dir: Path) -> None:
    d = data_dir / "state"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"series_id": s, "obs_date": o, "value": v, "revision": rev}
            for (s, o), (v, rev) in sorted(state.delivered.items())]
    pq.write_table(pa.Table.from_pylist(rows, schema=DELIVERED_SCHEMA), d / "delivered.parquet")
    (d / "series.json").write_text(json.dumps(state.series, indent=2, sort_keys=True) + "\n")
