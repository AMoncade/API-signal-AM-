"""Pydantic response models for the signal endpoints.

Lenient by design (``extra='ignore'``) so DB/view rows map cleanly even as columns
evolve; every signal carries a source URL where one exists (spec provenance rule).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Migration(_Row):
    software: str
    signal_type: str | None = None
    confidence: float | None = None
    source_posting_url: str | None = None
    detected_at: datetime | None = None


class FundingSignal(_Row):
    cik: str
    entity_name: str | None = None
    state_or_country: str | None = None
    derived_domain: str | None = None
    industry_group: str | None = None
    submission_type: str | None = None
    filed_at: datetime | None = None
    date_of_first_sale: date | None = None
    total_offering_amount_raw: str | None = None
    total_offering_amount_usd: float | None = None
    total_amount_sold_usd: float | None = None
    incremental_amount_sold_usd: float | None = None
    federal_exemption: str | None = None
    source_url: str | None = None
    migrations: list[Migration] = []


class VelocitySignal(_Row):
    cik: str
    entity_name: str | None = None
    derived_domain: str | None = None
    ats_provider: str | None = None
    latest_date: date | None = None
    current_open: int | None = None
    baseline_open: int | None = None
    baseline_date: date | None = None
    velocity_ratio: float | None = None
    is_surging: bool | None = None
    source_url: str | None = None


class RiskSignal(_Row):
    cik: str
    entity_name: str | None = None
    filed_at: datetime | None = None
    item_codes: list[str] = []
    event_type: str
    severity: str
    is_abrupt: bool | None = None
    affected_role: str | None = None
    summary: str | None = None
    confidence: float | None = None
    source_url: str | None = None


class FundedAndHiringSignal(_Row):
    cik: str
    entity_name: str | None = None
    derived_domain: str | None = None
    ats_provider: str | None = None
    latest_filing_date: datetime | None = None
    latest_amount_sold_usd: float | None = None
    current_open: int | None = None
    baseline_open: int | None = None
    velocity_ratio: float | None = None
    migrations: list[Migration] = []


class Paginated(BaseModel, Generic[T]):
    page: int
    page_size: int
    count: int = Field(description="number of items in THIS page (not the total matching rows)")
    has_more: bool
    items: list[T]
