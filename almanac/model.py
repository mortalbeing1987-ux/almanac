"""Tidy rows and per-source outcomes shared by fetchers and the bundle writer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Observation:
    series_id: str  # delivered id (registry id, suffixed per key where needed)
    obs_date: str  # YYYY-MM-DD (period start for monthly/weekly/quarterly)
    value: float  # in the registry's units
    source: str  # registry source name


class FetchError(Exception):
    """A fetch that produced no usable data.

    kind "outage" = transport trouble worth retrying (timeouts, 429/5xx);
    kind "error"  = the source answered but not as expected (layout change,
    4xx, unparseable body). Both are retryable; neither means "up to date".
    """

    def __init__(self, kind: str, reason: str):
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


@dataclass
class SourceResult:
    source: str
    status: str  # ok | empty | outage | error
    reason: str = ""
    fetched_at: str = ""  # UTC ISO-8601, when the fetch finished
    observations: list[Observation] = field(default_factory=list)
