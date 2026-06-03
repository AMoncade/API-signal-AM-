"""Phase 1 - EDGAR daily-index parsing (pure; no network)."""

from __future__ import annotations

from datetime import datetime, timezone

from worker.edgar.daily_index import parse_master_idx

# A trimmed real-shaped master.<date>.idx body: preamble, header, then rows.
SAMPLE = """\
Description:           Master Index of EDGAR Dissemination Feed
Last Data Received:    June 02, 2026
CIK|Company Name|Form Type|Date Filed|File Name
--------------------------------------------------------------------------------
1369790|FEB BANCSHARES INC|D|2026-06-02|edgar/data/1369790/0001369790-26-000004.txt
1504410|Secured Income Fund-II, LLC.|D/A|2026-06-02|edgar/data/1504410/0001504410-26-000001.txt
320193|Apple Inc.|8-K|2026-06-02|edgar/data/320193/0000320193-26-000050.txt
1643138|ClusterTruck, Inc.|D|2026-06-01|edgar/data/1643138/0001643138-26-000003.txt
9999999|Smith | Jones Ventures, LLC|D|2026-06-02|edgar/data/9999999/0009999999-26-000001.txt
garbage line with no pipes
"""


def test_parse_master_idx_filters_to_requested_forms() -> None:
    refs = parse_master_idx(SAMPLE, forms={"D", "D/A"})
    accs = {r.accession_number for r in refs}
    assert accs == {
        "0001369790-26-000004",
        "0001504410-26-000001",
        "0001643138-26-000003",
        "0009999999-26-000001",
    }
    # the 8-K and the malformed line are excluded
    assert all(r.submission_type in {"D", "D/A"} for r in refs)


def test_parse_master_idx_builds_primary_doc_url_and_filed_at() -> None:
    [feb] = [r for r in parse_master_idx(SAMPLE, forms={"D"}) if r.cik == "1369790"]
    assert feb.submission_type == "D"
    assert feb.company_name == "FEB BANCSHARES INC"
    assert feb.filed_at == datetime(2026, 6, 2, tzinfo=timezone.utc)
    assert feb.cik10 == "0001369790"
    assert feb.primary_doc_url == (
        "https://www.sec.gov/Archives/edgar/data/1369790/"
        "000136979026000004/primary_doc.xml"
    )


def test_parse_master_idx_only_8k() -> None:
    refs = parse_master_idx(SAMPLE, forms={"8-K"})
    assert [r.accession_number for r in refs] == ["0000320193-26-000050"]


def test_parse_master_idx_keeps_company_names_containing_pipes() -> None:
    # SEC names can legitimately contain '|'; a strict 5-field split would drop them.
    refs = parse_master_idx(SAMPLE, forms={"D", "D/A"})
    [pipey] = [r for r in refs if r.cik == "9999999"]
    assert pipey.company_name == "Smith | Jones Ventures, LLC"
    assert pipey.accession_number == "0009999999-26-000001"
