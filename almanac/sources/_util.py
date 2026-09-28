"""Small helpers shared by fetchers."""

from __future__ import annotations

import csv
import io
from ..model import FetchError
from ..registry import auth


def url_for(ctx, key: str = "", **extra) -> str:
    year = ctx.today.year
    return ctx.src["url"].format(key=key, since=ctx.since.isoformat(), year=year,
                                 prev_year=year - 1,
                                 n=max((ctx.today - ctx.since).days + 1, 5), **extra)


def get(ctx, url: str) -> bytes:
    headers, params = auth(ctx.src)
    return ctx.http.get(url, headers=headers, params=params)


def text(body: bytes) -> str:
    return body.decode("utf-8-sig", "replace")


def rows(body: bytes, delimiter: str = ",") -> list[list[str]]:
    return list(csv.reader(io.StringIO(text(body)), delimiter=delimiter))


def num(s: str | None) -> float | None:
    """A number, or None for the sources' many spellings of 'missing'."""
    if s is None:
        return None
    s = s.strip()
    if s in ("", ".", "null", "NA", "n.a.", "N/A", "-"):
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        raise FetchError("error", f"unexpected value format {s[:20]!r}") from None


def require(cond: bool, what: str) -> None:
    """Layout check: a failed check is an error, never 'up to date'."""
    if not cond:
        raise FetchError("error", f"unexpected layout: {what}")
