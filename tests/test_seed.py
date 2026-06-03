"""Phase 2 - seeding orchestration end-to-end (offline).

Uses the real AtsProber over MockTransport-served fixtures plus a fake resolver,
against the InMemoryStore, to prove: a derived domain + a live board => ats stored;
a no-board company stays untracked; outcomes are counted; the run is logged.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from core.config import Settings
from core.http_client import HttpClient
from worker.form_d.models import CompanyRecord
from worker.seeding.ats import AtsHit, AtsProber
from worker.seeding.domain_resolver import DomainResult
from worker.seeding.seed import seed_companies
from worker.store import InMemoryStore

ATS = Path(__file__).parent / "fixtures" / "ats"


def _ats_client() -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if "greenhouse" in host:
            token = path.rstrip("/").split("/")[-2]
            f = ATS / f"greenhouse_{token}.json"
            return (
                httpx.Response(200, text=f.read_text(encoding="utf-8"))
                if f.exists()
                else httpx.Response(404, text="x")
            )
        if "lever" in host:
            token = path.rstrip("/").split("/")[-1]
            f = ATS / f"lever_{token}.json"
            return (
                httpx.Response(200, text=f.read_text(encoding="utf-8"))
                if f.exists()
                else httpx.Response(404, text="")
            )
        return httpx.Response(404)

    return HttpClient(Settings(_env_file=None), transport=httpx.MockTransport(handler))


class FakeResolver:
    def __init__(self, mapping: dict[str, DomainResult]) -> None:
        self._mapping = mapping

    def resolve(self, entity_name: str, state_or_country: str | None) -> DomainResult:
        return self._mapping.get(entity_name, DomainResult(None, "unresolved"))


def test_seed_matches_ats_and_logs_run() -> None:
    store = InMemoryStore()
    # Stripe -> greenhouse board exists; the obscure co resolves but has no board.
    store.upsert_company(CompanyRecord("0000000001", "Stripe, Inc.", "CA"))
    store.upsert_company(CompanyRecord("0000000002", "Obscure Widgets", "DE"))

    resolver = FakeResolver(
        {
            "Stripe, Inc.": DomainResult("stripe.com", "resolved"),
            "Obscure Widgets": DomainResult("obscurewidgets.com", "resolved"),
        }
    )

    with _ats_client() as client:
        stats = seed_companies(client, store, resolver=resolver, prober=AtsProber(client))

    assert stats.seen == 2
    assert stats.domains_resolved == 2
    assert stats.ats_matched == 1
    # Stripe became hiring-trackable on greenhouse; obscure co did not.
    assert store.ats["0000000001"] == ("greenhouse", "stripe")
    assert "0000000002" not in store.ats
    assert store.domains["0000000001"] == ("stripe.com", "resolved")
    # run logged as success
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
    resolver = FakeResolver(
        {"Apex Tech Growth Partners, LLC": DomainResult("apextechgrowthpartners.com", "resolved")}
    )
    # A coincidental greenhouse board named "Apex Fintech" must NOT be accepted.
    prober = _FixedProber(AtsHit("greenhouse", "apex", 1, None, company_name="Apex Fintech"))
    stats = seed_companies(None, store, resolver=resolver, prober=prober)
    assert stats.ats_matched == 0
    assert stats.ats_rejected == 1
    assert "0000000003" not in store.ats


def test_seed_skips_companies_already_seeded() -> None:
    store = InMemoryStore()
    store.upsert_company(CompanyRecord("0000000001", "Stripe, Inc.", "CA"))
    store.set_ats("0000000001", "greenhouse", "stripe")  # already matched
    resolver = FakeResolver({"Stripe, Inc.": DomainResult("stripe.com", "resolved")})
    with _ats_client() as client:
        stats = seed_companies(client, store, resolver=resolver, prober=AtsProber(client))
    assert stats.seen == 0     # nothing left needing seeding
