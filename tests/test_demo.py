"""The /demo dashboard page: served, gate-exempt, and CORS-enabled.

Asserts real behavior of the running app (per CLAUDE.md DoD):
  * GET /demo returns the bundled demo/dashboard.html.
  * /demo stays reachable when the proxy-secret gate is CLOSED, while /signals
    remains gated (the page is a static shell; its data fetches still need the
    secret in production).
  * CORS headers are present, so the same file works opened from disk or hosted
    elsewhere against a dev instance.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import create_app
from core.config import Settings


def _client(secret: str = "") -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, rapidapi_proxy_secret=secret)))


def test_demo_page_is_served() -> None:
    resp = _client().get("/demo")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    # The real file, not a stub: endpoint paths the page fetches are baked in.
    assert "/signals/funded-and-hiring" in resp.text
    assert "/signals/material-risks" in resp.text


def test_demo_is_exempt_from_proxy_gate_but_signals_are_not() -> None:
    client = _client(secret="s3cret")
    assert client.get("/demo").status_code == 200  # static shell stays reachable
    assert client.get("/signals/material-risks").status_code == 403  # data stays gated


def test_cors_allows_cross_origin_get() -> None:
    resp = _client().get("/health", headers={"Origin": "http://example.com"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"
