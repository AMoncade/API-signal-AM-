"""Phase 2 orchestration: company name -> candidate board slugs -> matched ATS board.

There is NO website/domain step anymore (it was lossy and added nothing): slugs are
derived straight from the company NAME and probed across every supported provider.
Each hit is VERIFIED (by the board's own company name, or by requiring the token to
equal the full company name) before storing ats_provider + ats_token. Most filers
have no public board at all, which is expected. Logs to ingestion_runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.http_client import HttpClient
from worker.seeding.ats import AtsProber, accept_ats_match, board_tokens_from_name
from worker.store import Store

log = logging.getLogger("worker.seeding")


@dataclass(slots=True)
class SeedStats:
    seen: int = 0
    ats_matched: int = 0
    ats_rejected: int = 0      # probe hit but failed name verification (likely false positive)
    errors: int = 0            # per-company transient failures (network/DB), isolated

    def summary(self) -> str:
        ar = f"{self.ats_matched}/{self.seen}" if self.seen else "0/0"
        return f"seen={self.seen} ats_matched={ar} ats_rejected={self.ats_rejected} errors={self.errors}"


def seed_one(store: Store, prober: AtsProber, company, stats: SeedStats) -> None:
    tokens = board_tokens_from_name(company.entity_name)
    if not tokens:
        return
    hit = prober.probe_tokens(tokens)
    if not hit:
        return
    if not accept_ats_match(company.entity_name, hit):
        stats.ats_rejected += 1
        log.info(
            "rejected likely false-positive ATS match: %s !~ %s/%s (board=%r)",
            company.entity_name, hit.provider, hit.token, hit.company_name,
        )
        return
    store.set_ats(company.cik, hit.provider, hit.token)
    stats.ats_matched += 1
    log.info(
        "ATS hit: %s -> %s/%s (%d open)",
        company.entity_name, hit.provider, hit.token, hit.open_positions,
    )


def seed_companies(
    client: HttpClient,
    store: Store,
    *,
    prober: AtsProber | None = None,
    limit: int | None = None,
) -> SeedStats:
    prober = prober or AtsProber(client)
    stats = SeedStats()
    run_id = store.start_run("seeding")
    try:
        companies = store.companies_needing_seeding(limit)
        stats.seen = len(companies)
        log.info("seeding %d companies", stats.seen)
        for company in companies:
            try:
                seed_one(store, prober, company, stats)
            except Exception as exc:  # noqa: BLE001 - isolate one company's failure
                stats.errors += 1
                log.warning("seeding failed for %s: %s", company.cik, exc)
        store.finish_run(
            run_id, status="success", items_processed=stats.ats_matched, notes=stats.summary()
        )
    except Exception as exc:  # noqa: BLE001
        try:
            store.finish_run(run_id, status="error", items_processed=stats.ats_matched, notes=repr(exc))
        except Exception:  # noqa: BLE001 - never let run-logging mask the real error
            log.exception("could not log seeding run failure")
        raise
    log.info("seeding done: %s", stats.summary())
    return stats
