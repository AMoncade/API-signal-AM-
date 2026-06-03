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

GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_POSTINGS_URL = "https://api.lever.co/v0/postings/{token}?mode=json"


@dataclass(frozen=True, slots=True)
class AtsHit:
    provider: str            # 'greenhouse' | 'lever'
    token: str
    open_positions: int
    dept_counts: dict[str, int] | None = None


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
        return AtsHit("greenhouse", token, open_positions, _dept_counts([d for d in departments if d]))

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
    """Candidate board slugs: the bare second-level domain plus a couple of variants,
    and the concatenated significant name words (a common slug even off-domain)."""
    out: list[str] = []
    seen: set[str] = set()

    def add(tok: str | None) -> None:
        if tok and len(tok) >= 2 and tok not in seen:
            seen.add(tok)
            out.append(tok)

    if domain:
        sld = domain.split(".")[0]
        add(sld)
        add(sld.replace("-", ""))
    if entity_name_tokens:
        add("".join(entity_name_tokens))
        if entity_name_tokens:
            add(entity_name_tokens[0])
    return out
