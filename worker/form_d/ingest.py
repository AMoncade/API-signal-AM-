"""Phase 1 orchestration: EDGAR daily index -> primary_doc.xml -> signal rows.

Wires together the pieces that are individually unit-tested:
  discovery (edgar.daily_index) -> fetch (shared HttpClient) -> parse (form_d.parser)
  -> filter pooled funds -> compute incremental -> write (store) -> log the run.

Pooled investment funds are SKIPPED entirely (HARD RULE #5 / phase1 prompt: "do NOT
create company rows for pooled funds"); they dominate the feed and are fund paperwork,
not funded startups. Personal names never leave the parser (HARD RULE #7).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.http_client import HttpClient
from worker.edgar.daily_index import FilingRef, fetch_filing_refs
from worker.form_d.models import (
    CompanyRecord,
    FormDFilingRecord,
    compute_incremental_amount,
)
from worker.form_d.parser import parse_form_d
from worker.store import Store

log = logging.getLogger("worker.form_d")

_FORMS = {"D", "D/A"}


@dataclass(slots=True)
class RunStats:
    seen: int = 0
    stored: int = 0
    skipped_pooled: int = 0
    skipped_existing: int = 0
    fetch_errors: int = 0
    stored_companies: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"seen={self.seen} stored={self.stored} pooled_skipped={self.skipped_pooled} "
            f"already_present={self.skipped_existing} fetch_errors={self.fetch_errors}"
        )


def _fetch_primary_doc(client: HttpClient, url: str) -> str | None:
    resp = client.get(url)
    if resp.status_code != 200 or "<edgarSubmission" not in resp.text:
        return None
    return resp.text


def ingest_one(client: HttpClient, store: Store, ref: FilingRef, stats: RunStats) -> None:
    """Process a single filing ref end-to-end (idempotent on accession_number)."""
    if store.filing_exists(ref.accession_number):
        stats.skipped_existing += 1
        return

    xml = _fetch_primary_doc(client, ref.primary_doc_url)
    if xml is None:
        stats.fetch_errors += 1
        log.warning("no primary_doc.xml for %s (%s)", ref.accession_number, ref.primary_doc_url)
        return

    parsed = parse_form_d(xml)

    if parsed.is_pooled_fund:
        stats.skipped_pooled += 1
        log.info("skip pooled fund %s (%s)", parsed.entity_name, parsed.cik)
        return

    store.upsert_company(
        CompanyRecord(
            cik=parsed.cik,
            entity_name=parsed.entity_name,
            state_or_country=parsed.state_or_country,
            entity_type=parsed.entity_type,
            industry_group=parsed.industry_group,
        )
    )

    prior = store.latest_amount_sold_for_cik(parsed.cik)
    incremental = compute_incremental_amount(parsed.total_amount_sold_usd, prior)

    # Enforce the schema CHECK ourselves rather than trusting the XML verbatim:
    # the daily index already guarantees ref.submission_type is 'D'/'D/A'.
    submission_type = (
        parsed.submission_type
        if parsed.submission_type in {"D", "D/A"}
        else ref.submission_type
    )

    store.insert_filing(
        FormDFilingRecord(
            accession_number=ref.accession_number,
            cik=parsed.cik,
            submission_type=submission_type,
            filed_at=ref.filed_at,
            date_of_first_sale=parsed.date_of_first_sale,
            total_offering_amount_raw=parsed.total_offering_amount_raw,
            total_offering_amount_usd=parsed.total_offering_amount_usd,
            total_amount_sold_usd=parsed.total_amount_sold_usd,
            incremental_amount_sold_usd=incremental,
            industry_group=parsed.industry_group,
            is_pooled_fund=parsed.is_pooled_fund,
            federal_exemption=parsed.federal_exemption,
        )
    )
    stats.stored += 1
    stats.stored_companies.append(f"{parsed.cik} {parsed.entity_name}")


def ingest_form_d(
    client: HttpClient,
    store: Store,
    *,
    date: str | None = None,
    limit: int | None = None,
) -> RunStats:
    """Ingest one daily index of Form D / D-A filings. Logs to ingestion_runs."""
    stats = RunStats()
    run_id = store.start_run("form_d")
    try:
        refs = fetch_filing_refs(client, forms=_FORMS, date=date)
        if limit is not None:
            refs = refs[:limit]
        stats.seen = len(refs)
        log.info("Form D ingest: %d D/D-A filings to process", stats.seen)
        for ref in refs:
            ingest_one(client, store, ref, stats)
        store.finish_run(
            run_id, status="success", items_processed=stats.stored, notes=stats.summary()
        )
    except Exception as exc:  # noqa: BLE001 - record the failure, then re-raise
        log.exception("Form D ingest failed")  # keep the traceback in worker logs
        store.finish_run(
            run_id, status="error", items_processed=stats.stored, notes=repr(exc)
        )
        raise
    log.info("Form D ingest done: %s", stats.summary())
    return stats
