"""Probe public ATS job boards for a board token, across several providers.

Auth: NONE. These GET endpoints are the *intended* public consumption surface
(companies use them to power their own careers pages) - that is what keeps this
legally clean (spec §2/§8). All requests go through the shared rate-limited
HttpClient. We read COUNTS ONLY and never store raw descriptions (spec §6).

Providers (all public, no-auth, JSON), verified live:
  greenhouse, lever, ashby, smartrecruiters, recruitee, workable, breezy.
A hit = HTTP 200 with a non-empty postings list. Matches are then VERIFIED (by the
board's own company name when the API exposes one, else by requiring the token to
equal the issuer's full name) to cut coincidental collisions. Shared by Phase 2
(seeding) and Phase 3 (daily snapshots).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from core.http_client import HttpClient

# --- name normalization (lives here so seeding has no domain-derivation dep) ----
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp",
    "corporation", "co", "company", "plc", "gmbh", "ag", "sa", "nv", "pllc",
}
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Generic / filler words that, alone, do not identify a company. Used to reduce a
# name to its DISTINCTIVE stem when verifying a match (so "Apex Tech Growth
# Partners" doesn't match an unrelated "Apex" board).
_GENERIC_NAME_WORDS = {
    "capital", "partners", "partner", "holdings", "holding", "group", "ventures",
    "venture", "fund", "funds", "management", "advisors", "advisers", "financial",
    "services", "service", "international", "company", "trust", "associates",
    "equity", "investments", "investment", "global", "real", "estate", "properties",
    "property", "income", "opportunities", "opportunity", "strategic", "strategies",
    "strategy", "markets", "market", "technologies", "technology", "tech", "growth",
    "solutions", "systems", "labs", "lab", "national", "american", "first", "united",
}


def normalize_name_tokens(entity_name: str) -> list[str]:
    """Lowercased alphanumeric word tokens with legal suffixes removed."""
    cleaned = _NON_ALNUM.sub(" ", (entity_name or "").lower()).strip()
    # Drop single-char tokens (remnants of dotted legal forms like "L.P." -> l p).
    return [t for t in cleaned.split() if len(t) >= 2 and t not in _LEGAL_SUFFIXES]


@dataclass(frozen=True, slots=True)
class AtsHit:
    provider: str
    token: str
    open_positions: int
    dept_counts: dict[str, int] | None = None
    company_name: str | None = None   # board's own name, when the API exposes one


def _dept_name(value) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return value.get("name") or value.get("label")
    return None


def _dept_counts(values: list) -> dict[str, int] | None:
    counts: dict[str, int] = {}
    for v in values:
        name = _dept_name(v)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts or None


# Each parser maps a decoded JSON body to (open_positions, company_name|None,
# [dept values]) or None when the board is missing/empty.
def _parse_greenhouse(data):
    if not isinstance(data, dict) or not data.get("jobs"):
        return None
    jobs = data["jobs"]
    total = (data.get("meta") or {}).get("total")
    open_ = int(total) if isinstance(total, int) else len(jobs)
    name = jobs[0].get("company_name") if isinstance(jobs[0], dict) else None
    depts = [d for job in jobs for d in (job.get("departments") or [])]
    return open_, name, depts


def _parse_lever(data):
    if not isinstance(data, list) or not data:
        return None
    return len(data), None, [(p.get("categories") or {}).get("team") for p in data]


def _parse_ashby(data):
    if not isinstance(data, dict):
        return None
    jobs = [j for j in (data.get("jobs") or []) if j.get("isListed", True)]
    if not jobs:
        return None
    return len(jobs), None, [j.get("department") for j in jobs]


def _parse_smartrecruiters(data):
    if not isinstance(data, dict):
        return None
    total = data.get("totalFound")
    if not total:
        return None
    content = data.get("content") or []
    name = (content[0].get("company") or {}).get("name") if content else None
    return int(total), name, [c.get("department") for c in content]


def _parse_recruitee(data):
    if not isinstance(data, dict) or not data.get("offers"):
        return None
    offers = data["offers"]
    name = offers[0].get("company_name")
    return len(offers), name, [o.get("department") for o in offers]


def _parse_workable(data):
    if not isinstance(data, dict) or not data.get("jobs"):
        return None
    jobs = data["jobs"]
    return len(jobs), data.get("name"), [j.get("department") for j in jobs]


def _parse_breezy(data):
    if not isinstance(data, list) or not data:
        return None
    name = (data[0].get("company") or {}).get("name") if isinstance(data[0], dict) else None
    return len(data), name, [item.get("department") for item in data]


# provider name -> (url builder, parser). Order = probe priority.
_PROVIDERS: dict[str, tuple[Callable[[str], str], Callable]] = {
    "greenhouse": (lambda t: f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs", _parse_greenhouse),
    "lever": (lambda t: f"https://api.lever.co/v0/postings/{t}?mode=json", _parse_lever),
    "ashby": (lambda t: f"https://api.ashbyhq.com/posting-api/job-board/{t}", _parse_ashby),
    "smartrecruiters": (lambda t: f"https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=100", _parse_smartrecruiters),
    "recruitee": (lambda t: f"https://{t}.recruitee.com/api/offers/", _parse_recruitee),
    "workable": (lambda t: f"https://apply.workable.com/api/v1/widget/accounts/{t}", _parse_workable),
    "breezy": (lambda t: f"https://{t}.breezy.hr/json", _parse_breezy),
}


class AtsProber:
    """Existence/count probe across all supported ATS providers."""

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def _get_json(self, url: str):
        try:
            resp = self._client.get(url)
        except Exception:  # noqa: BLE001 - network hiccup => treat as miss
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def probe(self, provider: str, token: str) -> AtsHit | None:
        """Probe ONE provider for a token (used by Phase 3 snapshots)."""
        spec = _PROVIDERS.get(provider)
        if spec is None:
            return None
        url_fn, parse = spec
        parsed = parse(self._get_json(url_fn(token)))
        if not parsed:
            return None
        open_, name, depts = parsed
        if open_ <= 0:
            return None
        return AtsHit(provider, token, open_, _dept_counts(depts), company_name=name)

    # Back-compat helpers used by tests / Phase 3.
    def probe_greenhouse(self, token: str) -> AtsHit | None:
        return self.probe("greenhouse", token)

    def probe_lever(self, token: str) -> AtsHit | None:
        return self.probe("lever", token)

    def probe_tokens(self, tokens: list[str]) -> AtsHit | None:
        """Try each token across every provider; first non-empty board wins."""
        for token in tokens:
            for provider in _PROVIDERS:
                hit = self.probe(provider, token)
                if hit:
                    return hit
        return None


def board_tokens_from_name(entity_name: str) -> list[str]:
    """Candidate board slugs (>=4 chars) derived from the company NAME only:
    the concatenated significant words, the bare first word when distinctive
    (>=5 chars), and the first two words joined. Every hit is still verified."""
    toks = normalize_name_tokens(entity_name)
    out: list[str] = []
    seen: set[str] = set()

    def add(tok: str | None, min_len: int = 4) -> None:
        if tok and len(tok) >= min_len and tok not in seen:
            seen.add(tok)
            out.append(tok)

    if toks:
        add("".join(toks))
        add(toks[0], min_len=5)
        if len(toks) >= 2:
            add("".join(toks[:2]))
    return out


def _significant_tokens(name: str) -> list[str]:
    """Distinctive name words: legal suffixes stripped, then generic words removed."""
    return [t for t in normalize_name_tokens(name) if len(t) >= 3 and t not in _GENERIC_NAME_WORDS]


def names_agree(form_d_name: str, board_name: str) -> bool:
    """True when the FULL distinctive stems match. A shared leading word is NOT
    enough ("Huntress Wealth Co." must not match a board named "Huntress"); generic
    words are stripped, so "Mercury Technologies" still reduces to {mercury}."""
    fa = _significant_tokens(form_d_name)
    ba = _significant_tokens(board_name)
    if not fa or not ba:
        return False
    return "".join(fa) == "".join(ba)


def token_is_name_justified(token: str, name: str) -> bool:
    """For providers with no company-name field (lever, ashby): the token must equal
    the issuer's FULL name concatenation. A bare leading word or distinctive stem is
    NOT enough (a fund "Apex Tech Growth Partners" -> stem "apex" would otherwise
    match an unrelated board)."""
    return token == "".join(normalize_name_tokens(name)) and len(token) >= 4


def accept_ats_match(form_d_name: str, hit: AtsHit) -> bool:
    """Verification gate applied AFTER a probe hit, to reject coincidental matches.
    Use the board's own name when the provider exposes one; otherwise require the
    token to equal the full company name."""
    if hit.company_name:
        return names_agree(form_d_name, hit.company_name)
    return token_is_name_justified(hit.token, form_d_name)
