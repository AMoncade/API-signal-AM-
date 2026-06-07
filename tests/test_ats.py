"""Phase 2/3 - ATS probing against REAL saved Greenhouse/Lever responses.

Fixtures were captured live (Stripe on Greenhouse, Spotify on Lever) and trimmed.
Served through an httpx.MockTransport so the shared HttpClient does the real work
with no network.
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
    board_tokens_from_domain,
    names_agree,
    token_is_name_justified,
)

ATS = Path(__file__).parent / "fixtures" / "ats"


def _ats_client() -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if "greenhouse" in host:
            token = path.rstrip("/").split("/")[-2]  # /v1/boards/{token}/jobs
            if token == "emptyco":
                return httpx.Response(200, json={"jobs": [], "meta": {"total": 0}})
            f = ATS / f"greenhouse_{token}.json"
            if f.exists():
                return httpx.Response(200, text=f.read_text(encoding="utf-8"))
            return httpx.Response(404, text="not found")
        if "lever" in host:
            token = path.rstrip("/").split("/")[-1]   # /v0/postings/{token}
            f = ATS / f"lever_{token}.json"
            if f.exists():
                return httpx.Response(200, text=f.read_text(encoding="utf-8"))
            return httpx.Response(404, text="")
        return httpx.Response(404)

    return HttpClient(Settings(_env_file=None), transport=httpx.MockTransport(handler))


def test_board_tokens_from_domain() -> None:
    assert board_tokens_from_domain("clustertruck.com", ["clustertruck"]) == ["clustertruck"]
    toks = board_tokens_from_domain("foo-bar.io", ["foobar"])
    assert "foo-bar" in toks and "foobar" in toks      # sld + no-hyphen variant + name


def test_probe_greenhouse_hit_uses_meta_total() -> None:
    with _ats_client() as client:
        hit = AtsProber(client).probe_greenhouse("stripe")
    assert hit is not None
    assert hit.provider == "greenhouse"
    assert hit.token == "stripe"
    assert hit.open_positions == 476          # from meta.total in the real response
    assert hit.company_name == "Stripe"       # captured for verification


def test_board_tokens_skip_short_generic_first_word() -> None:
    # "Apex Tech Growth Partners" -> the bare "apex" (4 chars) is NOT probed; only
    # the full concatenation / domain SLD are.
    toks = board_tokens_from_domain("apextechgrowthpartners.com",
                                    ["apex", "tech", "growth", "partners"])
    assert "apex" not in toks
    assert "apextechgrowthpartners" in toks


def test_names_agree_precision() -> None:
    assert names_agree("Stripe, Inc.", "Stripe") is True
    assert names_agree("ClusterTruck, Inc.", "ClusterTruck") is True
    assert names_agree("Robinhood Markets, Inc.", "Robinhood") is True   # board = distinctive first word
    # the false-positive we are killing: only a short prefix coincides
    assert names_agree("Apex Tech Growth Partners, LLC", "Apex Fintech") is False
    assert names_agree("Summit Capital Partners, LP", "Stripe") is False
    # NOTE: a board named exactly "Apex" *would* string-agree with an "Apex ..." issuer,
    # but the bare token "apex" is never PROBED (see test_board_tokens_skip_short_generic_first_word),
    # so that coincidence cannot reach the verifier in the real pipeline.


def test_accept_ats_match_gate() -> None:
    gh_ok = AtsHit("greenhouse", "stripe", 476, None, company_name="Stripe")
    assert accept_ats_match("Stripe, Inc.", gh_ok) is True
    gh_bad = AtsHit("greenhouse", "apex", 1, None, company_name="Apex Fintech")
    assert accept_ats_match("Apex Tech Growth Partners, LLC", gh_bad) is False
    # lever: token must equal the FULL name concatenation
    lever_ok = AtsHit("lever", "dexterity", 5, None)
    assert accept_ats_match("Dexterity, Inc.", lever_ok, "dexterity.com") is True
    lever_bad = AtsHit("lever", "apex", 1, None)   # 'apex' != 'apextechgrowthpartners'
    assert accept_ats_match("Apex Tech Growth Partners, LLC", lever_bad) is False


def test_token_is_name_justified() -> None:
    assert token_is_name_justified("stripe", "Stripe, Inc.", "stripe.com") is True
    assert token_is_name_justified("clustertruck", "ClusterTruck, Inc.") is True
    assert token_is_name_justified("apex", "Apex Tech Growth Partners, LLC") is False


def test_probe_greenhouse_miss_on_404_and_empty() -> None:
    with _ats_client() as client:
        prober = AtsProber(client)
        assert prober.probe_greenhouse("doesnotexist") is None     # 404
        assert prober.probe_greenhouse("emptyco") is None          # 200 but empty jobs


def test_probe_lever_hit() -> None:
    with _ats_client() as client:
        hit = AtsProber(client).probe_lever("spotify")
    assert hit is not None
    assert hit.provider == "lever"
    assert hit.open_positions == 3            # trimmed fixture has 3 postings


def test_probe_tokens_tries_greenhouse_then_lever() -> None:
    with _ats_client() as client:
        prober = AtsProber(client)
        # greenhouse miss, lever hit -> falls through to lever
        hit = prober.probe_tokens(["spotify"])
    assert hit is not None and hit.provider == "lever"
