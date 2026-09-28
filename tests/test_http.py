import io
import urllib.error

import pytest

from almanac import USER_AGENT
from almanac.http import Http
from almanac.model import FetchError


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://x.test/p?key=SECRET", code, "err", headers, None)


def make(responses, **kw):
    calls, sleeps = [], []

    def opener(req, timeout):
        calls.append(req)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return Resp(r)

    t = [0.0]
    http = Http(opener=opener, sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)),
                clock=lambda: t[0], **kw)
    return http, calls, sleeps


def test_sends_honest_user_agent_and_params():
    http, calls, _ = make([b"ok"])
    assert http.get("https://x.test/p?a=1", params={"key": "K"}) == b"ok"
    req = calls[0]
    assert req.get_header("User-agent") == USER_AGENT
    assert req.full_url == "https://x.test/p?a=1&key=K"


def test_retries_5xx_and_429_with_backoff_then_succeeds():
    http, calls, sleeps = make([http_error(503), http_error(429, "7"), b"ok"], backoff=2.0)
    assert http.get("https://x.test/p") == b"ok"
    assert len(calls) == 3
    assert 2.0 in sleeps and 7.0 in sleeps  # backoff, then Retry-After honoured


def test_gives_up_as_outage_and_never_leaks_the_query():
    http, calls, _ = make([http_error(503)] * 3, retries=2)
    with pytest.raises(FetchError) as e:
        http.get("https://x.test/p", params={"key": "SECRET"})
    assert e.value.kind == "outage"
    assert "SECRET" not in e.value.reason and "x.test/p" in e.value.reason
    assert len(calls) == 3


def test_4xx_is_an_error_without_retry():
    http, calls, _ = make([http_error(404)])
    with pytest.raises(FetchError) as e:
        http.get("https://x.test/p")
    assert e.value.kind == "error" and len(calls) == 1


def test_network_failure_is_an_outage():
    http, _, _ = make([urllib.error.URLError("reset")] * 2, retries=1)
    with pytest.raises(FetchError) as e:
        http.get("https://x.test/p")
    assert e.value.kind == "outage"


def test_one_request_at_a_time_per_host():
    http, _, sleeps = make([b"a", b"b", b"c"], min_interval=1.0)
    http.get("https://x.test/1")
    http.get("https://x.test/2")  # same host: waits
    http.get("https://other.test/1")  # other host: no wait
    assert sleeps == [1.0]
