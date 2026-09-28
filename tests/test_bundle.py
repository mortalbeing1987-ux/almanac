import hashlib
import json
from datetime import datetime, timezone

import pyarrow.parquet as pq
import pytest

from almanac.bundle import SCHEMA, assign_revisions, write_macro_bundle
from almanac.model import Observation, SourceResult

T0 = datetime(2026, 9, 28, 21, 5, 0, tzinfo=timezone.utc)


def res(source, obs, status="ok"):
    return SourceResult(source, status, fetched_at="2026-09-28T21:05:10+00:00",
                        observations=[Observation(*o, source) for o in obs])


def test_revisions_new_changed_unchanged():
    results = [res("s", [("A", "2026-09-25", 1.0), ("A", "2026-09-26", 2.5), ("A", "2026-09-27", 3.0)])]
    delivered = {("A", "2026-09-25"): (1.0, 0), ("A", "2026-09-26", ): (2.0, 1)}
    rows = assign_revisions(results, delivered)
    assert [(r["obs_date"], r["revision"]) for r in rows] == [("2026-09-26", 2), ("2026-09-27", 0)]


def test_bundle_layout_manifest_and_checksum(tmp_path):
    results = [res("fred", [("CMT_DGS10", "2026-09-25", 4.1)]),
               res("boj", [], status="outage")]
    results[1].reason = "HTTP 503 from x after 5 attempts"
    path = write_macro_bundle(tmp_path, T0, results)
    assert path.name == "macro-20260928T210500Z"
    assert not (tmp_path / "macro-20260928T210500Z.partial").exists()

    m = json.loads((path / "manifest.json").read_text())
    assert m["contract_version"] == 1 and m["kind"] == "macro"
    assert m["run_id"] == "20260928T210500Z"
    f = m["files"][0]
    assert f["name"] == "observations.parquet" and f["rows"] == 1
    assert f["sha256"] == hashlib.sha256((path / f["name"]).read_bytes()).hexdigest()
    assert m["sources"]["boj"]["status"] == "outage"
    assert m["sources"]["fred"]["observations"] == 1

    table = pq.read_table(path / "observations.parquet")
    assert table.schema.equals(SCHEMA)
    assert table.to_pylist()[0]["revision"] == 0


def test_never_overwrites_an_existing_bundle(tmp_path):
    write_macro_bundle(tmp_path, T0, [])
    with pytest.raises(FileExistsError):
        write_macro_bundle(tmp_path, T0, [])
