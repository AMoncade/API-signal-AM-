"""Phase 2 orchestration: companies -> derived domain -> ATS board token.

For each company not yet hiring-trackable (no ats_token): derive a domain (HARD
RULE #6), record derived_domain + domain_status, then probe Greenhouse/Lever with
candidate tokens built from the domain and the normalized name. On a hit, store
ats_provider + ats_token. Derivation failure is expected for a meaningful fraction
of filers; we count outcomes so the hit rate is visible. Logs to ingestion_runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.config import get_settings
from core.http_client import HttpClient
from worker.seeding.ats import AtsProber, board_tokens_from_domain
from worker.seeding.domain_resolver import Resolver, get_resolver, normalize_name_tokens
from worker.store import Store

log = logging.getLogger("worker.seeding")


@dataclass(slots=True)
class SeedStats:
    seen: int = 0
    domains_resolved: int = 0
    domains_failed: int = 0
    ats_matched: int = 0

    def summary(self) -> str:
        dr = f"{self.domains_resolved}/{self.seen}" if self.seen else "0/0"
        ar = f"{self.ats_matched}/{self.seen}" if self.seen else "0/0"
        return (
            f"seen={self.seen} domains_resolved={dr} domains_failed={self.domains_failed} "
            f"ats_matched={ar}"
        )


def seed_one(store: Store, prober: AtsProber, resolver: Resolver, company, stats: SeedStats) -> None:
    res = resolver.resolve(company.entity_name, company.state_or_country)
    store.set_domain(company.cik, res.domain, res.status)
    if res.status == "resolved":
        stats.domains_resolved += 1
    else:
        stats.domains_failed += 1

    name_tokens = normalize_name_tokens(company.entity_name)
    tokens = board_tokens_from_domain(res.domain, name_tokens)
    if not tokens:
        return
    hit = prober.probe_tokens(tokens)
    if hit:
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
    resolver: Resolver | None = None,
    prober: AtsProber | None = None,
    limit: int | None = None,
) -> SeedStats:
    resolver = resolver or get_resolver(get_settings(), client)
    prober = prober or AtsProber(client)
    stats = SeedStats()
    run_id = store.start_run("seeding")
    try:
        companies = store.companies_needing_seeding(limit)
        stats.seen = len(companies)
        log.info("seeding %d companies", stats.seen)
        for company in companies:
            seed_one(store, prober, resolver, company, stats)
        store.finish_run(
            run_id, status="success", items_processed=stats.ats_matched, notes=stats.summary()
        )
    except Exception as exc:  # noqa: BLE001
        store.finish_run(run_id, status="error", items_processed=stats.ats_matched, notes=repr(exc))
        raise
    log.info("seeding done: %s", stats.summary())
    return stats
