import tomllib
from pathlib import Path

REG = tomllib.loads((Path(__file__).resolve().parents[1] / "almanac" / "series.toml").read_text(encoding="utf-8"))


def test_series_ids_unique():
    ids = [s["id"] for s in REG["series"]]
    assert len(ids) == len(set(ids))


def test_series_reference_known_sources():
    for s in REG["series"]:
        assert s["source"] in REG["sources"], s["id"]


def test_required_fields_and_values():
    for s in REG["series"]:
        for field in ("id", "source", "key", "freq", "units", "use", "status", "redistribution"):
            assert field in s, (s.get("id"), field)
        assert s["use"] in "ABCDEF"
        assert s["status"] in ("active", "tbd")
        assert s["redistribution"] in ("open", "restricted")


def test_active_series_have_real_sources():
    for s in REG["series"]:
        if s["status"] == "active":
            assert REG["sources"][s["source"]]["url"] != "TBD", s["id"]
            assert s["key"] != "TBD", s["id"]
