"""Phase 7 - migrations enrichment: regex prefilter + extraction + persistence.

"Without migration language" cases use REAL Spotify postings (the Lever fixture);
"with" cases are realistic JD snippets. Asserts the prefilter gates cost, named
software is extracted only near migration verbs, signals are low-confidence, and
the raw JD text is NEVER stored.
"""

from __future__ import annotations

import json
from pathlib import Path

from worker.migrations.extract import KeywordExtractor, has_migration_signal
from worker.migrations.ingest import enrich_migrations
from worker.store import InMemoryStore

ATS = Path(__file__).parent / "fixtures" / "ats"

WITH_MIGRATION = (
    "We are migrating off Salesforce to HubSpot this year and need an engineer to "
    "lead the data migration. Experience replacing legacy systems is a plus."
)
NO_SOFTWARE_BUT_VERB = "You'll help us switch contexts quickly across many projects."
PLAIN = "Looking for a backend engineer with Python and distributed systems experience."


def _real_spotify_posting_texts() -> list[str]:
    data = json.loads((ATS / "lever_spotify.json").read_text(encoding="utf-8"))
    return [
        " ".join(str(p.get(k) or "") for k in ("descriptionPlain", "additionalPlain", "text"))
        for p in data
    ]


def test_prefilter_gates_on_migration_language() -> None:
    assert has_migration_signal(WITH_MIGRATION) is True
    assert has_migration_signal(NO_SOFTWARE_BUT_VERB) is True   # 'switch' matches the gate
    assert has_migration_signal(PLAIN) is False


def test_extractor_finds_named_software_near_migration_verbs() -> None:
    got = dict(KeywordExtractor().extract(WITH_MIGRATION))
    assert "Salesforce" in got and "HubSpot" in got
    assert all(0 < c <= 0.6 for c in got.values())            # low-confidence enrichment


def test_extractor_returns_nothing_without_named_software() -> None:
    # migration verb present but no catalog software nearby -> no signal (no false hit)
    assert KeywordExtractor().extract(NO_SOFTWARE_BUT_VERB) == []
    assert KeywordExtractor().extract(PLAIN) == []


def test_real_spotify_postings_yield_no_migration_signals() -> None:
    # Real JDs that are not about replacing named software -> nothing extracted.
    extractor = KeywordExtractor()
    total = sum(len(extractor.extract(t)) for t in _real_spotify_posting_texts())
    assert total == 0


def test_enrich_persists_signal_only_never_text() -> None:
    store = InMemoryStore()
    postings = [
        ("0000000001", "https://boards.greenhouse.io/acme/jobs/1", WITH_MIGRATION),
        ("0000000002", "https://jobs.lever.co/x/2", PLAIN),                 # filtered out
        *[("0000000003", "https://x/3", t) for t in _real_spotify_posting_texts()],
    ]
    stats = enrich_migrations(store, postings)

    assert stats.prefilter_hits >= 1
    rows = list(store.tech_signals.values())
    softwares = {r["software"] for r in rows}
    assert {"Salesforce", "HubSpot"} <= softwares
    # only the acme posting produced signals; plain + spotify produced none
    assert all(r["cik"] == "0000000001" for r in rows)
    # SIGNALS ONLY: no raw JD text persisted anywhere on the stored rows
    for r in rows:
        assert "text" not in r and "content" not in r and "description" not in r
        assert r["signal_type"] == "migration"
    assert store.runs[-1]["job_name"] == "migrations"
    assert store.runs[-1]["status"] == "success"
