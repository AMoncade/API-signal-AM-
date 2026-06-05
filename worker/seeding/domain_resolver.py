"""Derive a website domain from a Form D issuer's legal name + state.

Two backends behind one ``Resolver`` interface (phase2 prompt):
  * ``ApiDomainResolver``       - uses a search/enrichment service when both
    DOMAIN_RESOLVER_API_KEY and DOMAIN_RESOLVER_API_URL are configured. It queries
    the endpoint (``?name=<legal name>&country=<state>`` with a Bearer key) through
    the shared HttpClient and reads the best-guess domain out of the JSON. This is
    the higher-recall, more accurate path. Provider-agnostic: ``_extract`` accepts
    the common response shapes. On any failure (non-200, network, no domain in the
    body) it falls back to the heuristic so seeding is never blocked.
  * ``HeuristicDomainResolver`` - no key needed. Normalizes the name into candidate
    domains and keeps the first that actually RESOLVES in DNS. Lossy by design.

``get_resolver()`` returns the API backend when a key AND endpoint are configured,
else the heuristic.

Name normalization and DNS probing are both pure/injected so the logic is unit
testable without network: pass a ``dns_probe`` callable in tests; the API backend
is testable by injecting an HttpClient backed by an httpx.MockTransport.
"""

from __future__ import annotations

import logging
import re
import socket
from collections.abc import Callable
from typing import Protocol

from core.config import Settings, get_settings
from core.http_client import HttpClient

log = logging.getLogger("worker.seeding.domain_resolver")

# Legal-suffix tokens stripped before turning a company name into a domain guess.
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp",
    "corporation", "co", "company", "plc", "gmbh", "ag", "sa", "nv", "pllc",
}
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TLDS = (".com", ".io", ".co", ".ai")
# Pulls a bare registrable host out of whatever a provider returns: tolerates a
# scheme, a leading www., and a trailing path/query (e.g. "https://www.acme.io/x").
_DOMAIN_RE = re.compile(r"^(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9.-]*\.[a-z]{2,})", re.IGNORECASE)


def clean_domain(value: object) -> str | None:
    """Normalize a provider-returned value to a bare lowercase host, or None."""
    if not isinstance(value, str):
        return None
    m = _DOMAIN_RE.match(value.strip())
    if not m:
        return None
    return m.group(1).lower().rstrip(".") or None


class DomainResult:
    """Outcome of a derivation attempt."""

    __slots__ = ("domain", "status")

    def __init__(self, domain: str | None, status: str) -> None:
        # status is one of the companies.domain_status CHECK values.
        self.domain = domain
        self.status = status

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"DomainResult(domain={self.domain!r}, status={self.status!r})"


class Resolver(Protocol):
    def resolve(self, entity_name: str, state_or_country: str | None) -> DomainResult: ...


def normalize_name_tokens(entity_name: str) -> list[str]:
    """Lowercased alphanumeric word tokens with legal suffixes removed."""
    cleaned = _NON_ALNUM.sub(" ", entity_name.lower()).strip()
    # Drop single-char tokens: they are remnants of dotted legal forms
    # ("L.P." -> l p, "L.L.C." -> l l c), never meaningful for a domain guess.
    tokens = [t for t in cleaned.split() if len(t) >= 2 and t not in _LEGAL_SUFFIXES]
    return tokens


def candidate_domains(entity_name: str) -> list[str]:
    """Ordered, de-duplicated domain guesses for a company name.

    The bare concatenation of the significant words is the most common startup
    domain (e.g. "ClusterTruck, Inc." -> clustertruck.com); the first significant
    word is a frequent fallback. We try a few common TLDs.
    """
    tokens = normalize_name_tokens(entity_name)
    if not tokens:
        return []
    stems: list[str] = []
    joined = "".join(tokens)
    stems.append(joined)
    if tokens[0] != joined:
        stems.append(tokens[0])
    if len(tokens) > 1:
        stems.append("".join(tokens[:2]))
    out: list[str] = []
    seen: set[str] = set()
    for stem in stems:
        if len(stem) < 2:
            continue
        for tld in _TLDS:
            d = stem + tld
            if d not in seen:
                seen.add(d)
                out.append(d)
    return out


