"""Worker entry point: ``python -m worker [job] [options]``.

Jobs:
  (none)            Phase 0 wiring check - loads config + the shared HTTP client.
  form_d            Phase 1 - ingest Form D / D-A filings from the EDGAR daily index.
  seed              Phase 2 - derive domains + match Greenhouse/Lever board tokens.

Options for ``form_d``:
  --dry-run         Parse + classify but DO NOT persist (no Supabase needed). Prints
                    the extracted signal rows - a credential-free way to see it work.
  --date YYYYMMDD   Use a specific daily index (default: the latest published).
  --limit N         Process at most N filings (handy for the dry-run demo).

Options for ``seed``:
  --dry-run         Ingest a slice of Form D into memory, then derive domains and
                    probe ATS boards LIVE, reporting hit rates (no Supabase needed).
  --limit N         Cap how many companies to seed.

The schedule that runs these nightly arrives in Phase 6. EDGAR's global <=10 req/s
cap and the SEC_USER_AGENT header are enforced by the one shared HttpClient.
"""

from __future__ import annotations

import argparse
import logging
import sys

from core.config import get_settings
from core.http_client import HttpClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("worker")


def _scaffold_check() -> None:
    settings = get_settings()
    log.info(
        "worker scaffold (SEC_USER_AGENT configured: %s)", bool(settings.sec_user_agent)
    )
    with HttpClient(settings):
        log.info(
            "shared rate-limited HTTP client ready (global cap=%.1f req/s).",
            settings.http_max_requests_per_second,
        )


def _run_form_d(args: argparse.Namespace) -> None:
    from worker.form_d.ingest import ingest_form_d
    from worker.store import InMemoryStore, SupabaseStore

    store = InMemoryStore() if args.dry_run else SupabaseStore()
    mode = "DRY-RUN (no persistence)" if args.dry_run else "writing to Supabase"
    log.info("Phase 1 Form D ingest - %s", mode)

    with HttpClient() as client:
        stats = ingest_form_d(client, store, date=args.date, limit=args.limit)

    print("\n=== Form D ingest summary ===")
    print(stats.summary())
    if args.dry_run:
        print(f"\nCompanies that would be stored ({len(store.companies)}):")
        for cik, c in list(store.companies.items())[:25]:
            f = next((x for x in store.filings.values() if x.cik == cik), None)
            sold = f.total_amount_sold_usd if f else None
            offering = f.total_offering_amount_raw if f else None
            print(
                f"  {cik}  {c.entity_name[:38]:38}  {(c.industry_group or '')[:22]:22}"
                f"  offering={offering}  sold={sold}"
            )


def _run_seed(args: argparse.Namespace) -> None:
    from worker.form_d.ingest import ingest_form_d
    from worker.seeding.seed import seed_companies
    from worker.store import InMemoryStore, SupabaseStore

    if args.dry_run:
        store = InMemoryStore()
        log.info("Phase 2 seeding - DRY-RUN: ingest a Form D slice into memory, then probe ATS live")
        with HttpClient() as client:
            ingest_form_d(client, store, limit=args.limit)
            stats = seed_companies(client, store, limit=args.limit)
        print("\n=== Seeding summary (dry-run) ===")
        print(stats.summary())
        print(f"\nHiring-trackable matches ({len(store.ats)}):")
        for cik, (provider, token) in store.ats.items():
            print(f"  {cik}  {store.companies[cik].entity_name[:34]:34}  {provider}/{token}")
    else:
        store = SupabaseStore()
        log.info("Phase 2 seeding - writing to Supabase")
        with HttpClient() as client:
            stats = seed_companies(client, store, limit=args.limit)
        print("\n=== Seeding summary ===")
        print(stats.summary())


