"""Partitioned delivery state: one parquet per series, only changed series rewritten."""

import pyarrow as pa
import pyarrow.parquet as pq

from almanac import state as state_mod
from almanac.model import SourceResult

RUN = "2026-09-28T22:30:00+00:00"


def rows(sid, *pairs, rev=0):
    return [{"series_id": sid, "obs_date": d, "value": v, "revision": rev} for d, v in pairs]


def test_one_file_per_series_and_roundtrip(tmp_path):
    st = state_mod.State()
    st.apply(rows("A", ("2026-09-25", 1.0)) + rows("B", ("2026-09-25", 2.0)), [], {}, RUN)
    state_mod.save(st, tmp_path)
    files = sorted(p.name for p in (tmp_path / "state" / "delivered").iterdir())
    assert files == ["A.parquet", "B.parquet"]
    back = state_mod.load(tmp_path)
    assert back.delivered == {("A", "2026-09-25"): (1.0, 0), ("B", "2026-09-25"): (2.0, 0)}
    assert not (tmp_path / "state" / "delivered.parquet").exists()


def test_only_changed_series_are_rewritten(tmp_path):
    st = state_mod.State()
    st.apply(rows("A", ("2026-09-25", 1.0)) + rows("B", ("2026-09-25", 2.0)), [], {}, RUN)
    state_mod.save(st, tmp_path)
    part = tmp_path / "state" / "delivered"
    before = {p.name: p.stat().st_mtime_ns for p in part.iterdir()}
    b_bytes = (part / "B.parquet").read_bytes()

    st2 = state_mod.load(tmp_path)
    st2.apply(rows("A", ("2026-09-26", 1.1)), [SourceResult("s", "ok")], {"s": {"A", "B"}}, RUN)
    assert st2.dirty == {"A"}
    (part / "B.parquet").write_bytes(b"sentinel")  # would be overwritten if B were rewritten
    state_mod.save(st2, tmp_path)
    assert (part / "B.parquet").read_bytes() == b"sentinel"
    (part / "B.parquet").write_bytes(b_bytes)
    assert (part / "A.parquet").stat().st_mtime_ns >= before["A.parquet"]
    back = state_mod.load(tmp_path)
    assert back.delivered[("A", "2026-09-26")] == (1.1, 0) and back.delivered[("B", "2026-09-25")] == (2.0, 0)


def test_no_new_rows_rewrites_no_partition(tmp_path):
    st = state_mod.State()
    st.apply(rows("A", ("2026-09-25", 1.0)), [], {}, RUN)
    state_mod.save(st, tmp_path)
    (tmp_path / "state" / "delivered" / "A.parquet").write_bytes(b"sentinel")
    st2 = state_mod.State(delivered={("A", "2026-09-25"): (1.0, 0)})
    st2.apply([], [SourceResult("s", "outage", "down")], {"s": {"A"}}, RUN)
    state_mod.save(st2, tmp_path)
    assert (tmp_path / "state" / "delivered" / "A.parquet").read_bytes() == b"sentinel"


def test_legacy_single_file_is_read_then_migrated(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    legacy = rows("A", ("2026-09-24", 1.0), ("2026-09-25", 1.5), rev=0) + rows("B", ("2026-09-25", 2.0), rev=1)
    pq.write_table(pa.Table.from_pylist(legacy, schema=state_mod.DELIVERED_SCHEMA), d / "delivered.parquet")

    st = state_mod.load(tmp_path)
    assert st.delivered[("B", "2026-09-25")] == (2.0, 1) and st.migrate
    state_mod.save(st, tmp_path)  # nothing new delivered, but migration writes every series once
    assert not (d / "delivered.parquet").exists()
    assert sorted(p.name for p in (d / "delivered").iterdir()) == ["A.parquet", "B.parquet"]
    assert state_mod.load(tmp_path).delivered == st.delivered


def test_partitions_win_over_a_leftover_legacy_file(tmp_path):
    d = tmp_path / "state"
    (d / "delivered").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows("A", ("2026-09-25", 1.0)), schema=state_mod.DELIVERED_SCHEMA),
                   d / "delivered.parquet")
    pq.write_table(pa.Table.from_pylist(rows("A", ("2026-09-25", 1.2), rev=1), schema=state_mod.DELIVERED_SCHEMA),
                   d / "delivered" / "A.parquet")
    assert state_mod.load(tmp_path).delivered[("A", "2026-09-25")] == (1.2, 1)


def test_any_series_id_is_a_safe_file_name(tmp_path):
    p = state_mod.partition_path(tmp_path, "TRADE_CENSUS_EXP_-/x y")
    assert p.parent == tmp_path / "delivered" and "/" not in p.name and " " not in p.name
