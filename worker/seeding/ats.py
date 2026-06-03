"""Probe public ATS job boards (Greenhouse + Lever) for a board token.

Auth: NONE. These GET endpoints are the *intended* public consumption surface
(companies use them to power their own careers pages) - that is what keeps this
legally clean (spec §2/§8). All requests go through the shared rate-limited
HttpClient. We read COUNTS ONLY and never store raw descriptions (HARD RULE / spec
§6) - so the probe deliberately does not request ``content=true``.

A 200 with a non-empty postings list = a hit (store ats_provider + ats_token).
404 / empty = a miss. Shared by Phase 2 (seeding) and Phase 3 (daily snapshots).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.http_client import HttpClient
from worker.seeding.domain_resolver import normalize_name_tokens

# Generic / filler words that, alone, do not identify a company. Used to reduce a
# name to its DISTINCTIVE stem when verifying a board match (cuts false positives
# where only a common prefix like "Apex" or "Summit" coincides).
_GENERIC_NAME_WORDS = {
    "capital", "partners", "partner", "holdings", "holding", "group", "ventures",
    "venture", "fund", "funds", "management", "advisors", "advisers", "financial",
    "services", "service", "international", "company", "trust", "associates",
    "equity", "investments", "investment", "global", "real", "estate", "properties",
    "property", "income", "opportunities", "opportunity", "strategic", "strategies",
    "strategy", "markets", "market", "technologies", "technology", "tech", "growth",
    "solutions", "systems", "labs", "lab", "national", "american", "first", "united",
}

GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_POSTINGS_URL = "https://api.lever.co/v0/postings/{token}?mode=json"


@dataclass(frozen=True, slots=True)
class AtsHit:
    provider: str            # 'greenhouse' | 'lever'
    token: str
    open_positions: int
    dept_counts: dict[str, int] | None = None
    company_name: str | None = None   # board's own name (Greenhouse only) for verification


def _dept_counts(departments: list[str]) -> dict[str, int] | None:
    if not departments:
        return None
    counts: dict[str, int] = {}
    for name in departments:
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts or None


class AtsProber:
    """Existence/count probe for Greenhouse and Lever boards."""

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

    def probe_greenhouse(self, token: str) -> AtsHit | None:
        data = self._get_json(GREENHOUSE_JOBS_URL.format(token=token))
        if not isinstance(data, dict):
            return None
        jobs = data.get("jobs")
        if not jobs:
            return None
        total = (data.get("meta") or {}).get("total")
        open_positions = int(total) if isinstance(total, int) else len(jobs)
        departments = [
            (d or {}).get("name")
            for job in jobs
            for d in (job.get("departments") or [])
        ]
        # Greenhouse echoes the board owner's display name on each job; use it to
        # verify the match belongs to the right company.
        company_name = jobs[0].get("company_name") if isinstance(jobs[0], dict) else None
        return AtsHit(
            "greenhouse", token, open_positions,
            _dept_counts([d for d in departments if d]), company_name=company_name,
        )

    def probe_lever(self, token: str) -> AtsHit | None:
        data = self._get_json(LEVER_POSTINGS_URL.format(token=token))
        if not isinstance(data, list) or not data:
            return None
        departments = [(p.get("categories") or {}).get("team") for p in data]
        return AtsHit("lever", token, len(data), _dept_counts([d for d in departments if d]))

    def probe_tokens(self, tokens: list[str]) -> AtsHit | None:
        """Try each candidate token on Greenhouse then Lever; first hit wins."""
        for token in tokens:
            hit = self.probe_greenhouse(token)
            if hit:
                return hit
        for token in tokens:
            hit = self.probe_lever(token)
            if hit:
                return hit
        return None


def board_tokens_from_domain(domain: str | None, entity_name_tokens: list[str] | None = None) -> list[str]:
    """Candidate board slugs (>=4 chars): the second-level domain + a no-hyphen
    variant, the concatenated name words, and the bare first word ONLY when it is
    long enough (>=5) to be distinctive. Short generic first words (e.g. "apex")
    are NOT probed -- the #1 source of false positives. A hit is still verified."""
    out: list[str] = []
    seen: set[str] = set()

    def add(tok: str | None, min_len: int = 4) -> None:
        if tok and len(tok) >= min_len and tok not in seen:
            seen.add(tok)
            out.append(tok)

    if domain:
        sld = domain.split(".")[0]
        add(sld)
        add(sld.replace("-", ""))
    if entity_name_tokens:
        add("".join(entity_name_tokens))
        add(entity_name_tokens[0], min_len=5)   # first word only if distinctive
    return out


def _significant_tokens(name: str) -> list[str]:
    """Distinctive name words: legal suffixes already stripped by normalize, then
    generic/filler words removed and very short words dropped."""
    return [t for t in normalize_name_tokens(name) if len(t) >= 3 and t not in _GENERIC_NAME_WORDS]


def names_agree(form_d_name: str, board_name: str) -> bool:
    """True if a board's own name plausibly identifies the Form D issuer.

    Accept when the distinctive stems are EQUAL, or the board name is a single
    distinctive word (>=5 chars) equal to the issuer's first distinctive word.
    A short shared prefix alone (e.g. "Apex" vs "Apex Fintech") does NOT match.
    """
    fa = _significant_tokens(form_d_name)
    ba = _significant_tokens(board_name)
    if not fa or not ba:
        return False
    if "".join(fa) == "".join(ba):
        return True
    if len(ba) == 1 and ba[0] == fa[0] and len(ba[0]) >= 5:
        return True
    return False


def token_is_name_justified(token: str, name: str, domain: str | None = None) -> bool:
    """Lever has no company-name endpoint, so verify the TOKEN itself is a strong
    derivation of the issuer name: the full concatenation, the domain SLD, or the
    leading distinctive word (>=5 chars)."""
    toks = normalize_name_tokens(name)
    concat = "".join(toks)
    if token == concat and len(token) >= 4:
        return True
    if domain and token == domain.split(".")[0] and len(token) >= 5:
        return True
    sig = _significant_tokens(name)
    return bool(sig) and token == sig[0] and len(token) >= 5


def accept_ats_match(form_d_name: str, hit: AtsHit, domain: str | None = None) -> bool:
    """Verification gate applied AFTER a probe hit, to reject coincidental matches."""
    if hit.provider == "greenhouse" and hit.company_name:
        return names_agree(form_d_name, hit.company_name)
    return token_is_name_justified(hit.token, form_d_name, domain)
