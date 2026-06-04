"""Behavior tests for the ONE shared rate-limited HTTP client.

These assert the actual guarantees the rest of the project depends on:
- the global rate cap really throttles (and doesn't over-throttle under it),
- the mandatory SEC_USER_AGENT is attached to sec.gov requests and ONLY those,
- a sec.gov request without a configured UA is refused (missing UA = 403),
- the sec.gov host check rejects look-alike domains.
No network is used: rate limiting is timed directly, and request behavior is
exercised through an httpx.MockTransport.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from core.config import Settings
from core.http_client import HttpClient, RateLimiter


def _settings(**overrides) -> Settings:
    # _env_file=None isolates tests from any developer .env on disk.
    return Settings(_env_file=None, **overrides)


# --- rate limiter ------------------------------------------------------------

def test_rate_limiter_throttles_over_the_cap() -> None:
    # cap=10/s, ask for 11: the 11th must wait ~1s for the window to roll.
    limiter = RateLimiter(max_per_second=10)
    start = time.monotonic()
    for _ in range(11):
        limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.9, f"expected ~1s throttle for the 11th call, got {elapsed:.3f}s"


def test_rate_limiter_does_not_throttle_under_the_cap() -> None:
    # 5 acquisitions under a cap of 10 should be effectively instant.
    limiter = RateLimiter(max_per_second=10)
    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.2, f"under-cap calls should not block, took {elapsed:.3f}s"


def test_rate_limiter_rejects_bad_rate() -> None:
    with pytest.raises(ValueError):
        RateLimiter(max_per_second=0)


# --- sec.gov host detection --------------------------------------------------

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.sec.gov/Archives/edgar/data/1/x/primary_doc.xml", True),
        ("https://data.sec.gov/submissions/CIK0000320193.json", True),
        ("https://efts.sec.gov/LATEST/search-index", True),
        ("https://sec.gov/", True),
        ("https://boards-api.greenhouse.io/v1/boards/acme/jobs", False),
        ("https://api.lever.co/v0/postings/acme", False),
        ("https://notsec.gov/", False),          # look-alike suffix
        ("https://sec.gov.evil.com/", False),     # look-alike subdomain
    ],
)
def test_is_sec_url(url: str, expected: bool) -> None:
    assert HttpClient.is_sec_url(url) is expected


# --- SEC_USER_AGENT injection ------------------------------------------------

def test_sec_user_agent_attached_only_to_sec_requests() -> None:
    ua = "TestCo test@example.com"
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    client = HttpClient(_settings(sec_user_agent=ua), transport=httpx.MockTransport(handler))

    client.get("https://www.sec.gov/Archives/edgar/data/1/x/primary_doc.xml")
    client.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs")
    client.close()

    sec_req, ats_req = captured
    assert sec_req.headers["user-agent"] == ua
    # The ATS request must NOT carry our SEC UA (httpx sets its own default UA).
    assert ats_req.headers.get("user-agent") != ua


def test_sec_request_without_user_agent_is_refused() -> None:
    client = HttpClient(_settings(sec_user_agent=""))
    try:
        with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
            client.prepare_headers("https://www.sec.gov/Archives/edgar/data/1/x.xml")
        # Non-sec requests are unaffected even without a UA configured.
        assert client.prepare_headers("https://boards-api.greenhouse.io/x") == {}
    finally:
        client.close()


# --- transient-failure retries ----------------------------------------------

def _counting_transport(responses):
    """MockTransport that returns/raises each item of `responses` in turn, then
    repeats the last item. Items may be httpx.Response or an Exception to raise.
    Returns (transport, calls) where calls is a mutable [count]."""
    calls = [0]
    seq = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = seq[min(calls[0], len(seq) - 1)]
        calls[0] += 1
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler), calls


def _retry_client(responses, **settings_overrides):
    """An HttpClient over a scripted transport with a no-op sleep (no real wait)."""
    transport, calls = _counting_transport(responses)
    delays: list[float] = []
    settings = _settings(http_retry_backoff_seconds=0.01, **settings_overrides)
    client = HttpClient(settings, transport=transport, sleep=delays.append)
    return client, calls, delays


def test_retries_on_503_then_succeeds() -> None:
    client, calls, delays = _retry_client(
        [httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"ok": True})],
        http_max_retries=3,
    )
    resp = client.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs")
    client.close()
    assert resp.status_code == 200
    assert calls[0] == 3                 # two 503s then the 200
    assert len(delays) == 2              # backed off before each retry
    assert delays == [0.01, 0.02]        # exponential: base, base*2


def test_gives_up_after_max_retries_and_returns_last_response() -> None:
    client, calls, _ = _retry_client([httpx.Response(503)], http_max_retries=2)
    resp = client.get("https://boards-api.greenhouse.io/x")
    client.close()
    assert resp.status_code == 503       # exhausted retries -> last response returned, not raised
    assert calls[0] == 3                 # initial try + 2 retries


def test_retry_after_header_is_honored() -> None:
    client, calls, delays = _retry_client(
        [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200)],
        http_max_retries=3,
    )
    resp = client.get("https://api.anthropic.com/v1/messages")
    client.close()
    assert resp.status_code == 200
    assert delays == [2.0]               # server-specified wait used instead of backoff


def test_retries_on_transport_error_then_succeeds() -> None:
    client, calls, _ = _retry_client(
        [httpx.ConnectError("boom"), httpx.Response(200)],
        http_max_retries=3,
    )
    resp = client.get("https://boards-api.greenhouse.io/x")
    client.close()
    assert resp.status_code == 200
    assert calls[0] == 2


def test_transport_error_reraised_after_max_retries() -> None:
    client, calls, _ = _retry_client([httpx.ConnectError("boom")], http_max_retries=2)
    with pytest.raises(httpx.ConnectError):
        client.get("https://boards-api.greenhouse.io/x")
    client.close()
    assert calls[0] == 3                 # initial try + 2 retries, then re-raised


def test_client_error_4xx_is_not_retried() -> None:
    client, calls, delays = _retry_client([httpx.Response(404)], http_max_retries=3)
    resp = client.get("https://boards-api.greenhouse.io/x")
    client.close()
    assert resp.status_code == 404       # a 404 is a real answer, returned immediately
    assert calls[0] == 1
    assert delays == []


def test_retries_disabled_when_max_retries_zero() -> None:
    client, calls, _ = _retry_client([httpx.Response(503)], http_max_retries=0)
    resp = client.get("https://boards-api.greenhouse.io/x")
    client.close()
    assert resp.status_code == 503
    assert calls[0] == 1                 # no retries attempted


def test_post_is_retried_and_resubmits_body() -> None:
    # The headline retry use case is a POST: the Anthropic classifier and the Ollama
    # extractor both POST a json body. Retrying must resubmit the SAME body.
    captured: list[httpx.Request] = []
    seq = [httpx.Response(503), httpx.Response(200, json={"ok": True})]
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        item = seq[min(calls[0], len(seq) - 1)]
        calls[0] += 1
        return item

    client = HttpClient(
        _settings(http_max_retries=3, http_retry_backoff_seconds=0.01),
        transport=httpx.MockTransport(handler),
        sleep=lambda _d: None,
    )
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    resp = client.post("https://api.anthropic.com/v1/messages", json=body)
    client.close()

    assert resp.status_code == 200
    assert calls[0] == 2                          # the POST itself was retried
    assert all(r.method == "POST" for r in captured)
    assert json.loads(captured[1].content) == body   # same body resubmitted on retry


def test_retry_after_http_date_falls_back_to_backoff() -> None:
    # The HTTP-date form of Retry-After is intentionally not parsed -> exponential backoff.
    client, _calls, delays = _retry_client(
        [httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
         httpx.Response(200)],
        http_max_retries=3,
    )
    resp = client.get("https://api.anthropic.com/v1/messages")
    client.close()
    assert resp.status_code == 200
    assert delays == [0.01]                       # base backoff, not 0 and no crash


def test_retry_after_large_value_is_capped() -> None:
    client, _calls, delays = _retry_client(
        [httpx.Response(429, headers={"Retry-After": "600"}), httpx.Response(200)],
        http_max_retries=3,
    )
    client.get("https://api.anthropic.com/v1/messages")
    client.close()
    assert delays == [30.0]                       # min(600, _MAX_BACKOFF_SECONDS)


def test_retry_after_negative_falls_back_to_backoff() -> None:
    client, _calls, delays = _retry_client(
        [httpx.Response(429, headers={"Retry-After": "-5"}), httpx.Response(200)],
        http_max_retries=3,
    )
    client.get("https://api.anthropic.com/v1/messages")
    client.close()
    assert delays == [0.01]                       # negative ignored -> backoff
