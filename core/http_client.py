"""The ONE rate-limited HTTP client every component reuses.

By contract (CLAUDE.md), nothing else opens its own connection pool or invents a
parallel limiter. This module gives the whole project two guarantees:

1. A single GLOBAL request cap (default 10 req/s) — enforced across all callers
   and threads, because EDGAR's limit is "<= 10 requests/second across ALL
   sec.gov domains" (HARD RULE #1).
2. The mandatory ``SEC_USER_AGENT`` header is attached to every sec.gov request
   (and to no others). Missing UA = 403, so we refuse to send a sec.gov request
   when the UA is unset rather than fail mysteriously at the network.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from urllib.parse import urlsplit

import httpx

from core.config import Settings, get_settings

_SEC_DOMAIN = "sec.gov"


class RateLimiter:
    """Thread-safe rolling-window limiter: at most ``max_per_second`` acquisitions
    in any 1-second window.

    The lock is intentionally held across the throttling sleep. That serializes
    callers while throttling, which is exactly what a GLOBAL cap needs — two
    threads must not each independently decide they're under the limit.
    """

    def __init__(self, max_per_second: float) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be > 0")
        self._max = float(max_per_second)
        self._lock = threading.Lock()
        self._recent: deque[float] = deque()

    def acquire(self) -> None:
        """Block until a request is allowed under the global cap, then record it."""
        with self._lock:
            while True:
                now = time.monotonic()
                # Drop timestamps that have aged out of the 1-second window.
                while self._recent and now - self._recent[0] >= 1.0:
                    self._recent.popleft()
                if len(self._recent) < self._max:
                    self._recent.append(now)
                    return
                # Window is full: wait until the oldest timestamp expires.
                sleep_for = 1.0 - (now - self._recent[0])
                if sleep_for > 0:
                    time.sleep(sleep_for)


class HttpClient:
    """Rate-limited, SEC-aware wrapper around a single ``httpx.Client``."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._limiter = RateLimiter(self._settings.http_max_requests_per_second)
        self._sec_user_agent = self._settings.sec_user_agent
        # `transport` is an injection point for tests (httpx.MockTransport).
        self._client = httpx.Client(
            timeout=self._settings.http_timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    @staticmethod
    def is_sec_url(url: str) -> bool:
        """True only for sec.gov and its subdomains (www., data., efts., ...).

        Guards against look-alikes: ``notsec.gov`` and ``sec.gov.evil.com`` are
        both False.
        """
        host = (urlsplit(url).hostname or "").lower()
        return host == _SEC_DOMAIN or host.endswith("." + _SEC_DOMAIN)

    def prepare_headers(
        self, url: str, headers: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Return the final header set for a request, injecting the SEC UA when
        (and only when) the request targets sec.gov. Raises if a sec.gov request
        is attempted without a configured UA."""
        merged = dict(headers or {})
        if self.is_sec_url(url):
            if not self._sec_user_agent:
                raise RuntimeError(
                    "SEC_USER_AGENT is required for sec.gov requests "
                    "(missing User-Agent = 403). Set it in your environment / .env."
                )
            merged.setdefault("User-Agent", self._sec_user_agent)
        return merged

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> httpx.Response:
        final_headers = self.prepare_headers(url, headers)
        self._limiter.acquire()  # global throttle happens here, after header validation
        return self._client.request(method, url, headers=final_headers, **kwargs)

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
