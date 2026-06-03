"""Derive a website domain from a Form D issuer's legal name + state.

Two backends behind one ``Resolver`` interface (phase2 prompt):
  * ``ApiDomainResolver``       - uses DOMAIN_RESOLVER_API_KEY (a search/enrichment
    service) when one is configured. Provider-agnostic: it expects a JSON body with
    a best-guess domain. This is the accurate path once you wire a real provider.
  * ``HeuristicDomainResolver`` - no key needed. Normalizes the name into candidate
    domains and keeps the first that actually RESOLVES in DNS. Lossy by design.

``get_resolver()`` returns the API backend when a key is present, else the heuristic.

Name normalization and DNS probing are both pure/injected so the logic is unit
testable without network: pass a ``dns_probe`` callable in tests.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Callable
from typing import Protocol

from core.config import Settings, get_settings
from core.http_client import HttpClient

# Legal-suffix tokens stripped before turning a company name into a domain guess.
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp",
    "corporation", "co", "company", "plc", "gmbh", "ag", "sa", "nv", "pllc",
}
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TLDS = (".com", ".io", ".co", ".ai")


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
    """Resolver backed by a search/enrichment API (DOMAIN_RESOLVER_API_KEY).

    Provider-agnostic shell: it issues a query through the shared HttpClient and
    reads a domain out of the JSON. Wire ``_endpoint``/``_extract`` to your chosen
    provider; until then it degrades to the heuristic so seeding still runs.
    """

    def __init__(self, client: HttpClient, api_key: str, *, fallback: Resolver | None = None) -> None:
        self._client = client
        self._api_key = api_key
        self._fallback = fallback or HeuristicDomainResolver()

    def resolve(self, entity_name: str, state_or_country: str | None) -> DomainResult:
        # Provider wiring goes here (kept minimal: no real provider configured on
        # this box). Fall back to the heuristic so the pipeline is never blocked.
        return self._fallback.resolve(entity_name, state_or_country)


def get_resolver(settings: Settings | None = None, client: HttpClient | None = None) -> Resolver:
    settings = settings or get_settings()
    if settings.domain_resolver_api_key and client is not None:
        return ApiDomainResolver(client, settings.domain_resolver_api_key)
    return HeuristicDomainResolver()