def _default_dns_probe(domain: str) -> bool:
    """True if the domain resolves in DNS (A/AAAA record)."""
    try:
        socket.getaddrinfo(domain, None)
        return True
    except OSError:
        return False


class HeuristicDomainResolver:
    """No-key fallback: name -> candidate domains -> first that resolves in DNS."""

    def __init__(self, dns_probe: Callable[[str], bool] = _default_dns_probe) -> None:
        self._dns_probe = dns_probe

    def resolve(self, entity_name: str, state_or_country: str | None) -> DomainResult:
        candidates = candidate_domains(entity_name)
        if not candidates:
            return DomainResult(None, "failed")
        for domain in candidates:
            if self._dns_probe(domain):
                return DomainResult(domain, "resolved")
        return DomainResult(None, "unresolved")


class ApiDomainResolver:
    """Resolver backed by a search/enrichment API.

    Issues a GET to ``endpoint`` (``?name=<legal name>&country=<state>`` with the
    key as a Bearer token) through the shared HttpClient and reads a domain out of
    the JSON. Provider-agnostic: ``_extract`` walks the common response shapes
    (top-level ``domain``/``website``/``url`` or nested under
    ``data``/``result``/``company``/``organization``). On any failure (no endpoint,
    non-200, network/JSON error, or no domain in the body) it falls back to the
    heuristic so seeding is never blocked.
    """

    # Response keys that may carry the domain, and containers it may nest under.
    _DOMAIN_KEYS = ("domain", "website", "url", "company_domain", "primary_domain")
    _CONTAINERS = ("data", "result", "company", "organization")

    def __init__(
        self,
        client: HttpClient,
        api_key: str,
        *,
        endpoint: str = "",
        fallback: Resolver | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._endpoint = endpoint
        self._fallback = fallback or HeuristicDomainResolver()

    @classmethod
    def _extract(cls, payload: object) -> str | None:
        """Find the first usable domain in a provider-agnostic JSON payload."""
        if isinstance(payload, dict):
            for key in cls._DOMAIN_KEYS:
                domain = clean_domain(payload.get(key))
                if domain:
                    return domain
            for container in cls._CONTAINERS:
                if container in payload:
                    domain = cls._extract(payload[container])
                    if domain:
                        return domain
        elif isinstance(payload, list):
            for item in payload:
                domain = cls._extract(item)
                if domain:
                    return domain
        return None

    def resolve(self, entity_name: str, state_or_country: str | None) -> DomainResult:
        if not self._endpoint:
            # Nothing to query (key set but no endpoint): use the heuristic.
            return self._fallback.resolve(entity_name, state_or_country)
        params = {"name": entity_name}
        if state_or_country:
            params["country"] = state_or_country
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        try:
            resp = self._client.get(self._endpoint, params=params, headers=headers)
            if resp.status_code != 200:
                log.warning(
                    "domain resolver API returned %s for %r; falling back to heuristic",
                    resp.status_code, entity_name,
                )
                return self._fallback.resolve(entity_name, state_or_country)
            domain = self._extract(resp.json())
        except Exception as exc:  # noqa: BLE001 - network/JSON error must never block seeding
            log.warning(
                "domain resolver API call failed for %r (%s); falling back to heuristic",
                entity_name, exc,
            )
            return self._fallback.resolve(entity_name, state_or_country)
        if domain:
            return DomainResult(domain, "resolved")
        # The API answered but offered no domain: try the heuristic for recall.
        return self._fallback.resolve(entity_name, state_or_country)


def get_resolver(settings: Settings | None = None, client: HttpClient | None = None) -> Resolver:
    settings = settings or get_settings()
    if settings.domain_resolver_api_key and settings.domain_resolver_api_url and client is not None:
        return ApiDomainResolver(
            client,
            settings.domain_resolver_api_key,
            endpoint=settings.domain_resolver_api_url,
        )
    return HeuristicDomainResolver()
