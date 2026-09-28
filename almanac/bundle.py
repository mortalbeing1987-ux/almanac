"""Write a `macro-<run_id>/` bundle: observations.parquet, then manifest.json last.

The bundle is assembled in `<name>.partial/` and renamed into place only after
the manifest is written, so a reader never sees a half-written bundle (and an
importer skips any folder without a manifest).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .model import SourceResult

CONTRACT_VERSION = 1
SCHEMA = pa.schema([
    ("series_id", pa.string()),
    ("obs_date", pa.string()),
    ("value", pa.float64()),
    ("source", pa.string()),
    ("fetched_at", pa.string()),
    ("revision", pa.int64()),
])

# Delivered state: (series_id, obs_date) -> (last delivered value, its revision)
Delivered = dict[tuple[str, str], tuple[float, int]]


def run_id_for(started: datetime) -> str:
    return started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _same(a: float, b: float) -> bool:
    return round(a, 10) == round(b, 10)


def assign_revisions(results: list[SourceResult], delivered: Delivered) -> list[dict]:
    """Rows to deliver: new (series, date) pairs as revision 0, changed values as
    revision n+1, unchanged values dropped (insert-only, never rewritten)."""
    rows = []
    for res in results:
        for o in res.observations:
            prior = delivered.get((o.series_id, o.obs_date))
            if prior is None:
                rev = 0
            elif _same(prior[0], o.value):
                continue
            else:
                rev = prior[1] + 1
            rows.append({"series_id": o.series_id, "obs_date": o.obs_date,
                         "value": float(o.value), "source": o.source,
                         "fetched_at": res.fetched_at, "revision": rev})
    rows.sort(key=lambda r: (r["series_id"], r["obs_date"]))
    return rows


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_macro_bundle(out_dir: Path, started: datetime, results: list[SourceResult],
                       delivered: Delivered | None = None,
                       finished: datetime | None = None) -> Path:
    run_id = run_id_for(started)
    final = out_dir / f"macro-{run_id}"
    work = out_dir / f"macro-{run_id}.partial"
    if final.exists():
        raise FileExistsError(final)
    work.mkdir(parents=True, exist_ok=False)

    rows = assign_revisions(results, delivered or {})
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    data = work / "observations.parquet"
    pq.write_table(table, data)

    manifest = {
        "contract_version": CONTRACT_VERSION,
        "kind": "macro",
        "run_id": run_id,
        "started_at": started.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "finished_at": (finished or datetime.now(timezone.utc)).astimezone(timezone.utc)
                       .isoformat(timespec="seconds"),
        "files": [{"name": data.name, "sha256": _sha256(data), "rows": table.num_rows}],
        "sources": {r.source: {"status": r.status, "reason": r.reason,
                               "fetched_at": r.fetched_at,
                               "observations": len(r.observations)}
                    for r in sorted(results, key=lambda r: r.source)},
        "freshness": None,  # step 2
    }
    (work / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    work.rename(final)
    return final
