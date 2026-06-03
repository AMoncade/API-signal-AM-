"""The nightly pipeline: all ingestion jobs in one staggered, ordered run.

Order matters and provides the staggering the playbook asks for: Form D seeds the
company universe, seeding matches ATS boards, snapshots record today's counts, 8-K
classifies public-company events, and migrations enrich from JD text. Every step
runs through the ONE shared HttpClient, so the global <=10 req/s EDGAR cap holds
across all of them, and each step logs its own row to ingestion_runs.

8-K classification runs only when ANTHROPIC_API_KEY is configured (HARD RULE #9);
otherwise it is skipped with a warning so the rest of the pipeline still runs.
"""

from __future__ import annotations

import logging

from core.http_client import HttpClient
from worker.store import Store

log = logging.getLogger("worker.pipeline")


def run_all(
    client: HttpClient,
    store: Store,
    *,
    date: str | None = None,
    limit: int | None = None,
    classifier=None,
) -> dict:
    """Run every ingestion job once, in order. Returns each job's stats."""
    from worker.eight_k.ingest import ingest_eight_k
    from worker.form_d.ingest import ingest_form_d
    from worker.migrations.ingest import enrich_migrations, fetch_postings_text
    from worker.seeding.seed import seed_companies
    from worker.snapshots.snapshot import snapshot_jobs

    results: dict = {}
    log.info("nightly pipeline start (date=%s limit=%s)", date, limit)

    results["form_d"] = ingest_form_d(client, store, date=date, limit=limit)
    results["seeding"] = seed_companies(client, store, limit=limit)
    results["job_snapshots"] = snapshot_jobs(client, store, limit=limit)

    if classifier is not None:
        results["eight_k"] = ingest_eight_k(client, store, classifier, date=date, limit=limit)
    else:
        log.warning("8-K classification skipped: no ANTHROPIC_API_KEY (HARD RULE #9).")

    def _postings():
        for company in store.companies_with_ats(limit):
            yield from fetch_postings_text(client, company)

    results["migrations"] = enrich_migrations(store, _postings())

    log.info("nightly pipeline done")
    return results
