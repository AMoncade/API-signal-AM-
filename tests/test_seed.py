"""Phase 2 - seeding orchestration end-to-end (offline).

Derives slugs from the company NAME (no domain step), probes the real AtsProber
over MockTransport-served fixtures, verifies, and stores matches in InMemoryStore.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from core.config import Settings
from core.http_client import HttpClient
from worker.form_d.models import CompanyRecord
from worker.seeding.ats import AtsHit, AtsProber
from worker.seeding.seed import seed_companies
from worker.store import InMemoryStore

ATS = Path(__file__).parent / "fixtures" / "ats"


def _ats_client() -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if "greenhouse" in host:
            token = path.rstrip("/").split("/")[-2]
            f = ATS / f"greenhouse_{token}.json"
            return httpx.Response(200, text=f.read_text(encoding="utf-8")) if f.exists() else httpx.Response(404, text="x")
        if "lever" in host:
            token = path.rstrip("/").split("/")[-1]
            f = ATS / f"lever_{token}.json"
            return httpx.Response(200, text=f.read_text(encoding="utf-8")) if f.exists() else httpx.Response(404, text="")
        return httpx.Response(404)

    return HttpClient(Settings(_env_file=None), transport=httpx.MockTransport(handler))


def test_seed_matches_ats_and_logs_run() -> None:
    store = InMemoryStore()
    store.upsert_company(CompanyRecord("0000000001", "Stripe, Inc.", "CA"))   # greenhouse board exists
    store.upsert_company(CompanyRecord("0000000002", "Obscure Widgets", "DE"))  # no board anywhere

    with _ats_client() as client:
        stats = seed_companies(client, store, prober=AtsProber(client))

    assert stats.seen == 2
    assert stats.ats_matched == 1
    assert store.ats["0000000001"] == ("greenhouse", "stripe")
    assert "0000000002" not in store.ats
    assert store.runs[-1]["status"] == "success"
    assert store.runs[-1]["job_name"] == "seeding"


class _FixedProber:
    """Returns the same hit for any token (to exercise the verification gate)."""

    def __init__(self, hit: AtsHit) -> None:
        self._hit = hit

    def probe_tokens(self, tokens):
        return self._hit if tokens else None


def test_seed_rejects_false_positive_match() -> None:
    store = InMemoryStore()
    store.upsert_company(CompanyRecord("0000000003", "Apex Tech Growth Partners, LLC", "DE"))
    # A coincidental greenhouse board named "Apex Fintech" must NOT be accepted.
    prober = _FixedProber(AtsHit("greenhouse", "apex", 1, None, company_name="Apex Fintech"))
    stats = seed_companies(None, store, prober=prober)
    assert stats.ats_matched == 0
    assert stats.ats_rejected == 1
    assert "0000000003" not in store.ats


def test_seed_skips_companies_already_matched() -> None:
    store = InMemoryStore()
    store.upsert_company(CompanyRecord("0000000001", "Stripe, Inc.", "CA"))
    store.set_ats("0000000001", "greenhouse", "stripe")   # already matched
    with _ats_client() as client:
        stats = seed_companies(client, store, prober=AtsProber(client))
    assert stats.seen == 0     # already-matched companies are not re-seeded
