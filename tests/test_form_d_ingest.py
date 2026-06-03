"""Phase 1 - end-to-end ingest orchestration, offline.

Drives the REAL pipeline (discovery -> fetch via the shared HttpClient -> parse ->
filter pooled -> incremental diff -> store -> run log) with no network and no
Supabase: the daily index is a synthetic master.idx pointing at the real fixtures,
and the HttpClient serves those fixtures through an httpx.MockTransport. This is the
integration check from CLAUDE.md's DoD - it proves the rows the parser produces are
exactly what gets written under the locked schema, and that the run is logged.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx

from core.config import Settings
from core.http_client import HttpClient
from worker.edgar.daily_index import parse_master_idx
from worker.form_d.ingest import ingest_one, RunStats
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
