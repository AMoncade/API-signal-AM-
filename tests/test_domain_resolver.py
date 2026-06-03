"""Phase 2 - domain derivation (pure name normalization + injected DNS probe)."""

from __future__ import annotations

from worker.seeding.domain_resolver import (
    HeuristicDomainResolver,
    candidate_domains,
    normalize_name_tokens,
)


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
