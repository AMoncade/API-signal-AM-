"""Phase 4 - 8-K ingest orchestration end-to-end (offline, fake classifier).

Discovery is stubbed to point at the real header fixtures; the HttpClient serves
them via MockTransport; a FakeClassifier stands in for the hosted model. Proves:
event-bearing filtering, eventType FROM ITEM CODES, deterministic severity override,
token accounting, idempotency, run logging, and that no raw text is stored.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from core.config import Settings
from core.http_client import HttpClient
from worker.edgar.daily_index import parse_master_idx
from worker.eight_k.classifier import ClassificationResult
from worker.eight_k.ingest import ingest_eight_k
from worker.store import InMemoryStore

EK = Path(__file__).parent / "fixtures" / "eight_k"

MASTER_IDX = """\
CIK|Company Name|Form Type|Date Filed|File Name
--------------------------------------------------------------------------------
63276|MATTEL INC /DE/|8-K|2026-06-02|edgar/data/63276/0000063276-26-000013.txt
29644|DONALDSON Co INC|8-K|2026-06-02|edgar/data/29644/0000029644-26-000049.txt
886158|Bed Bath & Beyond Inc|8-K|2023-09-29|edgar/data/886158/0001193125-23-247428.txt
"""


class FakeClassifier:
    """Returns canned specifics + usage; never inspects the (transient) text."""

    def __init__(self) -> None:
        self.calls = 0

    def classify(self, *, text, item_codes, entity_name) -> ClassificationResult:
        self.calls += 1
        return ClassificationResult(
            data={
                "isAbrupt": True,
                "affectedRole": "Chief Executive Officer",
                "summary": "Executive departed; board appointed an interim replacement.",
                "confidence": 0.9,
            },
            input_tokens=100,
            output_tokens=50,
        )


def _ek_client() -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        accession = request.url.path.rstrip("/").split("/")[-1].removesuffix(".txt")
        f = EK / f"{accession}.hdr.txt"
        return (
            httpx.Response(200, text=f.read_text(encoding="utf-8"))
            if f.exists()
            else httpx.Response(404, text="x")
        )

    settings = Settings(_env_file=None, sec_user_agent="Test test@example.com")
    return HttpClient(settings, transport=httpx.MockTransport(handler))


def test_ingest_eight_k_classifies_event_bearing_only(monkeypatch) -> None:
    refs = parse_master_idx(MASTER_IDX, forms={"8-K"})
    monkeypatch.setattr(
        "worker.eight_k.ingest.fetch_filing_refs",
        lambda client, *, forms, date=None: refs,
    )
    store = InMemoryStore()
    fake = FakeClassifier()
    with _ek_client() as client:
        stats = ingest_eight_k(client, store, fake)

    # Donaldson (2.02 earnings) is skipped; Mattel (5.02) + BBBY (1.03) classified.
    assert stats.seen == 3
    assert stats.classified == 2
    assert stats.skipped_non_event == 1
    assert fake.calls == 2                       # no LLM call on the non-event filing
    assert stats.input_tokens == 200 and stats.output_tokens == 100

    mattel = store.eight_k_events["0000063276-26-000013"]
    assert mattel["event_type"] == "exec_departure"      # from Item codes, not the model
    assert mattel["item_codes"] == ["5.02", "5.07", "9.01"]
    assert mattel["severity"] == "high"                  # abrupt CEO -> override to high
    assert mattel["is_abrupt"] is True
    assert "0000063276-26-000013" in mattel["source_url"]

    bbby = store.eight_k_events["0001193125-23-247428"]
    assert bbby["event_type"] == "bankruptcy"
    assert bbby["severity"] == "critical"                # rubric override regardless of model
    assert bbby["item_codes"][0] == "1.03"

    # signals only: no raw filing text in the stored row
    assert "text" not in bbby and "body" not in bbby
    assert len(bbby["summary"]) <= 240

    assert store.runs[-1]["job_name"] == "eight_k"
    assert store.runs[-1]["status"] == "success"


def test_ingest_eight_k_is_idempotent(monkeypatch) -> None:
    refs = parse_master_idx(MASTER_IDX, forms={"8-K"})
    monkeypatch.setattr(
        "worker.eight_k.ingest.fetch_filing_refs",
        lambda client, *, forms, date=None: refs,
    )
    store = InMemoryStore()
    with _ek_client() as client:
        ingest_eight_k(client, store, FakeClassifier())
        stats2 = ingest_eight_k(client, store, FakeClassifier())
    assert stats2.classified == 0
    assert stats2.skipped_existing == 2
    assert len(store.eight_k_events) == 2