def _run_snapshot(args: argparse.Namespace) -> None:
    from worker.form_d.ingest import ingest_form_d
    from worker.seeding.seed import seed_companies
    from worker.snapshots.snapshot import snapshot_jobs
    from worker.store import InMemoryStore, SupabaseStore

    if args.dry_run:
        store = InMemoryStore()
        log.info("Phase 3 snapshots - DRY-RUN: ingest -> seed -> snapshot, all in memory + live ATS")
        with HttpClient() as client:
            ingest_form_d(client, store, limit=args.limit)
            seed_companies(client, store, limit=args.limit)
            stats = snapshot_jobs(client, store)
        print("\n=== Snapshot summary (dry-run) ===")
        print(stats.summary())
        for s in store.snapshots:
            print(f"  {s['cik']}  {store.companies[s['cik']].entity_name[:34]:34}  open={s['open_positions']}")
    else:
        store = SupabaseStore()
        log.info("Phase 3 snapshots - writing to Supabase")
        with HttpClient() as client:
            stats = snapshot_jobs(client, store)
        print("\n=== Snapshot summary ===")
        print(stats.summary())


def _run_eight_k(args: argparse.Namespace) -> None:
    if args.dry_run:
        from worker.eight_k.ingest import preview_event_bearing

        log.info("Phase 4 8-K - DRY-RUN: deterministic Item-code classification (no LLM call)")
        with HttpClient() as client:
            rows = preview_event_bearing(client, date=args.date, limit=args.limit)
        print(f"\n=== Event-bearing 8-Ks (deterministic, would be sent to the LLM): {len(rows)} ===")
        for r in rows:
            print(
                f"  {r['accession']}  {r['entity'][:32]:32}  items={','.join(r['item_codes'])}"
                f"  -> {r['event_type']} ({r['default_severity']})"
            )
        return

    from worker.eight_k.classifier import AnthropicClassifier
    from worker.eight_k.ingest import ingest_eight_k
    from worker.store import SupabaseStore

    store = SupabaseStore()
    with HttpClient() as client:
        classifier = AnthropicClassifier(client)   # requires ANTHROPIC_API_KEY (HARD RULE #9)
        log.info("Phase 4 8-K - classifying with hosted model, writing to Supabase")
        stats = ingest_eight_k(client, store, classifier, date=args.date, limit=args.limit)
    print("\n=== 8-K ingest summary ===")
    print(stats.summary())


def _run_migrations(args: argparse.Namespace) -> None:
    from worker.migrations.extract import KeywordExtractor
    from worker.migrations.ingest import enrich_migrations, fetch_postings_text
    from worker.store import InMemoryStore, SupabaseStore

    if args.dry_run:
        # Credential-free demo over a couple of sample JDs (no ATS fetch, no DB).
        samples = [
            ("0000000001", "https://example/job/1",
             "We are migrating off Salesforce to HubSpot and replacing our legacy CRM."),
            ("0000000002", "https://example/job/2",
             "Backend engineer, Python and distributed systems. No migration language here."),
        ]
        store = InMemoryStore()
        log.info("Phase 7 migrations - DRY-RUN over %d sample postings (no LLM/DB)", len(samples))
        stats = enrich_migrations(store, samples, extractor=KeywordExtractor())
        print("\n=== Migrations enrichment (dry-run) ===")
        print(stats.summary())
        for r in store.tech_signals.values():
            print(f"  {r['cik']}  {r['software']:14}  conf={r['confidence']}  ({r['signal_type']})")
        return

    store = SupabaseStore()
    log.info("Phase 7 migrations - fetching ATS content, extracting, writing to Supabase")
    with HttpClient() as client:
        def all_postings():
            for company in store.companies_with_ats(args.limit):
                yield from fetch_postings_text(client, company)
        stats = enrich_migrations(store, all_postings())
    print("\n=== Migrations enrichment ===")
    print(stats.summary())


def _build_classifier(client):
    """An AnthropicClassifier when a key is set, else None (8-K is then skipped)."""
    if not get_settings().anthropic_api_key:
        return None
    from worker.eight_k.classifier import AnthropicClassifier

    return AnthropicClassifier(client)


