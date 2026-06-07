"""Phase 5.5 - integration pass (offline, end-to-end).

Runs one company through the whole pipeline and proves the headline JOIN returns
it: ingest a REAL Form D (ClusterTruck) via the Phase 1 code path, seed its ATS
board (Phase 2), record a ~35-day-old baseline + a current 3x snapshot (Phase 3),
then assert the funded_and_hiring join (Phase 5 read) surfaces it. Also validates
the joined row against the API response schema, so a column rename in one phase
can't silently break the next.

The view is empty by design on day one (needs a 30-day-old baseline), so we seed
the baseline exactly as the integration-pass prompt describes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from api.schemas import FundedAndHiringSignal, FundingSignal
from api.repository import filing_index_url
from core.config import Settings
from core.http_client import HttpClient
from worker.edgar.daily_index import FilingRef
from worker.form_d.ingest import RunStats, ingest_one
from worker.store import InMemoryStore

FIXTURES = Path(__file__).parent / "fixtures" / "form_d"
CT_ACCESSION = "0001643138-26-000003"
CT_CIK = "0001643138"   # zero-padded, as the parser stores it


def _form_d_client() -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        folder = request.url.path.rstrip("/").split("/")[-2]
        accession = f"{folder[:10]}-{folder[10:12]}-{folder[12:]}"
        f = FIXTURES / f"{accession}.xml"
        return (
            httpx.Response(200, text=f.read_text(encoding="utf-8"))
            if f.exists()
            else httpx.Response(404)
        )

    return HttpClient(
        Settings(_env_file=None, sec_user_agent="Test t@example.com"),
        transport=httpx.MockTransport(handler),
    )


def _seed_full_pipeline() -> InMemoryStore:
    store = InMemoryStore()
    now = datetime.now(timezone.utc)

    # Phase 1: ingest the real Form D (recent, so it is inside the 90-day window).
    ref = FilingRef(
        cik="1643138",
        company_name="ClusterTruck, Inc.",
        submission_type="D",
        filed_at=now - timedelta(days=2),
        accession_number=CT_ACCESSION,
        primary_doc_url=(
            "https://www.sec.gov/Archives/edgar/data/1643138/"
            "000164313826000003/primary_doc.xml"
        ),
    )
    with _form_d_client() as client:
        ingest_one(client, store, ref, RunStats())

    # Phase 2: ATS board matched.
    store.set_ats(CT_CIK, "greenhouse", "clustertruck")

    # Phase 3: a ~35-day-old baseline (10) + a current 3x snapshot (30) => surging.
    store.insert_snapshot(CT_CIK, 10, snapshot_date=date.today() - timedelta(days=35))
    store.insert_snapshot(CT_CIK, 30, snapshot_date=date.today())
    return store


def test_funded_and_hiring_join_returns_seeded_company() -> None:
    store = _seed_full_pipeline()

    # company + filing landed under the padded CIK
    assert CT_CIK in store.companies
    assert store.filings[CT_ACCESSION].total_amount_sold_usd is not None

    rows = store.funded_and_hiring()
    assert len(rows) == 1, "the funded-and-hiring join should return exactly the seeded company"
    row = rows[0]
    assert row["cik"] == CT_CIK
    assert row["entity_name"] == "ClusterTruck, Inc."
    assert row["ats_provider"] == "greenhouse"
    assert row["current_open"] == 30
    assert row["baseline_open"] == 10
    assert row["velocity_ratio"] == 3.0

    # cross-phase alignment: the joined row validates against the API response model
    model = FundedAndHiringSignal.model_validate(row)
    assert model.cik == CT_CIK and model.velocity_ratio == 3.0


def test_funding_row_aligns_with_api_schema() -> None:
    store = _seed_full_pipeline()
    f = store.filings[CT_ACCESSION]
    row = {
        **f.to_row(),
        "entity_name": store.companies[CT_CIK].entity_name,
        "source_url": filing_index_url(f.cik, f.accession_number),
    }
    model = FundingSignal.model_validate(row)
    assert model.entity_name == "ClusterTruck, Inc."
    assert model.source_url.endswith("0001643138-26-000003-index.htm")
    assert model.total_offering_amount_usd == 1000000.0


def test_join_empty_without_baseline() -> None:
    # No baseline snapshot -> not surging -> join is empty (the day-one reality).
    store = InMemoryStore()
    now = datetime.now(timezone.utc)
    ref = FilingRef(
        cik="1643138", company_name="ClusterTruck, Inc.", submission_type="D",
        filed_at=now - timedelta(days=2), accession_number=CT_ACCESSION,
        primary_doc_url=(
            "https://www.sec.gov/Archives/edgar/data/1643138/000164313826000003/primary_doc.xml"
        ),
    )
    with _form_d_client() as client:
        ingest_one(client, store, ref, RunStats())
    store.set_ats(CT_CIK, "greenhouse", "clustertruck")
    store.insert_snapshot(CT_CIK, 30, snapshot_date=date.today())  # only one day
    assert store.funded_and_hiring() == []
