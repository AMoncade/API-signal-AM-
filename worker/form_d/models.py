"""Row/record types for Form D, aligned 1:1 with /docs/schema.sql.

Three shapes:
  * ``ParsedFormD``        - everything extracted PURELY from a primary_doc.xml.
                             Business fields only; related-person PII is never
                             captured (HARD RULE #7) - only a count is kept.
  * ``CompanyRecord``      - the columns we write to ``companies``.
  * ``FormDFilingRecord``  - the columns we write to ``form_d_filings``.

Money is ``Decimal`` (exact). ``Indefinite`` offering amounts survive as the raw
string with a NULL numeric (HARD RULE #4 - never coerce to 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(slots=True)
class ParsedFormD:
    """The signal content of one primary_doc.xml. No accession_number / filed_at:
    those are not in the XML - they come from the EDGAR index (see ``FilingRef``)."""

    cik: str                                   # 10-digit zero-padded (canonical key)
    entity_name: str
    submission_type: str                       # 'D' | 'D/A' (XML <submissionType>)
    is_amendment: bool

    state_or_country: str | None               # issuerAddress/stateOrCountry (e.g. 'AL')
    entity_type: str | None                    # Corporation / LLC / Limited Partnership ...
    industry_group: str | None                 # offeringData/industryGroup/industryGroupType
    is_pooled_fund: bool                        # exclude from the "funded startup" universe

    total_offering_amount_raw: str | None       # verbatim - may be 'Indefinite'
    total_offering_amount_usd: Decimal | None    # NULL when not numeric
    total_amount_sold_usd: Decimal | None        # CUMULATIVE as filed (D/A restates it)

    date_of_first_sale: date | None              # NULL when "yet to occur"
    federal_exemption: str | None                # comma-joined items, e.g. '06b' or '06b,3C.7'

    related_person_count: int = 0                # PII names dropped - count only


@dataclass(slots=True)
class CompanyRecord:
    """Maps to ``companies``. Domain/ATS columns are NOT set here - they default to
    'pending'/NULL and are filled in Phase 2, and are deliberately left untouched on
    re-ingest so an upsert never clobbers a previously matched board token."""

    cik: str
    entity_name: str
    state_or_country: str | None = None
    entity_type: str | None = None
    industry_group: str | None = None

    def to_row(self) -> dict:
        return {
            "cik": self.cik,
            "entity_name": self.entity_name,
            "state_or_country": self.state_or_country,
            "entity_type": self.entity_type,
            "industry_group": self.industry_group,
        }


@dataclass(slots=True)
class FormDFilingRecord:
    """Maps to ``form_d_filings``. ``is_amendment`` is a generated column in the DB,
    so it is intentionally NOT written. ``incremental_amount_sold_usd`` is computed
    by the worker (this filing's cumulative minus the prior filing's, same cik)."""

    accession_number: str
    cik: str
    submission_type: str
    filed_at: datetime
    date_of_first_sale: date | None
    total_offering_amount_raw: str | None
    total_offering_amount_usd: Decimal | None
    total_amount_sold_usd: Decimal | None
    incremental_amount_sold_usd: Decimal | None
    industry_group: str | None
    is_pooled_fund: bool
    federal_exemption: str | None

    def to_row(self) -> dict:
        def num(d: Decimal | None):
            return None if d is None else float(d)

        return {
            "accession_number": self.accession_number,
            "cik": self.cik,
            "submission_type": self.submission_type,
            "filed_at": self.filed_at.isoformat(),
            "date_of_first_sale": self.date_of_first_sale.isoformat()
            if self.date_of_first_sale
            else None,
            "total_offering_amount_raw": self.total_offering_amount_raw,
            "total_offering_amount_usd": num(self.total_offering_amount_usd),
            "total_amount_sold_usd": num(self.total_amount_sold_usd),
            "incremental_amount_sold_usd": num(self.incremental_amount_sold_usd),
            "industry_group": self.industry_group,
            "is_pooled_fund": self.is_pooled_fund,
            "federal_exemption": self.federal_exemption,
        }


def compute_incremental_amount(
    current_cumulative: Decimal | None,
    prior_cumulative: Decimal | None,
) -> Decimal | None:
    """De-double-count the cumulative ``total_amount_sold`` (HARD RULE #3).

    ``total_amount_sold`` is cumulative for the offering; a D/A restates the running
    total. Per /docs/schema.sql the worker computes the incremental as *this filing's
    cumulative minus the most recent prior filing of the same cik*:

      * no prior filing  -> the whole cumulative is "new" money.
      * prior present     -> the delta since that prior filing.

    Returns NULL only when the current cumulative itself is non-numeric/unknown.
    (Grouping is by cik per the locked schema's design note; Form D carries no
    explicit offering id. A negative delta - a later, smaller, separate offering -
    is preserved as computed rather than masked.)
    """
    if current_cumulative is None:
        return None
    if prior_cumulative is None:
        return current_cumulative
    return current_cumulative - prior_cumulative
