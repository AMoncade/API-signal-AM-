"""Phase 4 - Item-code extraction from REAL 8-K SGML headers + code->event mapping.

The BBBY case is cross-checked against the submissions-API numeric `items` field
("1.03,3.03,5.02,5.03,7.01,9.01"), proving the title->code recovery is correct.
"""

from __future__ import annotations

from pathlib import Path

from worker.eight_k.item_codes import (
    headline_event,
    item_codes_from_header,
    title_to_code,
)

EK = Path(__file__).parent / "fixtures" / "eight_k"


def _hdr(accession: str) -> str:
    return (EK / f"{accession}.hdr.txt").read_text(encoding="utf-8")


def test_title_to_code_examples() -> None:
    assert title_to_code("Bankruptcy or Receivership") == "1.03"
    assert title_to_code("Results of Operations and Financial Condition") == "2.02"
    assert title_to_code(
        "Departure of Directors or Certain Officers; Election of Directors; "
        "Appointment of Certain Officers: Compensatory Arrangements of Certain Officers"
    ) == "5.02"
    # plural in the header still matches the singular prefix
    assert title_to_code("Material Modifications to Rights of Security Holders") == "3.03"
    assert title_to_code("Material Impairments") == "2.06"
    assert title_to_code("Financial Statements and Exhibits") == "9.01"
    assert title_to_code("Some Unknown Future Item") is None


def test_bbby_bankruptcy_codes_match_submissions_api() -> None:
    codes = item_codes_from_header(_hdr("0001193125-23-247428"))
    # Exactly the numeric `items` the submissions API reports for this filing.
    assert codes == ["1.03", "3.03", "5.02", "5.03", "7.01", "9.01"]
    assert headline_event(codes) == ("bankruptcy", "critical")   # most material item wins


def test_mattel_is_exec_departure() -> None:
    codes = item_codes_from_header(_hdr("0000063276-26-000013"))
    assert codes == ["5.02", "5.07", "9.01"]
    assert headline_event(codes) == ("exec_departure", "medium")


def test_donaldson_earnings_is_not_event_bearing() -> None:
    codes = item_codes_from_header(_hdr("0000029644-26-000049"))
    assert codes == ["2.02", "9.01"]
    # 2.02 (earnings) + 9.01 (exhibits) are not material-risk events -> skip the filing.
    assert headline_event(codes) is None
