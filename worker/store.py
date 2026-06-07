"""Persistence for the worker - a thin data-access layer over the ONE shared
Supabase wrapper (``core.supabase_client.get_supabase``). This is NOT a parallel
Supabase client: it just maps domain records to the tables in /docs/schema.sql.

Two implementations:
  * ``SupabaseStore``  - production. Imports ``supabase`` lazily so the parser and
    the offline dry-run path don't require the package or any credentials.
  * ``InMemoryStore``  - no persistence. Powers ``python -m worker form_d --dry-run``
    (a credential-free demo) and the integration tests. Mirrors only the behaviour
    the worker depends on (idempotent accession PK, latest-prior lookup, run log).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol, runtime_checkable

from worker.form_d.models import CompanyRecord, FormDFilingRecord

# Velocity window/threshold. These MUST match the company_velocity view in
# /docs/schema.sql (the single source of truth); they are named here only so the
# offline InMemoryStore mirror cannot silently drift from the SQL definition.
VELOCITY_WINDOW_DAYS = 30
VELOCITY_SURGE_MULTIPLE = 2


@dataclass(frozen=True, slots=True)
class AtsCompany:
    """A hiring-trackable company: has a matched ATS board (Phase 3 input)."""

    cik: str
    entity_name: str
    ats_provider: str
    ats_token: str


@runtime_checkable
class Store(Protocol):
    """Everything the Phase 1 worker needs from storage."""

    def start_run(self, job_name: str) -> object: ...
    def finish_run(
        self, run_id: object, *, status: str, items_processed: int, notes: str | None = None
    ) -> None: ...

    def upsert_company(self, company: CompanyRecord) -> None: ...
    def filing_exists(self, accession_number: str) -> bool: ...
    def latest_amount_sold_for_cik(self, cik: str) -> Decimal | None: ...
    def insert_filing(self, filing: FormDFilingRecord) -> None: ...

    # --- Phase 2 (seeding) ---------------------------------------------------
    def companies_needing_seeding(self, limit: int | None = None) -> list[CompanyRecord]: ...
    def set_domain(self, cik: str, domain: str | None, status: str) -> None: ...
    def set_ats(self, cik: str, provider: str, token: str) -> None: ...
    # --- Phase 3 (snapshots + velocity) --------------------------------------
    def companies_with_ats(self, limit: int | None = None) -> list[AtsCompany]: ...
    def insert_snapshot(
        self,
        cik: str,
        open_positions: int,
        dept_counts: dict | None = None,
        snapshot_date: date | None = None,
    ) -> None: ...
    def surging_companies(self, limit: int | None = None) -> list[dict]: ...
    # --- Phase 4 (8-K) -------------------------------------------------------
    def eight_k_exists(self, accession_number: str) -> bool: ...
    def insert_eight_k_event(self, row: dict) -> None: ...
    # --- Phase 7 (migrations enrichment) -------------------------------------
    def insert_tech_signal(
        self,
        cik: str,
        software: str,
        confidence: float,
        source_posting_url: str | None = None,
        signal_type: str = "migration",
    ) -> None: ...


class SupabaseStore:
    """Production store. RLS is bypassed by the service-role key (see schema.sql)."""

    def __init__(self) -> None:
        self._sb = None  # built lazily on first use

    @property
    def sb(self):
        if self._sb is None:
            from core.supabase_client import get_supabase  # lazy: avoids hard dep offline

            self._sb = get_supabase()
        return self._sb

    # --- ingestion_runs ------------------------------------------------------
    def start_run(self, job_name: str) -> object:
        res = (
            self.sb.table("ingestion_runs")
            .insert({"job_name": job_name, "status": "running"})
            .execute()
        )
        return res.data[0]["id"]

    def finish_run(
        self, run_id: object, *, status: str, items_processed: int, notes: str | None = None
    ) -> None:
        (
            self.sb.table("ingestion_runs")
            .update(
                {
                    # ISO timestamp computed here: a literal "now()" string would be
                    # sent verbatim by PostgREST, not evaluated as the SQL function.
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "status": status,
                    "items_processed": items_processed,
                    "notes": notes,
                }
            )
            .eq("id", run_id)
            .execute()
        )

    # --- companies -----------------------------------------------------------
    def upsert_company(self, company: CompanyRecord) -> None:
        # on_conflict=cik: refresh the business fields, leave Phase-2 domain/ATS
        # columns and first_seen_at untouched (they are absent from the payload).
        self.sb.table("companies").upsert(company.to_row(), on_conflict="cik").execute()

    # --- form_d_filings ------------------------------------------------------
    def filing_exists(self, accession_number: str) -> bool:
        res = (
            self.sb.table("form_d_filings")
            .select("accession_number")
            .eq("accession_number", accession_number)
            .limit(1)
            .execute()
        )
        return bool(res.data)

    def latest_amount_sold_for_cik(self, cik: str) -> Decimal | None:
        res = (
            self.sb.table("form_d_filings")
            .select("total_amount_sold_usd")
            .eq("cik", cik)
            .not_.is_("total_amount_sold_usd", "null")
            # Secondary sort on accession_number: all filings from one daily index
            # share a midnight filed_at, so this makes "most recent prior"
            # deterministic and matches the (filed_at, accession) ingest order.
            .order("filed_at", desc=True)
            .order("accession_number", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        val = res.data[0]["total_amount_sold_usd"]
        return None if val is None else Decimal(str(val))

    def insert_filing(self, filing: FormDFilingRecord) -> None:
        self.sb.table("form_d_filings").insert(filing.to_row()).execute()

    # --- Phase 2 (seeding) ---------------------------------------------------
    def companies_needing_seeding(self, limit: int | None = None) -> list[CompanyRecord]:
        q = (
            self.sb.table("companies")
            .select("cik,entity_name,state_or_country,entity_type,industry_group")
            # Only companies NOT yet attempted (domain_status defaults to 'pending';
            # seeding sets it resolved/unresolved/failed). This makes repeated batches
            # advance instead of re-probing the same unmatched companies forever.
            .eq("domain_status", "pending")
            .order("first_seen_at", desc=False)
        )
        if limit is not None:
            q = q.limit(limit)
        return [self._to_company(r) for r in (q.execute().data or [])]

    def set_domain(self, cik: str, domain: str | None, status: str) -> None:
        (
            self.sb.table("companies")
            .update({"derived_domain": domain, "domain_status": status})
            .eq("cik", cik)
            .execute()
        )

    def set_ats(self, cik: str, provider: str, token: str) -> None:
        (
            self.sb.table("companies")
            .update({"ats_provider": provider, "ats_token": token})
            .eq("cik", cik)
            .execute()
        )

    # --- Phase 3 (snapshots + velocity) --------------------------------------
    def companies_with_ats(self, limit: int | None = None) -> list[AtsCompany]:
        q = (
            self.sb.table("companies")
            .select("cik,entity_name,ats_provider,ats_token")
            .not_.is_("ats_token", "null")
        )
        if limit is not None:
            q = q.limit(limit)
        return [
            AtsCompany(r["cik"], r["entity_name"], r["ats_provider"], r["ats_token"])
            for r in (q.execute().data or [])
        ]

    def insert_snapshot(
        self,
        cik: str,
        open_positions: int,
        dept_counts: dict | None = None,
        snapshot_date: date | None = None,
    ) -> None:
        # PK (cik, snapshot_date default current_date): upsert so a re-run on the
        # same day overwrites rather than erroring (one snapshot per company per day).
        row = {"cik": cik, "open_positions": open_positions, "dept_counts": dept_counts}
        if snapshot_date is not None:
            row["snapshot_date"] = snapshot_date.isoformat()
        self.sb.table("job_snapshots").upsert(row, on_conflict="cik,snapshot_date").execute()

    def surging_companies(self, limit: int | None = None) -> list[dict]:
        # Read the company_velocity VIEW (the single source of truth for the
        # surge logic per /docs/schema.sql). Never recompute it here.
        q = self.sb.table("company_velocity").select("*").eq("is_surging", True)
        if limit is not None:
            q = q.limit(limit)
        return q.execute().data or []

    # --- Phase 4 (8-K) -------------------------------------------------------
    def eight_k_exists(self, accession_number: str) -> bool:
        res = (
            self.sb.table("eight_k_events")
            .select("accession_number")
            .eq("accession_number", accession_number)
            .limit(1)
            .execute()
        )
        return bool(res.data)

    def insert_eight_k_event(self, row: dict) -> None:
        self.sb.table("eight_k_events").insert(row).execute()

    def insert_tech_signal(
        self,
        cik: str,
        software: str,
        confidence: float,
        source_posting_url: str | None = None,
        signal_type: str = "migration",
    ) -> None:
        # unique(cik, software, signal_type): upsert so repeated detections refresh
        # rather than error. Stores the signal only -- never the raw JD text.
        self.sb.table("company_tech_signals").upsert(
            {
                "cik": cik,
                "software": software,
                "signal_type": signal_type,
                "confidence": confidence,
                "source_posting_url": source_posting_url,
            },
            on_conflict="cik,software,signal_type",
        ).execute()

    @staticmethod
    def _to_company(row: dict) -> CompanyRecord:
        return CompanyRecord(
            cik=row["cik"],
            entity_name=row["entity_name"],
            state_or_country=row.get("state_or_country"),
            entity_type=row.get("entity_type"),
            industry_group=row.get("industry_group"),
        )


class InMemoryStore:
    """Non-persistent Store for the dry-run demo and tests. Not a database."""

    def __init__(self) -> None:
        self.companies: dict[str, CompanyRecord] = {}
        self.filings: dict[str, FormDFilingRecord] = {}
        self.runs: list[dict] = []
        self.domains: dict[str, tuple[str | None, str]] = {}     # cik -> (domain, status)
        self.ats: dict[str, tuple[str, str]] = {}                # cik -> (provider, token)
        self.snapshots: list[dict] = []                          # job_snapshots rows
        self.eight_k_events: dict[str, dict] = {}                # accession -> event row
        self.tech_signals: dict[tuple, dict] = {}                # (cik,software,type) -> row

    def start_run(self, job_name: str) -> object:
        run = {"id": len(self.runs), "job_name": job_name, "status": "running"}
        self.runs.append(run)
        return run["id"]

    def finish_run(
        self, run_id: object, *, status: str, items_processed: int, notes: str | None = None
    ) -> None:
        self.runs[int(run_id)].update(
            status=status, items_processed=items_processed, notes=notes
        )

    def upsert_company(self, company: CompanyRecord) -> None:
        self.companies[company.cik] = company

    def filing_exists(self, accession_number: str) -> bool:
        return accession_number in self.filings

    def latest_amount_sold_for_cik(self, cik: str) -> Decimal | None:
        rows = [f for f in self.filings.values() if f.cik == cik and f.total_amount_sold_usd is not None]
        if not rows:
            return None
        # Mirror production ordering: (filed_at, accession_number) descending.
        rows.sort(key=lambda f: (f.filed_at, f.accession_number), reverse=True)
        return rows[0].total_amount_sold_usd

    def insert_filing(self, filing: FormDFilingRecord) -> None:
        self.filings[filing.accession_number] = filing

    # --- Phase 2 (seeding) ---------------------------------------------------
    def companies_needing_seeding(self, limit: int | None = None) -> list[CompanyRecord]:
        # not-yet-attempted == no domain_status recorded yet (mirrors 'pending')
        out = [c for cik, c in self.companies.items() if cik not in self.domains]
        return out[:limit] if limit is not None else out

    def set_domain(self, cik: str, domain: str | None, status: str) -> None:
        self.domains[cik] = (domain, status)

    def set_ats(self, cik: str, provider: str, token: str) -> None:
        self.ats[cik] = (provider, token)

    # --- Phase 3 (snapshots + velocity) --------------------------------------
    def companies_with_ats(self, limit: int | None = None) -> list[AtsCompany]:
        out = [
            AtsCompany(cik, self.companies[cik].entity_name, provider, token)
            for cik, (provider, token) in self.ats.items()
            if cik in self.companies
        ]
        return out[:limit] if limit is not None else out

    def insert_snapshot(
        self,
        cik: str,
        open_positions: int,
        dept_counts: dict | None = None,
        snapshot_date: date | None = None,
    ) -> None:
        snap_date = snapshot_date or date.today()
        # one snapshot per company per day (PK): replace any existing same-day row
        self.snapshots = [
            s for s in self.snapshots
            if not (s["cik"] == cik and s["snapshot_date"] == snap_date)
        ]
        self.snapshots.append(
            {
                "cik": cik,
                "snapshot_date": snap_date,
                "open_positions": open_positions,
                "dept_counts": dept_counts,
            }
        )

    def company_velocity(self) -> list[dict]:
        """Offline MIRROR of the company_velocity view in /docs/schema.sql.

        Not production logic (the API/worker read the real SQL view via
        SupabaseStore); this exists so the no-DB integration check can confirm a
        fresh snapshot surfaces a company as surging exactly as the view would.
        """
        by_cik: dict[str, list[dict]] = {}
        for s in self.snapshots:
            by_cik.setdefault(s["cik"], []).append(s)
        out: list[dict] = []
        for cik, snaps in by_cik.items():
            snaps = sorted(snaps, key=lambda s: s["snapshot_date"])
            latest = snaps[-1]
            cutoff = latest["snapshot_date"] - timedelta(days=VELOCITY_WINDOW_DAYS)
            baselines = [s for s in snaps if s["snapshot_date"] <= cutoff]
            baseline = baselines[-1] if baselines else None
            base_open = baseline["open_positions"] if baseline else None
            current = latest["open_positions"]
            ratio = round(current / base_open, 2) if base_open else None
            is_surging = bool(base_open and current >= VELOCITY_SURGE_MULTIPLE * base_open)
            out.append(
                {
                    "cik": cik,
                    "latest_date": latest["snapshot_date"],
                    "current_open": current,
                    "baseline_open": base_open,
                    "baseline_date": baseline["snapshot_date"] if baseline else None,
                    "velocity_ratio": ratio,
                    "is_surging": is_surging,
                }
            )
        return out

    def surging_companies(self, limit: int | None = None) -> list[dict]:
        out = [v for v in self.company_velocity() if v["is_surging"]]
        return out[:limit] if limit is not None else out

    def funded_and_hiring(self, *, now: datetime | None = None) -> list[dict]:
        """Offline MIRROR of the funded_and_hiring view in /docs/schema.sql:
        companies with a Form D in the last 90 days AND currently surging on hiring.
        Used by the integration check (production reads the real SQL view)."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=90)
        surging = {v["cik"]: v for v in self.company_velocity() if v["is_surging"]}
        out: list[dict] = []
        for cik, vel in surging.items():
            recent = [
                f for f in self.filings.values()
                if f.cik == cik and f.filed_at >= cutoff
            ]
            if not recent or cik not in self.companies:
                continue
            recent.sort(key=lambda f: (f.filed_at, f.accession_number), reverse=True)
            latest = recent[0]
            domain, _status = self.domains.get(cik, (None, None))
            provider, _token = self.ats.get(cik, (None, None))
            out.append(
                {
                    "cik": cik,
                    "entity_name": self.companies[cik].entity_name,
                    "derived_domain": domain,
                    "ats_provider": provider,
                    "latest_filing_date": latest.filed_at,
                    "latest_amount_sold_usd": (
                        float(latest.total_amount_sold_usd)
                        if latest.total_amount_sold_usd is not None
                        else None
                    ),
                    "current_open": vel["current_open"],
                    "baseline_open": vel["baseline_open"],
                    "velocity_ratio": vel["velocity_ratio"],
                }
            )
        return out

    # --- Phase 4 (8-K) -------------------------------------------------------
    def eight_k_exists(self, accession_number: str) -> bool:
        return accession_number in self.eight_k_events

    def insert_eight_k_event(self, row: dict) -> None:
        self.eight_k_events[row["accession_number"]] = row

    def insert_tech_signal(
        self,
        cik: str,
        software: str,
        confidence: float,
        source_posting_url: str | None = None,
        signal_type: str = "migration",
    ) -> None:
        key = (cik, software, signal_type)
        self.tech_signals[key] = {
            "cik": cik,
            "software": software,
            "signal_type": signal_type,
            "confidence": confidence,
            "source_posting_url": source_posting_url,
        }