def _run_all(args: argparse.Namespace) -> None:
    from worker.pipeline import run_all
    from worker.store import SupabaseStore

    store = SupabaseStore()
    log.info("Running the full nightly pipeline once")
    with HttpClient() as client:
        run_all(client, store, date=args.date, limit=args.limit, classifier=_build_classifier(client))


def _run_schedule(args: argparse.Namespace) -> None:
    import time
    from datetime import datetime, timedelta, timezone

    from worker.pipeline import run_all
    from worker.store import SupabaseStore

    store = SupabaseStore()
    log.info("Scheduler started: nightly pipeline at %02d:00 UTC", args.hour)
    while True:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=args.hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        log.info("next run at %s UTC (%.0f min)", target.isoformat(), wait / 60)
        time.sleep(wait)
        try:
            with HttpClient() as client:
                run_all(client, store, limit=args.limit, classifier=_build_classifier(client))
        except Exception:  # noqa: BLE001 - never let one bad night kill the scheduler
            log.exception("nightly pipeline raised; will retry tomorrow")


def main() -> None:
    parser = argparse.ArgumentParser(prog="worker")
    sub = parser.add_subparsers(dest="job")

    p_formd = sub.add_parser("form_d", help="Phase 1: ingest Form D / D-A filings")
    p_formd.add_argument("--dry-run", action="store_true", help="parse only, no DB writes")
    p_formd.add_argument("--date", default=None, help="daily index date YYYYMMDD")
    p_formd.add_argument("--limit", type=int, default=None, help="max filings to process")

    p_seed = sub.add_parser("seed", help="Phase 2: derive domains + match ATS boards")
    p_seed.add_argument("--dry-run", action="store_true", help="in-memory demo, probe ATS live")
    p_seed.add_argument("--limit", type=int, default=None, help="max companies to seed")

    p_snap = sub.add_parser("snapshot", help="Phase 3: snapshot open postings per ATS company")
    p_snap.add_argument("--dry-run", action="store_true", help="ingest+seed+snapshot in memory")
    p_snap.add_argument("--limit", type=int, default=None, help="max companies")

    p_8k = sub.add_parser("eight_k", help="Phase 4: classify material-event 8-K filings")
    p_8k.add_argument("--dry-run", action="store_true", help="deterministic Item-code preview, no LLM")
    p_8k.add_argument("--date", default=None, help="daily index date YYYYMMDD")
    p_8k.add_argument("--limit", type=int, default=None, help="max filings to scan")

    p_mig = sub.add_parser("migrations", help="Phase 7: JD migration-hint enrichment")
    p_mig.add_argument("--dry-run", action="store_true", help="run over sample JDs, no ATS/DB")
    p_mig.add_argument("--limit", type=int, default=None, help="max ATS companies")

    p_all = sub.add_parser("run-all", help="Phase 6: run the full nightly pipeline once")
    p_all.add_argument("--date", default=None, help="daily index date YYYYMMDD")
    p_all.add_argument("--limit", type=int, default=None)

    p_sched = sub.add_parser("schedule", help="Phase 6: run the nightly pipeline daily, forever")
    p_sched.add_argument("--hour", type=int, default=6, help="UTC hour to run (default 06:00)")
    p_sched.add_argument("--limit", type=int, default=None)

    args = parser.parse_args()

    if args.job == "form_d":
        _run_form_d(args)
    elif args.job == "seed":
        _run_seed(args)
    elif args.job == "snapshot":
        _run_snapshot(args)
    elif args.job == "eight_k":
        _run_eight_k(args)
    elif args.job == "migrations":
        _run_migrations(args)
    elif args.job == "run-all":
        _run_all(args)
    elif args.job == "schedule":
        _run_schedule(args)
    else:
        _scaffold_check()
        log.info("no job specified - pass a job, e.g. `python -m worker form_d --dry-run`.")


if __name__ == "__main__":
    sys.exit(main())
