"""Phase 2 - domain derivation (pure name normalization + injected DNS probe).

Also covers the API backend (ApiDomainResolver), exercised with an HttpClient over
an httpx.MockTransport so the real request/parse/fallback path runs without network.
"""

from __future__ import annotations

import httpx

from core.config import Settings
from core.http_client import HttpClient
from worker.seeding.domain_resolver import (
    ApiDomainResolver,
    DomainResult,
    HeuristicDomainResolver,
    candidate_domains,
    clean_domain,
    get_resolver,
    normalize_name_tokens,
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


class _FixedFallback:
    """A fallback resolver with a recognizable result, to prove fallback happened."""

    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, entity_name: str, state_or_country: str | None) -> DomainResult:
        self.calls += 1
        return DomainResult("fallback.example", "resolved")


def _client(handler) -> HttpClient:
    # No retries here so a scripted 500 returns immediately (retries are tested
    # separately in test_http_client.py).
    settings = _settings(http_max_retries=0)
    return HttpClient(settings, transport=httpx.MockTransport(handler))


def test_normalize_strips_legal_suffixes_and_punctuation() -> None:
    assert normalize_name_tokens("ClusterTruck, Inc.") == ["clustertruck"]
    assert normalize_name_tokens("FEB BANCSHARES INC") == ["feb", "bancshares"]
    # 'L.P.' / 'Fund' handling: legal suffix dropped, real words kept.
    assert normalize_name_tokens("iCapital Multi-Strategy Fund, L.P.") == [
        "icapital", "multi", "strategy", "fund",
    ]


def test_candidate_domains_prefers_concatenated_dotcom() -> None:
    cands = candidate_domains("ClusterTruck, Inc.")
    assert cands[0] == "clustertruck.com"
    assert "clustertruck.io" in cands
    # multi-word company: full concatenation comes first
    cands2 = candidate_domains("FEB BANCSHARES INC")
    assert cands2[0] == "febbancshares.com"
    assert "feb.com" in cands2  # first-word fallback


def test_candidate_domains_empty_for_blank() -> None:
    assert candidate_domains("") == []
    assert candidate_domains("Inc.") == []  # nothing but a legal suffix


def test_heuristic_resolver_returns_first_dns_hit() -> None:
    resolved = {"clustertruck.com"}
    r = HeuristicDomainResolver(dns_probe=lambda d: d in resolved)
    res = r.resolve("ClusterTruck, Inc.", "IN")
    assert res.domain == "clustertruck.com"
    assert res.status == "resolved"


def test_heuristic_resolver_unresolved_when_nothing_in_dns() -> None:
    r = HeuristicDomainResolver(dns_probe=lambda d: False)
    res = r.resolve("Some Obscure Holdings", "DE")
    assert res.domain is None
    assert res.status == "unresolved"


def test_heuristic_resolver_failed_when_no_candidates() -> None:
    r = HeuristicDomainResolver(dns_probe=lambda d: True)
    res = r.resolve("LLC", None)            # only a legal suffix -> no candidates
    assert res.domain is None
    assert res.status == "failed"


# --- clean_domain normalization ---------------------------------------------

def test_clean_domain_strips_scheme_www_and_path() -> None:
    assert clean_domain("https://www.Acme.IO/about?x=1") == "acme.io"
    assert clean_domain("acme.com") == "acme.com"
    assert clean_domain("sub.acme.co.uk") == "sub.acme.co.uk"
    assert clean_domain("not a domain") is None
    assert clean_domain(None) is None
    assert clean_domain(123) is None


# --- ApiDomainResolver (real request via MockTransport) ----------------------

def test_api_resolver_reads_domain_and_sends_key_and_name() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["name"] = request.url.params.get("name")
        seen["country"] = request.url.params.get("country")
        return httpx.Response(200, json={"domain": "https://www.acme.io/about"})

    with _client(handler) as client:
        r = ApiDomainResolver(client, "secret-key", endpoint="https://api.resolver.test/resolve")
        res = r.resolve("Acme Robotics, Inc.", "CA")

    assert res.domain == "acme.io"          # normalized out of the URL the API returned
    assert res.status == "resolved"
    assert seen["auth"] == "Bearer secret-key"
    assert seen["name"] == "Acme Robotics, Inc."
    assert seen["country"] == "CA"


def test_api_resolver_extracts_nested_domain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"company": {"website": "acme.com"}}})

    with _client(handler) as client:
        r = ApiDomainResolver(client, "k", endpoint="https://api.resolver.test/resolve")
        res = r.resolve("Acme Inc", None)
    assert res.domain == "acme.com"
    assert res.status == "resolved"


def test_api_resolver_falls_back_on_non_200() -> None:
    fallback = _FixedFallback()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream boom")

    with _client(handler) as client:
        r = ApiDomainResolver(client, "k", endpoint="https://api.resolver.test/r", fallback=fallback)
        res = r.resolve("Acme Inc", "CA")
    assert fallback.calls == 1
    assert res.domain == "fallback.example"


def test_api_resolver_falls_back_when_body_has_no_domain() -> None:
    fallback = _FixedFallback()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"unrelated": "value"}})

    with _client(handler) as client:
        r = ApiDomainResolver(client, "k", endpoint="https://api.resolver.test/r", fallback=fallback)
        res = r.resolve("Acme Inc", None)
    assert fallback.calls == 1
    assert res.domain == "fallback.example"


def test_api_resolver_falls_back_on_network_error() -> None:
    fallback = _FixedFallback()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with _client(handler) as client:
        r = ApiDomainResolver(client, "k", endpoint="https://api.resolver.test/r", fallback=fallback)
        res = r.resolve("Acme Inc", None)
    assert fallback.calls == 1
    assert res.domain == "fallback.example"


def test_api_resolver_without_endpoint_uses_fallback_without_network() -> None:
    fallback = _FixedFallback()

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no HTTP call should be made when endpoint is empty")

    with _client(handler) as client:
        r = ApiDomainResolver(client, "k", endpoint="", fallback=fallback)
        res = r.resolve("Acme Inc", None)
    assert fallback.calls == 1
    assert res.domain == "fallback.example"


# --- get_resolver backend selection ------------------------------------------

def test_get_resolver_picks_api_backend_only_when_key_and_url_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json={})

    with _client(handler) as client:
        both = _settings(domain_resolver_api_key="k", domain_resolver_api_url="https://r.test")
        assert isinstance(get_resolver(both, client), ApiDomainResolver)

        key_only = _settings(domain_resolver_api_key="k")
        assert isinstance(get_resolver(key_only, client), HeuristicDomainResolver)

        # key + url but no client -> can't issue requests, so heuristic
        assert isinstance(get_resolver(both, None), HeuristicDomainResolver)

        none = _settings()
        assert isinstance(get_resolver(none, client), HeuristicDomainResolver)
