"""The five read-only signal endpoints (Phase 5).

Each is paginated (small default page size), filterable where it makes sense, and
carries a source URL per record. The JOIN/surge logic is NOT here - those routes
read the funded_and_hiring / company_velocity SQL views. ``migrations`` is an
ENRICHMENT field on company-centric records (funding, funded-and-hiring), never a
flagship route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import get_repository
from api.repository import Page, Repository
from api.schemas import (
    FundedAndHiringSignal,
    FundingSignal,
    Paginated,
    RiskSignal,
    VelocitySignal,
)

router = APIRouter(prefix="/signals", tags=["signals"])

_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100


def _envelope(page: int, page_size: int, pg: Page) -> dict:
    return {
        "page": page,
        "page_size": page_size,
        "count": len(pg.items),
        "has_more": pg.has_more,
        "items": pg.items,
    }


def _attach_migrations(repo: Repository, rows: list[dict]) -> None:
    by_cik = repo.migrations_for_ciks([r["cik"] for r in rows])
    for r in rows:
        r["migrations"] = by_cik.get(r["cik"], [])


@router.get("/pre-announced-funding", response_model=Paginated[FundingSignal])
def pre_announced_funding(
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    sector: str | None = Query(None, description="filter by industry_group"),
    filed_after: str | None = Query(None, description="ISO date/datetime lower bound"),
    repo: Repository = Depends(get_repository),
):
    pg = repo.pre_announced_funding(
        offset=(page - 1) * page_size, limit=page_size, sector=sector, filed_after=filed_after
    )
    _attach_migrations(repo, pg.items)
    return _envelope(page, page_size, pg)


@router.get("/surging-velocity", response_model=Paginated[VelocitySignal])
def surging_velocity(
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    min_ratio: float | None = Query(None, ge=1.0, description="minimum velocity ratio"),
    repo: Repository = Depends(get_repository),
):
    pg = repo.surging_velocity(offset=(page - 1) * page_size, limit=page_size, min_ratio=min_ratio)
    return _envelope(page, page_size, pg)


@router.get("/material-risks", response_model=Paginated[RiskSignal])
def material_risks(
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    severity: str | None = Query(None, description="low|medium|high|critical"),
    event_type: str | None = Query(None),
    filed_after: str | None = Query(None),
    repo: Repository = Depends(get_repository),
):
    pg = repo.material_risks(
        offset=(page - 1) * page_size,
        limit=page_size,
        severity=severity,
        event_type=event_type,
        filed_after=filed_after,
    )
    return _envelope(page, page_size, pg)


@router.get("/funded-and-hiring", response_model=Paginated[FundedAndHiringSignal])
def funded_and_hiring(
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    repo: Repository = Depends(get_repository),
):
    pg = repo.funded_and_hiring(offset=(page - 1) * page_size, limit=page_size)
    _attach_migrations(repo, pg.items)
    return _envelope(page, page_size, pg)
