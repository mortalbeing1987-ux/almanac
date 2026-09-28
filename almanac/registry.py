"""Read almanac/series.toml -- the single list of what is collected."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .model import FetchError

REGISTRY_PATH = Path(__file__).resolve().parent / "series.toml"


@dataclass(frozen=True)
class Registry:
    sources: dict
    series: list[dict]

    def select(self, uses: str = "ABCDEFG", *, role: str = "deliver") -> list[dict]:
        """Active series for the given use-case tags and role."""
        return [s for s in self.series
                if s["status"] == "active" and s["use"] in uses
                and s.get("role", "deliver") == role]

    def by_source(self, series: list[dict]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for s in series:
            out.setdefault(s["source"], []).append(s)
        return out


def load(path: Path = REGISTRY_PATH) -> Registry:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return Registry(sources=raw["sources"], series=raw["series"])


def keys(series: dict) -> list[str]:
    k = series["key"]
    return list(k) if isinstance(k, list) else [k]


def delivered_id(series: dict, key: str) -> str:
    """Registry id for single-key series; `<id>_<key>` when a series lists keys."""
    return f"{series['id']}_{key}" if isinstance(series["key"], list) else series["id"]


PER_KEY_FIELDS = ("freq", "max_age_days", "lookback_days")


def key_settings(series: dict, key: str | None) -> dict:
    """freq / max_age_days / lookback_days for one key of a series: the series'
    own values, overridden by `per_key.<key>` where the registry sets them (e.g.
    a weekly code inside an otherwise daily series)."""
    out = {f: series[f] for f in PER_KEY_FIELDS if f in series}
    if key is not None:
        out.update({f: v for f, v in series.get("per_key", {}).get(key, {}).items()
                    if f in PER_KEY_FIELDS})
    return out


def key_of(series: dict, delivered: str) -> str | None:
    """The registry key behind a delivered id (`<id>_<key>`), or None."""
    if isinstance(series["key"], list) and delivered.startswith(series["id"] + "_"):
        return delivered[len(series["id"]) + 1:]
    return None


def auth(src: dict) -> tuple[dict[str, str], dict[str, str]]:
    """(headers, query params) carrying a source's API key from its Actions secret.

    The key only goes into the request; it is never logged or returned elsewhere.
    """
    secret = src.get("secret")
    if not secret:
        return {}, {}
    key = os.environ.get(secret, "")
    if not key:
        raise FetchError("error", f"secret {secret} is not set")
    if "auth_param" in src:
        return {}, {src["auth_param"]: key}
    return {src["auth_header"]: key}, {}
