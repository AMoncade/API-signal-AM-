"""Phase 3 orchestration: snapshot today's open-posting count per ATS company.

Reuses the shared rate-limited HttpClient and the AtsProber (same prober Phase 2
used to MATCH boards, now used to COUNT). One snapshot per company per day; counts
and optional department counts only. Logs the run to ingestion_runs.

No backfill: a missed day is a permanent hole in the velocity series, so this is
meant to run on a schedule (Phase 6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.http_client import HttpClient
from worker.seeding.ats import AtsHit, AtsProber
from worker.store import AtsCompany, Store

log = logging.getLogger("worker.snapshots")


@dataclass(slots=True)
class SnapshotStats:
    seen: int = 0
    snapshotted: int = 0
    misses: int = 0          # board returned nothing / disappeared
    total_open: int = 0

    def summary(self) -> str:
        return (
            f"seen={self.seen} snapshotted={self.snapshotted} misses={self.misses} "
            f"total_open_positions={self.total_open}"
        )


def _probe(prober: AtsProber, company: AtsCompany) -> AtsHit | None:
    if company.ats_provider == "greenhouse":
        return prober.probe_greenhouse(company.ats_token)
    if company.ats_provider == "lever":
        return prober.probe_lever(company.ats_token)
    return None


def snapshot_one(prober: AtsProber, store: Store, company: AtsCompany, stats: SnapshotStats) -> None:
    hit = _probe(prober, company)
    if hit is None:
        stats.misses += 1
        log.info("no postings for %s (%s/%s)", company.entity_name, company.ats_provider, company.ats_token)
        return
    store.insert_snapshot(company.cik, hit.open_positions, hit.dept_counts)
    stats.snapshotted += 1
    stats.total_open += hit.open_positions


def snapshot_jobs(
    client: HttpClient,
    store: Store,
    *,
    prober: AtsProber | None = None,
    limit: int | None = None,
) -> SnapshotStats:
    prober = prober or AtsProber(client)
    stats = SnapshotStats()
    run_id = store.start_run("job_snapshots")
    try:
        companies = store.companies_with_ats(limit)
        stats.seen = len(companies)
        log.info("snapshotting %d ATS companies", stats.seen)
        for company in companies:
            snapshot_one(prober, store, company, stats)
        store.finish_run(
            run_id, status="success", items_processed=stats.snapshotted, notes=stats.summary()
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("job_snapshots failed")
        store.finish_run(run_id, status="error", items_processed=stats.snapshotted, notes=repr(exc))
        raise
    log.info("snapshots done: %s", stats.summary())
    return stats
