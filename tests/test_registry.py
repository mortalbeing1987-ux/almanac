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
        assert s["use"] in "ABCDEFG"
        assert s["status"] in ("active", "tbd")
        assert s["redistribution"] in ("open", "restricted")


def test_active_series_have_real_sources():
    for s in REG["series"]:
        if s["status"] == "active":
            assert REG["sources"][s["source"]]["url"] != "TBD", s["id"]
            assert s["key"] != "TBD", s["id"]


def test_keyed_sources_name_their_secret_and_header():
    for name, src in REG["sources"].items():
        if "secret" in src:
            assert src["secret"].isupper() and src["secret"].endswith("_API_KEY"), name
            assert src.get("auth_header") or src.get("auth_param"), name


def test_no_source_key_listed_twice():
    seen = set()
    for s in REG["series"]:
        keys = s["key"] if isinstance(s["key"], list) else [s["key"]]
        for k in keys:
            ident = (s["source"], s.get("file"), s.get("column"), k)
            assert ident not in seen, (s["id"], k)
            seen.add(ident)


def test_roles_and_release_files():
    for s in REG["series"]:
        assert s.get("role", "deliver") in ("deliver", "crosscheck"), s["id"]
        if "file" in s:
            src = REG["sources"][s["source"]]
            assert s["file"] in src.get("files", {}), s["id"]
            assert s.get("expect"), s["id"]
