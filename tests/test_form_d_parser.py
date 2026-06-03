"""Phase 1 - parser tests against REAL saved Form D fixtures (CLAUDE.md DoD:
assert against real filings with known correct amounts, not mocks of our own
assumptions). Fixtures were downloaded from EDGAR; the asserted numbers match the
live filings (cross-checked at the source URLs noted per case).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from worker.form_d.models import compute_incremental_amount
from worker.form_d.parser import parse_amount, parse_form_d

FIXTURES = Path(__file__).parent / "fixtures" / "form_d"


def _load(accession: str) -> str:
    return (FIXTURES / f"{accession}.xml").read_text(encoding="utf-8")


# --- a clean numeric operating-company filing --------------------------------
# https://www.sec.gov/Archives/edgar/data/1369790/000136979026000004/primary_doc.xml
def test_feb_bancshares_numeric_filing() -> None:
    p = parse_form_d(_load("0001369790-26-000004"))
    assert p.cik == "0001369790"
    assert p.entity_name == "FEB BANCSHARES INC"
    assert p.submission_type == "D"
    assert p.is_amendment is False
    assert p.state_or_country == "AL"
    assert p.entity_type == "Corporation"
    assert p.industry_group == "Commercial Banking"
    assert p.is_pooled_fund is False
    assert p.total_offering_amount_raw == "7500000"
    assert p.total_offering_amount_usd == Decimal("7500000")
    assert p.total_amount_sold_usd == Decimal("1700000")
    assert p.date_of_first_sale == date(2026, 5, 22)
    assert p.federal_exemption == "06b"


# --- a real venture-backed tech startup (the target universe) ----------------
# https://www.sec.gov/Archives/edgar/data/1643138/000164313826000003/primary_doc.xml
def test_clustertruck_tech_startup() -> None:
    p = parse_form_d(_load("0001643138-26-000003"))
    assert p.entity_name == "ClusterTruck, Inc."
    assert p.industry_group == "Other Technology"
    assert p.is_pooled_fund is False
    assert p.total_offering_amount_usd == Decimal("1000000")
    assert p.total_amount_sold_usd == Decimal("425521")
    assert p.date_of_first_sale == date(2026, 5, 20)


# --- "Indefinite" offering + pooled-fund detection ---------------------------
# https://www.sec.gov/Archives/edgar/data/1455085/000145508526000001/primary_doc.xml
def test_icapital_indefinite_and_pooled() -> None:
    p = parse_form_d(_load("0001455085-26-000001"))
    assert p.entity_name == "iCapital Multi-Strategy Fund, L.P."
    assert p.submission_type == "D/A"
    assert p.is_amendment is True
    assert p.is_pooled_fund is True                      # HARD RULE #5
    assert p.industry_group == "Pooled Investment Fund"
    # HARD RULE #4: "Indefinite" survives verbatim and the numeric column is NULL.
    assert p.total_offering_amount_raw == "Indefinite"
    assert p.total_offering_amount_usd is None
    assert p.total_amount_sold_usd == Decimal("545061156")


# --- PII rule: business names only, never personal names (HARD RULE #7) -------
def test_no_personal_names_anywhere_in_parsed_output() -> None:
    # This filing's related persons / signer include these real personal names.
    p = parse_form_d(_load("0001369790-26-000004"))
    leaked = ("Slawson", "Stinson", "Steve Smith", "Guice", "William")
    blob = " ".join(str(v) for v in asdict(p).values())
    for name in leaked:
        assert name not in blob, f"personal name {name!r} leaked into parsed output"
    # We still keep a non-PII count of related persons for optional use.
    assert p.related_person_count >= 1


# --- amount parsing edge cases -----------------------------------------------
@pytest.mark.parametrize(
    ("raw", "exp_raw", "exp_num"),
    [
        ("1000000", "1000000", Decimal("1000000")),
        ("Indefinite", "Indefinite", None),
        ("indefinite", "indefinite", None),
        ("0", "0", Decimal("0")),            # sold=0 must NOT become NULL
        ("$1,250,000", "$1,250,000", Decimal("1250000")),
        ("", None, None),
        (None, None, None),
    ],
)
def test_parse_amount_edge_cases(raw, exp_raw, exp_num) -> None:
    got_raw, got_num = parse_amount(raw)
    assert got_raw == exp_raw
    assert got_num == exp_num


# --- incremental diff on a REAL same-issuer D/A chain ------------------------
# Secured Income Fund-II, LLC. (cik 1504410), two consecutive D/A filings:
#   2025-05-22  total_amount_sold = 274,233,604  (prior)
#   2026-06-02  total_amount_sold = 300,392,879  (current)
def test_incremental_amount_from_real_da_chain() -> None:
    prior = parse_form_d(_load("0001504410-25-000003"))
    current = parse_form_d(_load("0001504410-26-000001"))
    assert prior.total_amount_sold_usd == Decimal("274233604")
    assert current.total_amount_sold_usd == Decimal("300392879")
    # Cumulative restated by the amendment; the genuine new money is the delta.
    inc = compute_incremental_amount(
        current.total_amount_sold_usd, prior.total_amount_sold_usd
    )
    assert inc == Decimal("26159275")
    # First filing of a cik (no prior) -> whole cumulative is "new".
    assert compute_incremental_amount(prior.total_amount_sold_usd, None) == Decimal("274233604")


def test_rejects_non_form_d_xml() -> None:
    with pytest.raises(ValueError):
        parse_form_d("<edgarSubmission><submissionType>8-K</submissionType></edgarSubmission>")


def test_rejects_missing_or_zero_cik() -> None:
    # primaryIssuer present but CIK absent / all-zeros -> reject (canonical key).
    with pytest.raises(ValueError):
        parse_form_d(
            "<edgarSubmission><submissionType>D</submissionType>"
            "<primaryIssuer><entityName>X Co</entityName></primaryIssuer></edgarSubmission>"
        )
    with pytest.raises(ValueError):
        parse_form_d(
            "<edgarSubmission><submissionType>D</submissionType>"
            "<primaryIssuer><cik>0000000000</cik><entityName>X Co</entityName>"
            "</primaryIssuer></edgarSubmission>"
        )


def test_rejects_missing_entity_name() -> None:
    with pytest.raises(ValueError):
        parse_form_d(
            "<edgarSubmission><submissionType>D</submissionType>"
            "<primaryIssuer><cik>0000001234</cik></primaryIssuer></edgarSubmission>"
        )
