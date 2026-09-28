"""Delivery state, kept in the PRIVATE data repo next to the bundles.

state/delivered/<series_id>.parquet  latest delivered value and revision per obs_date,
                                     one file per series: a run rewrites only the
                                     series that received new rows
state/series.json                    per series: first/last obs delivered, last
                                     attempt/success, last status/error

Older data repos kept everything in one state/delivered.parquet. It is still
read; the first save migrates it into per-series files and removes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import pyarrow as pa
import pyarrow.parquet as pq

from .bundle import Delivered
from .model import SourceResult

DELIVERED_SCHEMA = pa.schema([("series_id", pa.string()), ("obs_date", pa.string()),
                              ("value", pa.float64()), ("revision", pa.int64())])
LEGACY_FILE = "delivered.parquet"
PARTITION_DIR = "delivered"


@dataclass
class State:
    delivered: Delivered = field(default_factory=dict)
    series: dict[str, dict] = field(default_factory=dict)
    dirty: set[str] = field(default_factory=set)  # series ids whose delivered rows changed
    migrate: bool = False  # loaded from the legacy single file

    def last_obs(self, series_id: str) -> str | None:
        return self.series.get(series_id, {}).get("last_obs")

    def apply(self, rows: list[dict], results: list[SourceResult], ids_by_source: dict[str, set[str]],
              run_at: str) -> None:
        """Record delivered rows, then each source's outcome on its series."""
        for r in rows:
            self.delivered[(r["series_id"], r["obs_date"])] = (r["value"], r["revision"])
            self.dirty.add(r["series_id"])
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


def partition_path(state_dir: Path, series_id: str) -> Path:
    """File for one series; ids are percent-encoded so any id is a safe file name."""
    return state_dir / PARTITION_DIR / f"{quote(series_id, safe='')}.parquet"


def _read(path: Path, delivered: Delivered) -> None:
    for r in pq.read_table(path).to_pylist():
        delivered[(r["series_id"], r["obs_date"])] = (r["value"], r["revision"])


def load(data_dir: Path) -> State:
    d = data_dir / "state"
    delivered: Delivered = {}
    legacy = d / LEGACY_FILE
    if legacy.exists():
        _read(legacy, delivered)
    for part in sorted((d / PARTITION_DIR).glob("*.parquet")):
        _read(part, delivered)  # per-series files win over the legacy file
    series = json.loads((d / "series.json").read_text()) if (d / "series.json").exists() else {}
    return State(delivered, series, migrate=legacy.exists())


def save(state: State, data_dir: Path) -> None:
    d = data_dir / "state"
    (d / PARTITION_DIR).mkdir(parents=True, exist_ok=True)
    by_series: dict[str, list[dict]] = {}
    write = {s for s, _ in state.delivered} if state.migrate else state.dirty
    for (s, o), (v, rev) in sorted(state.delivered.items()):
        if s in write:
            by_series.setdefault(s, []).append({"series_id": s, "obs_date": o, "value": v, "revision": rev})
    for s, rows in by_series.items():
        pq.write_table(pa.Table.from_pylist(rows, schema=DELIVERED_SCHEMA), partition_path(d, s))
    if state.migrate:
        (d / LEGACY_FILE).unlink(missing_ok=True)
        state.migrate = False
    state.dirty.clear()
    (d / "series.json").write_text(json.dumps(state.series, indent=2, sort_keys=True) + "\n")
