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
from worker.seeding.ats import AtsProber, accept_ats_match, board_tokens_from_domain
from worker.seeding.domain_resolver import Resolver, get_resolver, normalize_name_tokens
from worker.store import Store

log = logging.getLogger("worker.seeding")


@dataclass(slots=True)
class SeedStats:
    seen: int = 0
    domains_resolved: int = 0
    domains_failed: int = 0
    ats_matched: int = 0
    ats_rejected: int = 0      # probe hit but failed name verification (likely false positive)
    errors: int = 0            # per-company transient failures (network/DNS/DB), isolated

    def summary(self) -> str:
        dr = f"{self.domains_resolved}/{self.seen}" if self.seen else "0/0"
        ar = f"{self.ats_matched}/{self.seen}" if self.seen else "0/0"
        return (
            f"seen={self.seen} domains_resolved={dr} domains_failed={self.domains_failed} "
            f"ats_matched={ar} ats_rejected={self.ats_rejected} errors={self.errors}"
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
    if not hit:
        return
    if not accept_ats_match(company.entity_name, hit, res.domain):
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
            try:
                seed_one(store, prober, resolver, company, stats)
            except Exception as exc:  # noqa: BLE001 - one company's transient failure
                # (DNS overload, network blip, DB write) must not abort the batch.
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
