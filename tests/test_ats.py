"""Phase 2/3 - ATS probing across providers.

Greenhouse/Lever use REAL saved fixtures (Stripe, Spotify). The newer providers
(Ashby, SmartRecruiters, Recruitee, Workable, Breezy) are exercised with
provider-shaped responses through an httpx.MockTransport. No network is used.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from core.config import Settings
from core.http_client import HttpClient
from worker.seeding.ats import (
    AtsHit,
    AtsProber,
    accept_ats_match,
    board_tokens_from_name,
    names_agree,
    token_is_name_justified,
)

ATS = Path(__file__).parent / "fixtures" / "ats"


def _fixture_client() -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if "greenhouse" in host:
            token = path.rstrip("/").split("/")[-2]  # /v1/boards/{token}/jobs
            if token == "emptyco":
                return httpx.Response(200, json={"jobs": [], "meta": {"total": 0}})
            f = ATS / f"greenhouse_{token}.json"
            return httpx.Response(200, text=f.read_text(encoding="utf-8")) if f.exists() else httpx.Response(404, text="x")
        if "lever" in host:
            token = path.rstrip("/").split("/")[-1]   # /v0/postings/{token}
            f = ATS / f"lever_{token}.json"
            return httpx.Response(200, text=f.read_text(encoding="utf-8")) if f.exists() else httpx.Response(404, text="")
        return httpx.Response(404)

    return HttpClient(Settings(_env_file=None), transport=httpx.MockTransport(handler))


# --- token generation (name only, no domain) ---------------------------------

def test_board_tokens_from_name() -> None:
    assert board_tokens_from_name("ClusterTruck, Inc.") == ["clustertruck"]
    toks = board_tokens_from_name("Black Forest Labs Inc.")
    assert "blackforestlabs" in toks                       # full concatenation
    apex = board_tokens_from_name("Apex Tech Growth Partners, LLC")
    assert "apex" not in apex                              # short generic first word not probed
    assert "apextechgrowthpartners" in apex


# --- greenhouse / lever against real fixtures --------------------------------

def test_probe_greenhouse_hit_uses_meta_total() -> None:
    with _fixture_client() as client:
        hit = AtsProber(client).probe("greenhouse", "stripe")
    assert hit and hit.provider == "greenhouse" and hit.open_positions == 476
    assert hit.company_name == "Stripe"


def test_probe_greenhouse_miss_on_404_and_empty() -> None:
    with _fixture_client() as client:
        prober = AtsProber(client)
        assert prober.probe("greenhouse", "doesnotexist") is None
        assert prober.probe("greenhouse", "emptyco") is None


def test_probe_lever_hit_and_probe_tokens_falls_through() -> None:
    with _fixture_client() as client:
        prober = AtsProber(client)
        assert prober.probe("lever", "spotify").open_positions == 3
        # greenhouse miss then lever hit
        hit = prober.probe_tokens(["spotify"])
    assert hit and hit.provider == "lever"


# --- new providers: parser shapes via MockTransport --------------------------

def _provider_client() -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        h = request.url.host
        if "ashbyhq" in h:
            return httpx.Response(200, json={"jobs": [
                {"isListed": True, "department": "Eng"}, {"isListed": True, "department": "Sales"},
            ]})
        if "smartrecruiters" in h:
            return httpx.Response(200, json={"totalFound": 5, "content": [
                {"company": {"name": "Acme"}, "department": "Eng"}]})
        if "recruitee" in h:
            return httpx.Response(200, json={"offers": [
                {"company_name": "Acme", "department": "Eng"}]})
        if "workable" in h:
            return httpx.Response(200, json={"name": "Acme", "jobs": [{"department": "Eng"}, {"department": "Eng"}]})
        if "breezy" in h:
            return httpx.Response(200, json=[{"company": {"name": "Acme"}, "department": "Eng"}])
        return httpx.Response(404)

    return HttpClient(Settings(_env_file=None), transport=httpx.MockTransport(handler))


def test_new_provider_parsers() -> None:
    with _provider_client() as client:
        p = AtsProber(client)
        a = p.probe("ashby", "acme")
        assert a and a.open_positions == 2 and a.company_name is None     # ashby has no name
        s = p.probe("smartrecruiters", "Acme")
        assert s and s.open_positions == 5 and s.company_name == "Acme"   # totalFound
        r = p.probe("recruitee", "acme")
        assert r and r.open_positions == 1 and r.company_name == "Acme"
        w = p.probe("workable", "acme")
        assert w and w.open_positions == 2 and w.company_name == "Acme"
        b = p.probe("breezy", "acme")
        assert b and b.open_positions == 1 and b.company_name == "Acme"


# --- match verification ------------------------------------------------------

def test_names_agree_precision() -> None:
    assert names_agree("Stripe, Inc.", "Stripe") is True
    assert names_agree("Mercury Technologies, Inc.", "Mercury") is True   # 'technologies' is generic
    assert names_agree("Robinhood Markets, Inc.", "Robinhood") is True    # 'markets' is generic
    assert names_agree("Huntress Wealth Co.", "Huntress") is False        # 'wealth' is distinctive
    assert names_agree("Apex Tech Growth Partners, LLC", "Apex Fintech") is False


def test_accept_ats_match_gate() -> None:
    # provider with a company name -> verify by name
    assert accept_ats_match("Stripe, Inc.", AtsHit("greenhouse", "stripe", 5, None, company_name="Stripe")) is True
    assert accept_ats_match("Huntress Wealth Co.", AtsHit("greenhouse", "huntress", 9, None, company_name="Huntress")) is False
    # provider without a name (lever/ashby) -> token must equal full name concat
    assert accept_ats_match("Dexterity, Inc.", AtsHit("lever", "dexterity", 5, None)) is True
    assert accept_ats_match("Apex Tech Growth Partners, LLC", AtsHit("ashby", "apex", 1, None)) is False


def test_token_is_name_justified() -> None:
    assert token_is_name_justified("stripe", "Stripe, Inc.") is True
    assert token_is_name_justified("clustertruck", "ClusterTruck, Inc.") is True
    assert token_is_name_justified("apex", "Apex Tech Growth Partners, LLC") is False
