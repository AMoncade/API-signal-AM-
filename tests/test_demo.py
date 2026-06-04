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


# --- demo data mode (SIGNALS_DEMO_DATA) --------------------------------------

def test_demo_repository_paging_and_filters() -> None:
    from api.demo_data import DemoRepository

    repo = DemoRepository()
    crit = repo.material_risks(offset=0, limit=25, severity="critical")
    assert crit.items and all(r["severity"] == "critical" for r in crit.items)
    v = repo.surging_velocity(offset=0, limit=25, min_ratio=2.6)
    assert v.items and all(r["velocity_ratio"] >= 2.6 for r in v.items)
    p = repo.pre_announced_funding(offset=0, limit=2)
    assert len(p.items) == 2 and p.has_more is True   # limit+1 sentinel


def test_demo_mode_serves_sample_data_without_a_database(monkeypatch) -> None:
    # get_repository reads the global settings; flip demo mode on there.
    import api.deps as deps
    monkeypatch.setattr(deps, "get_settings",
                        lambda: Settings(_env_file=None, signals_demo_data=True))

    client = TestClient(create_app(Settings(_env_file=None)))

    join = client.get("/signals/funded-and-hiring").json()
    assert join["count"] >= 1
    row = join["items"][0]
    assert row["entity_name"] and row["velocity_ratio"] >= 2
    assert row["migrations"], "join rows should carry migration enrichment"

    # all four endpoints return populated rows (no DB, no supabase package needed)
    assert client.get("/signals/pre-announced-funding").json()["count"] >= 1
    assert client.get("/signals/surging-velocity").json()["count"] >= 1
    assert client.get("/signals/material-risks").json()["count"] >= 1
