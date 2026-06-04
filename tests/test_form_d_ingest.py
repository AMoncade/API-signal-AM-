"""Phase 1 - end-to-end ingest orchestration, offline.

Drives the REAL pipeline (discovery -> fetch via the shared HttpClient -> parse ->
filter pooled -> incremental diff -> store -> run log) with no network and no
Supabase: the daily index is a synthetic master.idx pointing at the real fixtures,
and the HttpClient serves those fixtures through an httpx.MockTransport. This is the
integration check from CLAUDE.md's DoD - it proves the rows the parser produces are
exactly what gets written under the locked schema, and that the run is logged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from core.config import Settings
from core.http_client import HttpClient
from worker.edgar.daily_index import FilingRef, parse_master_idx
from worker.form_d.ingest import ingest_form_d, ingest_one, RunStats
from worker.store import InMemoryStore

FIXTURES = Path(__file__).parent / "fixtures" / "form_d"

# Two real Secured Income Fund-II D/A filings (a chain), one pooled fund (iCapital,
# must be skipped), and FEB BANCSHARES - referencing the real fixture files.
MASTER_IDX = """\
CIK|Company Name|Form Type|Date Filed|File Name
--------------------------------------------------------------------------------
1504410|Secured Income Fund-II, LLC.|D/A|2025-05-22|edgar/data/1504410/0001504410-25-000003.txt
1369790|FEB BANCSHARES INC|D|2026-06-02|edgar/data/1369790/0001369790-26-000004.txt
1455085|iCapital Multi-Strategy Fund, L.P.|D/A|2026-06-02|edgar/data/1455085/0001455085-26-000001.txt
1504410|Secured Income Fund-II, LLC.|D/A|2026-06-02|edgar/data/1504410/0001504410-26-000001.txt
"""


def _fixture_serving_client() -> HttpClient:
    """An HttpClient whose transport returns the on-disk fixture for each
    primary_doc.xml URL (mapping the accession folder back to the file)."""

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.rstrip("/").split("/")
        folder = parts[-2]  # e.g. 000150441026000001
        accession = f"{folder[:10]}-{folder[10:12]}-{folder[12:]}"
        f = FIXTURES / f"{accession}.xml"
        if not f.exists():
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=f.read_text(encoding="utf-8"))

    settings = Settings(_env_file=None, sec_user_agent="Test test@example.com")
    return HttpClient(settings, transport=httpx.MockTransport(handler))


def test_full_ingest_loop_offline() -> None:
    refs = parse_master_idx(MASTER_IDX, forms={"D", "D/A"})
    refs.sort(key=lambda r: (r.filed_at, r.accession_number))  # oldest-first (as ingest does)
    store = InMemoryStore()
    stats = RunStats()
    run_id = store.start_run("form_d")

    with _fixture_serving_client() as client:
        for ref in refs:
            ingest_one(client, store, ref, stats)
    store.finish_run(run_id, status="success", items_processed=stats.stored, notes=stats.summary())

    # pooled fund skipped; the other three stored
    assert stats.skipped_pooled == 1
    assert stats.stored == 3
    assert "0001455085" not in store.companies          # iCapital pooled -> no company row
    assert set(store.companies) == {"0001504410", "0001369790"}  # Secured (x2 filings), FEB

    # incremental diff computed from the REAL D/A chain
    prior = store.filings["0001504410-25-000003"]
    current = store.filings["0001504410-26-000001"]
    assert prior.total_amount_sold_usd == Decimal("274233604")
    assert prior.incremental_amount_sold_usd == Decimal("274233604")  # no prior -> all new
    assert current.total_amount_sold_usd == Decimal("300392879")      # cumulative as filed
    assert current.incremental_amount_sold_usd == Decimal("26159275")  # de-double-counted delta

    # FEB: first filing of its cik -> incremental == cumulative
    feb = store.filings["0001369790-26-000004"]
    assert feb.incremental_amount_sold_usd == Decimal("1700000")

    # run logged
    assert store.runs[run_id]["status"] == "success"
    assert store.runs[run_id]["items_processed"] == 3


def test_ingest_is_idempotent_on_accession() -> None:
    refs = parse_master_idx(MASTER_IDX, forms={"D", "D/A"})
    store = InMemoryStore()
    stats = RunStats()
    with _fixture_serving_client() as client:
        for ref in refs:
            ingest_one(client, store, ref, stats)
        # re-run the same refs: nothing new stored, all counted as already present
        rerun = RunStats()
        for ref in refs:
            ingest_one(client, store, ref, rerun)

    assert rerun.stored == 0
    assert rerun.skipped_existing == 3   # the 3 non-pooled filings already exist
    assert rerun.skipped_pooled == 1     # pooled is still filtered before the existence check matters
    assert len(store.filings) == 3       # no duplicates


def _ref(cik: str, accession: str, url: str) -> FilingRef:
    return FilingRef(
        cik=cik,
        company_name=f"CO {cik}",
        submission_type="D",
        filed_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        accession_number=accession,
        primary_doc_url=url,
    )


def test_one_unparseable_filing_does_not_abort_run(monkeypatch) -> None:
    """A single malformed-but-HTTP-200 filing must be skipped+counted, NOT abort the
    whole daily run. Regression guard: ingest_one previously let parse_form_d's
    ValueError propagate, marking the run 'error' and dropping every later filing."""
    feb = _ref(
        "1369790",
        "0001369790-26-000004",
        "https://www.sec.gov/Archives/edgar/data/1369790/000136979026000004/primary_doc.xml",
    )
    bad_value = _ref(  # passes the '<edgarSubmission' gate but has no <primaryIssuer>
        "9999999",
        "9999999999-26-000001",
        "https://www.sec.gov/Archives/edgar/data/9999999/bad-valueerror/primary_doc.xml",
    )
    bad_xml = _ref(  # well-formed prefix, then truncated -> XML ParseError
        "9999998",
        "9999999998-26-000001",
        "https://www.sec.gov/Archives/edgar/data/9999998/bad-parseerror/primary_doc.xml",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "bad-valueerror" in path:
            return httpx.Response(200, text="<edgarSubmission><offeringData/></edgarSubmission>")
        if "bad-parseerror" in path:
            return httpx.Response(200, text="<edgarSubmission><unclosed>")
        accession = "0001369790-26-000004"
        return httpx.Response(200, text=(FIXTURES / f"{accession}.xml").read_text(encoding="utf-8"))

    settings = Settings(_env_file=None, sec_user_agent="Test test@example.com")
    client = HttpClient(settings, transport=httpx.MockTransport(handler))

    # Stub discovery so ingest_form_d (the orchestrator) drives our three refs.
    monkeypatch.setattr(
        "worker.form_d.ingest.fetch_filing_refs",
        lambda c, *, forms, date=None: [feb, bad_value, bad_xml],
    )

    store = InMemoryStore()
    with client:
        stats = ingest_form_d(client, store)   # must NOT raise

    assert stats.stored == 1                    # FEB survived
    assert stats.parse_errors == 2              # both bad filings skipped, not fatal
    assert "0001369790" in store.companies
    # the run completed successfully despite the two bad filings
    assert store.runs[-1]["status"] == "success"
    assert store.runs[-1]["items_processed"] == 1
