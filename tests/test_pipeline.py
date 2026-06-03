"""Phase 6 - the nightly pipeline runs every job in order and logs each run.

Offline: EDGAR discovery is stubbed empty, so no network is needed; this verifies
the orchestration wiring + per-job ingestion_runs logging, not the per-job logic
(which each phase tests against real fixtures).
"""

from __future__ import annotations

import httpx

from core.config import Settings
from core.http_client import HttpClient
from worker.pipeline import run_all
from worker.store import InMemoryStore


def test_run_all_runs_and_logs_every_job(monkeypatch) -> None:
    # No filings discovered -> seeding/snapshots/migrations all no-op over an empty store.
    monkeypatch.setattr(
        "worker.form_d.ingest.fetch_filing_refs", lambda client, *, forms, date=None: []
    )
    store = InMemoryStore()
    client = HttpClient(
        Settings(_env_file=None, sec_user_agent="t t@example.com"),
        transport=httpx.MockTransport(lambda r: httpx.Response(404)),
    )
    try:
        results = run_all(client, store, classifier=None)
    finally:
        client.close()

    # 8-K is skipped without a classifier; the other four jobs ran.
    assert "eight_k" not in results
    assert {"form_d", "seeding", "job_snapshots", "migrations"} <= set(results)

    logged = {r["job_name"] for r in store.runs}
    assert {"form_d", "seeding", "job_snapshots", "migrations"} <= logged
    assert all(r["status"] == "success" for r in store.runs)
