"""Read-only data access for the API - the ONE place that talks to Supabase for
reads. The signal JOINS live in SQL views (company_velocity, funded_and_hiring);
this layer READS those views and never re-implements the surge/join logic.

A ``Repository`` protocol lets the endpoints be tested against a fake without a
database; ``SupabaseRepository`` is the production implementation (lazy supabase
import, service-role key bypasses RLS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

_EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
_GREENHOUSE_BOARD = "https://boards.greenhouse.io/{token}"
_LEVER_BOARD = "https://jobs.lever.co/{token}"


def filing_index_url(cik: str, accession_number: str) -> str:
    """Provenance link to the EDGAR filing index (spec: every signal carries a source)."""
    folder = accession_number.replace("-", "")
    return f"{_EDGAR_ARCHIVES}/{int(cik)}/{folder}/{accession_number}-index.htm"


def board_url(provider: str | None, token: str | None) -> str | None:
    if not provider or not token:
        return None
    if provider == "greenhouse":
        return _GREENHOUSE_BOARD.format(token=token)
    if provider == "lever":
        return _LEVER_BOARD.format(token=token)
    return None


@dataclass(slots=True)
class Page:
    items: list[dict] = field(default_factory=list)
    has_more: bool = False


class Repository(Protocol):
    def pre_announced_funding(
        self, *, offset: int, limit: int, sector: str | None = None, filed_after: str | None = None
    ) -> Page: ...
    def surging_velocity(self, *, offset: int, limit: int, min_ratio: float | None = None) -> Page: ...
    def material_risks(
        self,
        *,
        offset: int,
        limit: int,
        severity: str | None = None,
        event_type: str | None = None,
        filed_after: str | None = None,
    ) -> Page: ...
    def funded_and_hiring(self, *, offset: int, limit: int) -> Page: ...
    def migrations_for_ciks(self, ciks: list[str]) -> dict[str, list[dict]]: ...


class SupabaseRepository:
    """Production repository. Reads tables + the signal views via the service role."""

    def __init__(self) -> None:
        self._sb = None

    @property
    def sb(self):
        if self._sb is None:
            from core.supabase_client import get_supabase  # lazy

            self._sb = get_supabase()
        return self._sb

    @staticmethod
    def _take(rows: list[dict], limit: int) -> Page:
        # Callers request limit+1 rows; >limit means there is a next page.
        return Page(items=rows[:limit], has_more=len(rows) > limit)

    def _company_map(self, ciks: list[str]) -> dict[str, dict]:
        if not ciks:
            return {}
        res = (
            self.sb.table("companies")
            .select("cik,entity_name,state_or_country,ats_provider,ats_token")
            .in_("cik", ciks)
            .execute()
        )
        return {r["cik"]: r for r in (res.data or [])}

    def pre_announced_funding(
        self, *, offset: int, limit: int, sector: str | None = None, filed_after: str | None = None
    ) -> Page:
        # Embed the FK'd company (entity_name) in one query.
        q = (
            self.sb.table("form_d_filings")
            .select("*, companies(entity_name, state_or_country)")
            .eq("is_pooled_fund", False)
            # Secondary key: daily-index rows share a midnight filed_at, so a unique
            # tie-breaker is required or offset pagination can skip/duplicate rows.
            .order("filed_at", desc=True)
            .order("accession_number", desc=True)
        )
        if sector:
            q = q.eq("industry_group", sector)
        if filed_after:
            q = q.gte("filed_at", filed_after)
        rows = (q.range(offset, offset + limit).execute().data) or []
        for r in rows:
            # to-one FK embed -> dict; harden against null / a future to-many (list).
            company = r.pop("companies", None)
            company = company[0] if isinstance(company, list) else (company or {})
            r["entity_name"] = company.get("entity_name")
            r["state_or_country"] = company.get("state_or_country")
            r["source_url"] = filing_index_url(r["cik"], r["accession_number"])
        return self._take(rows, limit)

    def surging_velocity(self, *, offset: int, limit: int, min_ratio: float | None = None) -> Page:
        q = (
            self.sb.table("company_velocity")
            .select("*")
            .eq("is_surging", True)
            .order("velocity_ratio", desc=True)
            .order("cik")  # unique tie-breaker for stable pagination
        )
        if min_ratio is not None:
            q = q.gte("velocity_ratio", min_ratio)
        rows = (q.range(offset, offset + limit).execute().data) or []
        companies = self._company_map([r["cik"] for r in rows])
        for r in rows:
            c = companies.get(r["cik"], {})
            r["entity_name"] = c.get("entity_name")
            r["ats_provider"] = c.get("ats_provider")
            r["source_url"] = board_url(c.get("ats_provider"), c.get("ats_token"))
        return self._take(rows, limit)

    def material_risks(
        self,
        *,
        offset: int,
        limit: int,
        severity: str | None = None,
        event_type: str | None = None,
        filed_after: str | None = None,
    ) -> Page:
        # eight_k_events stores entity_name + source_url directly (no FK to companies).
        q = (
            self.sb.table("eight_k_events")
            .select("*")
            .order("filed_at", desc=True)
            .order("accession_number", desc=True)  # unique tie-breaker
        )
        if severity:
            q = q.eq("severity", severity)
        if event_type:
            q = q.eq("event_type", event_type)
        if filed_after:
            q = q.gte("filed_at", filed_after)
        rows = (q.range(offset, offset + limit).execute().data) or []
        return self._take(rows, limit)

    def funded_and_hiring(self, *, offset: int, limit: int) -> Page:
        # READ the view; the join (funded in 90d AND surging) lives there.
        rows = (
            self.sb.table("funded_and_hiring")
            .select("*")
            .order("latest_filing_date", desc=True)
            .order("cik")  # unique tie-breaker for stable pagination
            .range(offset, offset + limit)
            .execute()
            .data
        ) or []
        return self._take(rows, limit)

    def migrations_for_ciks(self, ciks: list[str]) -> dict[str, list[dict]]:
        if not ciks:
            return {}
        res = (
            self.sb.table("company_tech_signals")
            .select("cik,software,signal_type,confidence,source_posting_url,detected_at")
            .in_("cik", ciks)
            .execute()
        )
        out: dict[str, list[dict]] = {}
        for row in res.data or []:
            out.setdefault(row["cik"], []).append(row)
        return out
