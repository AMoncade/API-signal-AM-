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
