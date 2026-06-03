"""Discover new filings from the EDGAR daily index.

The daily ``master.<YYYYMMDD>.idx`` lists every filing accepted that day as
``CIK|Company Name|Form Type|Date Filed|File Name``. We filter it to the form
types a job cares about (e.g. {'D','D/A'} for Phase 1) and turn each row into a
``FilingRef`` carrying the primary-document URL plus the accession_number and
filed_at that the primary_doc.xml itself does NOT contain.

Pure parsing (``parse_master_idx``) is separated from the networked walk so it can
be unit-tested without hitting SEC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from core.http_client import HttpClient

_DAILY_INDEX_BASE = "https://www.sec.gov/Archives/edgar/daily-index"
_ARCHIVES = "https://www.sec.gov/Archives"
_ACCESSION_RE = re.compile(r"/([\d]{10}-\d{2}-\d{6})\.txt$")


@dataclass(frozen=True, slots=True)
class FilingRef:
    """A pointer to one filing's primary document, with the index-only metadata."""

    cik: str                       # as listed in the index (unpadded); URL uses this
    company_name: str
    submission_type: str           # 'D' | 'D/A' | '8-K' | ...
    filed_at: datetime             # midnight UTC of the index date
    accession_number: str          # e.g. 0001504410-26-000001
    primary_doc_url: str

    @property
    def cik10(self) -> str:
        return self.cik.zfill(10)


def _primary_doc_url(cik: str, accession_number: str) -> str:
    folder = accession_number.replace("-", "")
    return f"{_ARCHIVES}/edgar/data/{int(cik)}/{folder}/primary_doc.xml"


def parse_master_idx(text: str, *, forms: set[str]) -> list[FilingRef]:
    """Parse a ``master.<date>.idx`` body into FilingRefs for the requested forms.

    Pure: no network. Skips the header preamble and any malformed lines.
    """
    refs: list[FilingRef] = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 5:
            continue
        # CIK is field 0; Form Type / Date Filed / File Name are the LAST three.
        # The company NAME is everything in between -- SEC names can legitimately
        # contain '|' (e.g. "Smith | Jones, LLC"), so a strict ==5 split would
        # silently drop those filings. Reconstruct the name from the middle fields.
        cik = parts[0].strip()
        name = "|".join(parts[1:-3]).strip()
        form = parts[-3].strip()
        date_filed = parts[-2].strip()
        filename = parts[-1].strip()
        if form not in forms:
            continue
        m = _ACCESSION_RE.search(filename)
        if not m:
            continue
        accession = m.group(1)
        try:
            filed_at = datetime.fromisoformat(date_filed).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        refs.append(
            FilingRef(
                cik=cik,
                company_name=name,
                submission_type=form,
                filed_at=filed_at,
                accession_number=accession,
                primary_doc_url=_primary_doc_url(cik, accession),
            )
        )
    return refs


def _index_children(client: HttpClient, url: str, pattern: str) -> list[str]:
    resp = client.get(f"{url}/index.json")
    resp.raise_for_status()
    items = resp.json()["directory"]["item"]
    rx = re.compile(pattern)
    return sorted(name for name in (it["name"] for it in items) if rx.search(name))


def latest_index_date(client: HttpClient) -> str:
    """Walk the daily-index tree to the most recent ``master.<YYYYMMDD>.idx``.

    Returns the 8-digit date string. Used when no explicit date is given so the
    worker always picks up the freshest published index.
    """
    years = _index_children(client, _DAILY_INDEX_BASE, r"^\d{4}$")
    year = years[-1]
    qtrs = _index_children(client, f"{_DAILY_INDEX_BASE}/{year}", r"^QTR\d$")
    qtr = qtrs[-1]
    masters = _index_children(
        client, f"{_DAILY_INDEX_BASE}/{year}/{qtr}", r"^master\.\d{8}\.idx$"
    )
    if not masters:
        raise RuntimeError(f"no master idx files under {_DAILY_INDEX_BASE}/{year}/{qtr}")
    return masters[-1].split(".")[1]


def fetch_filing_refs(
    client: HttpClient,
    *,
    forms: set[str],
    date: str | None = None,
) -> list[FilingRef]:
    """Fetch the daily index for ``date`` (or the latest) and return matching refs.

    ``date`` is 'YYYYMMDD'. Refs come back sorted oldest-filed first, which lets the
    incremental-amount diff see prior filings of the same cik before later ones.
    """
    if date is None:
        date = latest_index_date(client)
    year = date[:4]
    qtr = f"QTR{(int(date[4:6]) - 1) // 3 + 1}"
    url = f"{_DAILY_INDEX_BASE}/{year}/{qtr}/master.{date}.idx"
    resp = client.get(url)
    resp.raise_for_status()
    refs = parse_master_idx(resp.text, forms=forms)
    refs.sort(key=lambda r: (r.filed_at, r.accession_number))
    return refs
