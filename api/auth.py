"""RapidAPI proxy-secret gate.

RapidAPI injects a shared secret header on every request it proxies to your origin;
validating it rejects traffic that bypassed the RapidAPI gateway (HARD requirement
of the listing). The header name is RapidAPI's documented default - CONFIRM it
against the current provider docs before going live (it has changed historically),
and set RAPIDAPI_PROXY_SECRET on both RapidAPI and the host.

When no secret is configured (local dev) the gate is open; a startup warning is
logged so this is never silently relied on in production.
"""

from __future__ import annotations

import hmac
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("api.auth")

PROXY_SECRET_HEADER = "X-RapidAPI-Proxy-Secret"
# Liveness/docs/demo page must stay reachable without the proxy secret. (/demo is
# a static HTML shell; the /signals fetches it makes are still gated as usual.)
EXEMPT_PATHS = {"/health", "/docs", "/redoc", "/openapi.json", "/demo"}


class RapidApiProxyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, secret: str, *, header: str = PROXY_SECRET_HEADER) -> None:
        super().__init__(app)
        self._secret = secret
        self._header = header  # configurable: RapidAPI's header name has changed historically
        if not secret:
            log.warning(
                "RAPIDAPI_PROXY_SECRET is not set: the proxy-secret gate is OPEN "
                "(dev mode). Set it in production so only RapidAPI traffic is served."
            )

    async def dispatch(self, request: Request, call_next):
        if not self._secret or request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        provided = request.headers.get(self._header)
        # constant-time compare: this gate guards a shared secret.
        if provided is None or not hmac.compare_digest(provided, self._secret):
            return JSONResponse(
                {"detail": "Forbidden: request did not come through the RapidAPI proxy."},
                status_code=403,
            )
        return await call_next(request)
