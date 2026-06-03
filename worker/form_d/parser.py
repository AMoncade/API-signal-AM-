"""Pure XML parser for Form D ``primary_doc.xml`` (HARD RULE #2 - no LLM).

Paths below were CONFIRMED against live filings downloaded from EDGAR (see
tests/fixtures/form_d/), not taken on faith from spec Appendix A. Notable
realities the appendix glosses over:

  * The root ``<edgarSubmission>`` has NO XML namespace - plain tag paths work.
  * ``dateOfFirstSale`` is nested under ``offeringData/typeOfFiling`` (NOT directly
    under offeringData), and may be ``<yetToOccur>true</yetToOccur>`` with no value.
  * ``isPooledInvestmentFundType`` lives under ``typesOfSecuritiesOffered``; pooled
    status is also implied by ``industryGroupType == "Pooled Investment Fund"`` -
    we treat EITHER as pooled (HARD RULE #5).
  * ``federalExemptionsExclusions`` can contain multiple ``<item>`` elements.
  * ``totalOfferingAmount`` is frequently the literal string ``"Indefinite"``.

This module performs NO I/O and NO PII capture (related-person names are counted,
never stored - HARD RULE #7).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from worker.form_d.models import ParsedFormD

_INDEFINITE = "indefinite"
_POOLED_INDUSTRY = "Pooled Investment Fund"


def _findtext(node: ET.Element, path: str) -> str | None:
    """Return stripped text at ``path`` relative to ``node``, or None if absent/empty."""
    if node is None:
        return None
    el = node.find(path)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def parse_amount(raw: str | None) -> tuple[str | None, Decimal | None]:
    """Split an offering/sold amount into (raw_string, numeric_or_None).

    ``"Indefinite"`` (and any non-numeric value) keeps its raw string and yields a
    NULL numeric - never coerced to 0 (HARD RULE #4). Empty/missing yields (None, None).
    """
    if raw is None:
        return None, None
    raw = raw.strip()
    if not raw:
        return None, None
    if raw.lower() == _INDEFINITE:
        return raw, None
    cleaned = raw.replace(",", "").replace("$", "").strip()
    try:
        return raw, Decimal(cleaned)
    except (InvalidOperation, ValueError):
        # Genuinely non-numeric (defensive): preserve raw, leave numeric NULL.
        return raw, None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def parse_form_d(xml: str | bytes) -> ParsedFormD:
    """Parse one Form D / D-A primary document into a ``ParsedFormD`` signal row.

    Raises ``ValueError`` if the document is not a Form D submission (no
    ``primaryIssuer``) so callers can skip junk rather than store garbage.
    """
    root = ET.fromstring(xml)

    issuer = root.find("primaryIssuer")
    if issuer is None:
        raise ValueError("not a Form D submission: missing <primaryIssuer>")

    offering = root.find("offeringData")

    # CIK is the canonical join key (CLAUDE.md): reject junk rather than store an
    # all-zeros key that would pollute companies/form_d_filings. Same for a blank
    # legal name (companies.entity_name is NOT NULL and an upsert must not blank it).
    raw_cik = _findtext(issuer, "cik")
    if not raw_cik or not raw_cik.strip("0"):
        raise ValueError("Form D missing or zero CIK")
    cik = raw_cik.zfill(10)
    entity_name = _findtext(issuer, "entityName")
    if not entity_name:
        raise ValueError("Form D missing entityName")
    submission_type = _findtext(root, "submissionType") or ""

    industry_group = (
        _findtext(offering, "industryGroup/industryGroupType") if offering is not None else None
    )
    pooled_flag = (
        _is_true(_findtext(offering, "typesOfSecuritiesOffered/isPooledInvestmentFundType"))
        if offering is not None
        else False
    )
    is_pooled = pooled_flag or industry_group == _POOLED_INDUSTRY

    offering_raw, offering_usd = parse_amount(
        _findtext(offering, "offeringSalesAmounts/totalOfferingAmount") if offering is not None else None
    )
    _, sold_usd = parse_amount(
        _findtext(offering, "offeringSalesAmounts/totalAmountSold") if offering is not None else None
    )

    # date of first sale: offeringData/typeOfFiling/dateOfFirstSale/{value|yetToOccur}
    first_sale: date | None = None
    if offering is not None:
        first_sale = _parse_date(_findtext(offering, "typeOfFiling/dateOfFirstSale/value"))

    federal_items: list[str] = []
    if offering is not None:
        for item in offering.findall("federalExemptionsExclusions/item"):
            if item.text and item.text.strip():
                federal_items.append(item.text.strip())
    federal_exemption = ",".join(federal_items) or None

    # Match the persisted contract: the DB column is GENERATED ALWAYS AS
    # (submission_type = 'D/A'), and we never write is_amendment ourselves, so the
    # parsed flag is defined the same way (a true amendment is always filed as D/A).
    is_amendment = submission_type == "D/A"

    related = root.find("relatedPersonsList")
    related_count = len(related.findall("relatedPersonInfo")) if related is not None else 0

    return ParsedFormD(
        cik=cik,
        entity_name=entity_name,
        submission_type=submission_type,
        is_amendment=is_amendment,
        state_or_country=_findtext(issuer, "issuerAddress/stateOrCountry"),
        entity_type=_findtext(issuer, "entityType"),
        industry_group=industry_group,
        is_pooled_fund=is_pooled,
        total_offering_amount_raw=offering_raw,
        total_offering_amount_usd=offering_usd,
        total_amount_sold_usd=sold_usd,
        date_of_first_sale=first_sale,
        federal_exemption=federal_exemption,
        related_person_count=related_count,
    )
