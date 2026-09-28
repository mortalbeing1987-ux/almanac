"""Polite HTTP: honest User-Agent, one request at a time per host, backoff on 429/5xx.

Secrets (API keys) are passed in `headers`/`params` and never appear in error
messages: errors name the host and path only, never the query string.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from . import USER_AGENT
from .model import FetchError

RETRY_STATUS = {429, 500, 502, 503, 504}


class Http:
    def __init__(self, *, timeout: float = 60, retries: int = 4, backoff: float = 2.0,
                 min_interval: float = 1.0,
                 opener: Callable = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self.min_interval = min_interval
        self._open, self._sleep, self._clock = opener, sleep, clock
        self._last: dict[str, float] = {}  # host -> time of last request

    def get(self, url: str, *, headers: dict[str, str] | None = None,
            params: dict[str, str] | None = None) -> bytes:
        parts = urllib.parse.urlsplit(url)
        where = f"{parts.netloc}{parts.path}"  # safe to log: no query, no key
        if params:
            url += ("&" if parts.query else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        last_reason = ""
        for attempt in range(self.retries + 1):
            self._pace(parts.netloc)
            try:
                with self._open(req, timeout=self.timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code not in RETRY_STATUS:
                    raise FetchError("error", f"HTTP {e.code} from {where}") from None
                last_reason = f"HTTP {e.code} from {where}"
                wait = _retry_after(e) or self.backoff * 2 ** attempt
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_reason = f"{type(e).__name__} from {where}"
                wait = self.backoff * 2 ** attempt
            if attempt < self.retries:
                self._sleep(wait)
        raise FetchError("outage", f"{last_reason} after {self.retries + 1} attempts")

    def _pace(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self._clock() - last
            if gap < self.min_interval:
                self._sleep(self.min_interval - gap)
        self._last[host] = self._clock()


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    value = e.headers.get("Retry-After") if e.headers else None
    try:
        return min(float(value), 300.0) if value else None
    except ValueError:
        return None
