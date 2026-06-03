"""Phase 3 - job snapshots + velocity.

Snapshot counts come from the real ATS fixtures (Stripe/Greenhouse, Spotify/Lever)
via MockTransport. The velocity integration check seeds a ~35-day-old baseline plus
a current count >= 2x and asserts the company surfaces as surging through the
InMemoryStore mirror of the company_velocity view (no Postgres needed).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import httpx

from core.config import Settings
from core.http_client import HttpClient
from worker.form_d.models import CompanyRecord
from worker.seeding.ats import AtsProber
from worker.snapshots.snapshot import snapshot_jobs
from worker.store import InMemoryStore

ATS = Path(__file__).parent / "fixtures" / "ats"


def _ats_client() -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if "greenhouse" in host:
            token = path.rstrip("/").split("/")[-2]
            f = ATS / f"greenhouse_{token}.json"
            return httpx.Response(200, text=f.read_text(encoding="utf-8")) if f.exists() else httpx.Response(404, text="x")
        if "lever" in host:
            token = path.rstrip("/").split("/")[-1]
            f = ATS / f"lever_{token}.json"
            return httpx.Response(200, text=f.read_text(encoding="utf-8")) if f.exists() else httpx.Response(404, text="")
        return httpx.Response(404)

    return HttpClient(Settings(_env_file=None), transport=httpx.MockTransport(handler))


def test_snapshot_counts_from_real_boards() -> None:
    store = InMemoryStore()
    store.upsert_company(CompanyRecord("0000000001", "Stripe, Inc.", "CA"))
    store.set_ats("0000000001", "greenhouse", "stripe")
    store.upsert_company(CompanyRecord("0000000002", "Spotify", "NY"))
    store.set_ats("0000000002", "lever", "spotify")

    with _ats_client() as client:
        stats = snapshot_jobs(client, store, prober=AtsProber(client))

    assert stats.seen == 2
    assert stats.snapshotted == 2
    counts = {s["cik"]: s["open_positions"] for s in store.snapshots}
    assert counts["0000000001"] == 476     # Stripe greenhouse meta.total
    assert counts["0000000002"] == 3       # Spotify lever (trimmed fixture)
    assert store.runs[-1]["job_name"] == "job_snapshots"
    assert store.runs[-1]["status"] == "success"


def test_snapshot_miss_when_board_gone() -> None:
    store = InMemoryStore()
    store.upsert_company(CompanyRecord("0000000009", "Gone Co", "CA"))
    store.set_ats("0000000009", "greenhouse", "doesnotexist")
    with _ats_client() as client:
        stats = snapshot_jobs(client, store, prober=AtsProber(client))
    assert stats.snapshotted == 0
    assert stats.misses == 1
    assert store.snapshots == []


def test_velocity_view_mirror_flags_surging() -> None:
    store = InMemoryStore()
    today = date.today()
    baseline_day = today - timedelta(days=35)   # older than the 30-day window
    # surging: doubled (10 -> 25 = 2.5x)
    store.insert_snapshot("0000000001", 10, snapshot_date=baseline_day)
    store.insert_snapshot("0000000001", 25, snapshot_date=today)
    # flat: barely up (10 -> 12) -> not surging
    store.insert_snapshot("0000000002", 10, snapshot_date=baseline_day)
    store.insert_snapshot("0000000002", 12, snapshot_date=today)

    velocity = {v["cik"]: v for v in store.company_velocity()}
    assert velocity["0000000001"]["is_surging"] is True
    assert velocity["0000000001"]["velocity_ratio"] == 2.5
    assert velocity["0000000002"]["is_surging"] is False

    surging = store.surging_companies()
    assert [v["cik"] for v in surging] == ["0000000001"]


def test_no_backfill_single_snapshot_is_not_surging() -> None:
    # NO BACKFILL: with only one (today) snapshot there is no baseline, so a company
    # can never be "surging" until >=30 days of history accumulate.
    store = InMemoryStore()
    store.insert_snapshot("0000000001", 100, snapshot_date=date.today())
    [v] = store.company_velocity()
    assert v["baseline_open"] is None
    assert v["is_surging"] is False
    assert store.surging_companies() == []
