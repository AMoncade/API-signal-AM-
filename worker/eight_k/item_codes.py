"""Deterministic 8-K Item-code handling (free, no LLM - HARD RULE #8).

The full-submission SGML header lists each item as a standardized 'ITEM
INFORMATION:' TITLE line (confirmed against live filings - it carries NO numeric
codes). Those official titles map 1:1 to item numbers, so we recover the codes
from the titles, then map the codes to an eventType + default severity per
Appendix B. Cross-checked against the submissions-API numeric `items` field on a
real multi-item filing (Bed Bath & Beyond 8-K = 1.03,3.03,5.02,5.03,7.01,9.01).
"""

from __future__ import annotations

import re

# Distinctive lowercased title PREFIXES -> item code. Prefixes are chosen so they
# match regardless of singular/plural wording in the header (e.g. "material
# impairment" is a prefix of "Material Impairments").
_TITLE_PREFIX_TO_CODE: list[tuple[str, str]] = [
    ("entry into a material definitive agreement", "1.01"),
    ("termination of a material definitive agreement", "1.02"),
    ("bankruptcy or receivership", "1.03"),
    ("mine safety", "1.04"),
    ("material cybersecurity incident", "1.05"),
    ("completion of acquisition or disposition of assets", "2.01"),
    ("results of operations and financial condition", "2.02"),
    ("creation of a direct financial obligation", "2.03"),
    ("triggering events that accelerate or increase a direct financial obligation", "2.04"),
    ("costs associated with exit or disposal activities", "2.05"),
    ("material impairment", "2.06"),
    ("notice of delisting or failure to satisfy a continued listing rule", "3.01"),
    ("unregistered sales of equity securities", "3.02"),
    ("material modification", "3.03"),  # matches "Material Modifications to Rights..."
    ("changes in registrant", "4.01"),  # "Changes in Registrant's Certifying Accountant"
    ("non-reliance on previously issued financial statements", "4.02"),
    ("changes in control of registrant", "5.01"),
    ("departure of directors or certain officers", "5.02"),
    ("amendments to articles of incorporation or bylaws", "5.03"),
    ("temporary suspension of trading", "5.04"),
    ("amendment to registrant", "5.05"),  # "...Code of Ethics"
    ("change in shell company status", "5.06"),
    ("submission of matters to a vote of security holders", "5.07"),
    ("shareholder director nominations", "5.08"),
    ("regulation fd disclosure", "7.01"),
    ("other events", "8.01"),
    ("financial statements and exhibits", "9.01"),
]

# Item code -> (eventType, default severity). ORDER = priority (most material
# first); used to pick the headline event for a multi-item filing. Items absent
# here are NOT event-bearing for the material-risks signal (e.g. 2.02 earnings,
# 5.07 votes, 7.01 RegFD, 9.01 exhibits) and do not by themselves trigger a row.
ITEM_CODE_TO_EVENT: dict[str, tuple[str, str]] = {
    "1.03": ("bankruptcy", "critical"),
    "4.02": ("restatement", "critical"),
    "2.06": ("impairment", "high"),
    "2.05": ("restructuring_layoffs", "high"),
    "2.04": ("debt_acceleration", "high"),
    "3.01": ("delisting_risk", "high"),
    "1.05": ("cyber_incident", "high"),
    "5.02": ("exec_departure", "medium"),
    "4.01": ("auditor_change", "medium"),
    "1.02": ("contract_change", "low"),
    "1.01": ("contract_change", "low"),
    "8.01": ("other", "low"),
}

_ITEM_INFO_RE = re.compile(r"ITEM INFORMATION:\s*(.+)")
_WS_RE = re.compile(r"\s+")


def _normalize(title: str) -> str:
    return _WS_RE.sub(" ", title).strip().lower()


def title_to_code(title: str) -> str | None:
    """Map one standardized item title to its numeric code (longest prefix wins)."""
    norm = _normalize(title)
    best: tuple[int, str] | None = None
    for prefix, code in _TITLE_PREFIX_TO_CODE:
        if norm.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), code)
    return best[1] if best else None


def parse_item_titles(header_text: str) -> list[str]:
    """Extract the 'ITEM INFORMATION:' title lines from a full-submission header."""
    return [m.group(1).strip() for m in _ITEM_INFO_RE.finditer(header_text)]


def item_codes_from_header(header_text: str) -> list[str]:
    """Ordered, de-duplicated numeric item codes parsed from the header titles."""
    codes: list[str] = []
    for title in parse_item_titles(header_text):
        code = title_to_code(title)
        if code and code not in codes:
            codes.append(code)
    return codes


def headline_event(item_codes: list[str]) -> tuple[str, str] | None:
    """Pick the most material event-bearing item -> (eventType, default_severity).

    Returns None when the filing has no event-bearing item (so the worker skips it
    and never pays for an LLM call on routine 8-Ks).
    """
    for code, mapping in ITEM_CODE_TO_EVENT.items():  # dict order = priority
        if code in item_codes:
            return mapping
    return None
