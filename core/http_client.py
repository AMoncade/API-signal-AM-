"""The ONE rate-limited HTTP client every component reuses.

By contract (CLAUDE.md), nothing else opens its own connection pool or invents a
parallel limiter. This module gives the whole project two guarantees:

1. A single GLOBAL request cap (default 10 req/s) — enforced across all callers
   and threads, because EDGAR's limit is "<= 10 requests/second across ALL
   sec.gov domains" (HARD RULE #1).
2. The mandatory ``SEC_USER_AGENT`` header is attached to every sec.gov request
   (and to no others). Missing UA = 403, so we refuse to send a sec.gov request
   when the UA is unset rather than fail mysteriously at the network.
3. Transient failures are retried with exponential backoff — HTTP 429/5xx and
   network/timeout errors. EDGAR and the Anthropic API both rate-limit with 429s
   and have brief 5xx blips; one hiccup must not abort a nightly run. Every retry
   re-acquires a rate slot, so retrying never breaches the global req/s cap, and a
   ``Retry-After`` header is honored when the server sends one.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from core.config import Settings, get_settings

log = logging.getLogger("core.http_client")

_SEC_DOMAIN = "sec.gov"

# Statuses worth retrying: 429 (rate limited) and the transient 5xx family. A 4xx
# other than 429 is a client error and is returned to the caller unchanged.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Backoff is capped so a long Retry-After or a high attempt count can't stall the
# worker for minutes on a single request.
_MAX_BACKOFF_SECONDS = 30.0


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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        self._limiter = RateLimiter(self._settings.http_max_requests_per_second)
        self._sec_user_agent = self._settings.sec_user_agent
        self._max_retries = max(0, int(self._settings.http_max_retries))
        self._backoff_base = float(self._settings.http_retry_backoff_seconds)
        # `sleep` is injectable so retry tests don't spend real seconds backing off.
        self._sleep = sleep
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

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        """Parse a ``Retry-After`` header value given as a number of seconds.

        The HTTP-date form is not honored (rare from these APIs); we fall back to
        exponential backoff for it. Returns None when absent/unparseable/negative.
        """
        if not value:
            return None
        try:
            secs = float(value)
        except ValueError:
            return None
        return secs if secs >= 0 else None

    def _backoff_seconds(self, attempt: int, retry_after: float | None) -> float:
        """Seconds to wait before the next attempt (0-indexed ``attempt``)."""
        if retry_after is not None:
            return min(retry_after, _MAX_BACKOFF_SECONDS)
        return min(self._backoff_base * (2 ** attempt), _MAX_BACKOFF_SECONDS)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        retries: int | None = None,
        **kwargs,
    ) -> httpx.Response:
        # `retries` overrides the configured max for THIS call. Best-effort probes
        # (ATS board existence) pass retries=0 so a 429/5xx/error is an instant miss
        # instead of triggering backoff -- a rate-limited provider (e.g. Workable)
        # can send Retry-After: 30 and stall an entire scan otherwise.
        max_retries = self._max_retries if retries is None else max(0, int(retries))
        final_headers = self.prepare_headers(url, headers)
        attempt = 0
        while True:
            # Throttle before EVERY attempt (including retries) so the global cap
            # holds across retries, then issue the request.
            self._limiter.acquire()
            try:
                response = self._client.request(method, url, headers=final_headers, **kwargs)
            except httpx.TransportError as exc:
                # Network/timeout error: retry with backoff, then give up.
                if attempt >= max_retries:
                    raise
                delay = self._backoff_seconds(attempt, None)
                log.warning(
                    "transient HTTP error for %s (%s); retry %d/%d in %.2fs",
                    url, exc, attempt + 1, max_retries, delay,
                )
                self._sleep(delay)
                attempt += 1
                continue
            if response.status_code in _RETRYABLE_STATUS and attempt < max_retries:
                retry_after = self._retry_after_seconds(response.headers.get("Retry-After"))
                response.close()
                delay = self._backoff_seconds(attempt, retry_after)
                log.warning(
                    "retryable status %d for %s; retry %d/%d in %.2fs",
                    response.status_code, url, attempt + 1, max_retries, delay,
                )
                self._sleep(delay)
                attempt += 1
                continue
            return response

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
