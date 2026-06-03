"""Phase 5 - read API: endpoints, pagination, filters, migrations enrichment,
provenance URLs, and the RapidAPI proxy-secret gate.

Uses a fake Repository injected via dependency override, so the routes are tested
without a database. (The actual SQL views are exercised in the integration phase.)
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.deps import get_repository
from api.main import create_app
from api.repository import Page
from core.config import Settings


class FakeRepository:
    """Seeded, no-DB repository with genuine offset/limit paging + filter capture."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._funding = [
            {
                "cik": f"000000000{i}",
                "accession_number": f"000164313826-00000{i}",
                "entity_name": f"Startup {i}, Inc.",
                "industry_group": "Other Technology",
                "submission_type": "D",
                "filed_at": "2026-06-02T00:00:00+00:00",
                "total_offering_amount_raw": "Indefinite" if i == 0 else "1000000",
                "total_offering_amount_usd": None if i == 0 else 1000000.0,
                "total_amount_sold_usd": 425521.0,
                "incremental_amount_sold_usd": 425521.0,
                "federal_exemption": "06b",
                "source_url": f"https://www.sec.gov/Archives/edgar/data/{i}/x-index.htm",
            }
            for i in range(5)
        ]

    def _page(self, rows, offset, limit) -> Page:
        window = rows[offset : offset + limit + 1]
        return Page(items=window[:limit], has_more=len(window) > limit)

    def pre_announced_funding(self, *, offset, limit, sector=None, filed_after=None) -> Page:
        self.calls.append(("funding", offset, limit, sector, filed_after))
        rows = self._funding
        if sector:
            rows = [r for r in rows if r["industry_group"] == sector]
        return self._page(rows, offset, limit)

    def surging_velocity(self, *, offset, limit, min_ratio=None) -> Page:
        self.calls.append(("velocity", offset, limit, min_ratio))
        rows = [
            {
                "cik": "0000000001",
                "entity_name": "Startup 1, Inc.",
                "ats_provider": "greenhouse",
                "current_open": 40,
                "baseline_open": 10,
                "velocity_ratio": 4.0,
                "is_surging": True,
                "source_url": "https://boards.greenhouse.io/startup1",
            }
        ]
        return self._page(rows, offset, limit)

    def material_risks(self, *, offset, limit, severity=None, event_type=None, filed_after=None) -> Page:
        self.calls.append(("risks", offset, limit, severity, event_type, filed_after))
        rows = [
            {
                "cik": "0000320193",
                "entity_name": "Example Public Co",
                "filed_at": "2026-06-02T00:00:00+00:00",
                "item_codes": ["5.02"],
                "event_type": "exec_departure",
                "severity": "high",
                "is_abrupt": True,
                "affected_role": "CFO",
                "summary": "CFO departed effective immediately.",
                "confidence": 0.9,
                "source_url": "https://www.sec.gov/Archives/edgar/data/320193/x-index.htm",
            }
        ]
        if severity:
            rows = [r for r in rows if r["severity"] == severity]
        return self._page(rows, offset, limit)

    def funded_and_hiring(self, *, offset, limit) -> Page:
        self.calls.append(("join", offset, limit))
        rows = [
            {
                "cik": "0000000001",
                "entity_name": "Startup 1, Inc.",
                "derived_domain": "startup1.com",
                "ats_provider": "greenhouse",
                "latest_filing_date": "2026-06-02T00:00:00+00:00",
                "latest_amount_sold_usd": 425521.0,
                "current_open": 40,
                "baseline_open": 10,
                "velocity_ratio": 4.0,
            }
        ]
        return self._page(rows, offset, limit)

    def migrations_for_ciks(self, ciks):
        return {
            cik: [
                {
                    "software": "Salesforce",
                    "signal_type": "migration",
                    "confidence": 0.4,
                    "source_posting_url": "https://boards.greenhouse.io/startup1/jobs/1",
                    "detected_at": None,
                }
            ]
            for cik in ciks
        }


def _client(secret: str = "") -> tuple[TestClient, FakeRepository]:
    fake = FakeRepository()
    app = create_app(Settings(_env_file=None, rapidapi_proxy_secret=secret))
    app.dependency_overrides[get_repository] = lambda: fake
    return TestClient(app), fake


def test_pre_announced_funding_shape_and_migrations() -> None:
    client, _ = _client()
    r = client.get("/signals/pre-announced-funding?page_size=3")
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 1 and body["page_size"] == 3
    assert body["count"] == 3 and body["has_more"] is True
    first = body["items"][0]
    assert first["total_offering_amount_raw"] == "Indefinite"   # not zeroed
    assert first["total_offering_amount_usd"] is None
    assert first["source_url"].startswith("https://www.sec.gov/")
    # migrations enrichment attached on company-centric records
    assert first["migrations"][0]["software"] == "Salesforce"


def test_funding_sector_filter_passed_through() -> None:
    client, fake = _client()
    client.get("/signals/pre-announced-funding?sector=Other%20Technology&filed_after=2026-01-01")
    assert fake.calls[-1] == ("funding", 0, 25, "Other Technology", "2026-01-01")


def test_pagination_offset() -> None:
    client, fake = _client()
    client.get("/signals/pre-announced-funding?page=2&page_size=2")
    # offset = (page-1)*page_size = 2
    assert fake.calls[-1][1] == 2 and fake.calls[-1][2] == 2


def test_surging_velocity_and_risks() -> None:
    client, _ = _client()
    v = client.get("/signals/surging-velocity?min_ratio=2").json()
    assert v["items"][0]["is_surging"] is True
    assert v["items"][0]["velocity_ratio"] == 4.0
    risks = client.get("/signals/material-risks?severity=high").json()
    assert risks["items"][0]["event_type"] == "exec_departure"
    assert risks["items"][0]["severity"] == "high"


def test_funded_and_hiring_join_with_migrations() -> None:
    client, _ = _client()
    body = client.get("/signals/funded-and-hiring").json()
    row = body["items"][0]
    assert row["current_open"] == 40 and row["velocity_ratio"] == 4.0
    assert row["migrations"][0]["software"] == "Salesforce"


def test_proxy_secret_gate() -> None:
    client, _ = _client(secret="topsecret")
    # no header -> rejected
    assert client.get("/signals/pre-announced-funding").status_code == 403
    # correct header -> allowed
    ok = client.get(
        "/signals/pre-announced-funding", headers={"X-RapidAPI-Proxy-Secret": "topsecret"}
    )
    assert ok.status_code == 200
    # health stays reachable without the secret (liveness probe)
    assert client.get("/health").status_code == 200


def test_health_open_when_no_secret() -> None:
    client, _ = _client()
    assert client.get("/health").json()["status"] == "ok"
