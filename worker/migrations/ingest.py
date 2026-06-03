"""Phase 7 orchestration: JD text -> regex prefilter -> (hits only) -> extractor.

Takes an iterable of (cik, posting_url, text). The text is processed IN MEMORY and
DISCARDED; only the extracted (software, confidence, source_posting_url) signal is
written to company_tech_signals. The regex prefilter is the cost gate - the model
is only consulted for postings that already mention migration language.

``fetch_postings_text`` pulls live JD text from the matched ATS boards (the only
place we request content=true), yielding the same (cik, url, text) tuples; it is
kept separate from the pure orchestration so the logic is testable offline.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from core.http_client import HttpClient
from worker.migrations.extract import KeywordExtractor, SoftwareExtractor, has_migration_signal
from worker.store import AtsCompany, Store

log = logging.getLogger("worker.migrations")

_TAG_RE = re.compile(r"<[^>]+>")
_GREENHOUSE_CONTENT = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
_LEVER_POSTINGS = "https://api.lever.co/v0/postings/{token}?mode=json"


@dataclass(slots=True)
class MigrationStats:
    postings_scanned: int = 0
    prefilter_hits: int = 0
    signals_extracted: int = 0

    def summary(self) -> str:
        return (
            f"postings_scanned={self.postings_scanned} prefilter_hits={self.prefilter_hits} "
            f"signals_extracted={self.signals_extracted}"
        )


def enrich_migrations(
    store: Store,
    postings: Iterable[tuple[str, str, str]],
    *,
    extractor: SoftwareExtractor | None = None,
) -> MigrationStats:
    """Run the prefilter + extractor over (cik, url, text) and persist signals."""
    extractor = extractor or KeywordExtractor()
    stats = MigrationStats()
    run_id = store.start_run("migrations")
    try:
        for cik, url, text in postings:
            stats.postings_scanned += 1
            if not has_migration_signal(text):          # cheap gate (HARD RULE #11)
                continue
            stats.prefilter_hits += 1
            for software, confidence in extractor.extract(text):
                store.insert_tech_signal(cik, software, confidence, url)  # signal only; no text
                stats.signals_extracted += 1
        store.finish_run(
            run_id, status="success", items_processed=stats.signals_extracted, notes=stats.summary()
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("migrations enrichment failed")
        store.finish_run(run_id, status="error", items_processed=stats.signals_extracted, notes=repr(exc))
        raise
    log.info("migrations enrichment done: %s", stats.summary())
    return stats


def fetch_postings_text(client: HttpClient, company: AtsCompany) -> Iterator[tuple[str, str, str]]:
    """Yield (cik, posting_url, plain_text) for a company's live postings.

    The ONLY place content=true is requested. Text is yielded for in-memory
    processing and never stored.
    """
    if company.ats_provider == "greenhouse":
        resp = client.get(_GREENHOUSE_CONTENT.format(token=company.ats_token))
        if resp.status_code != 200:
            return
        for job in (resp.json().get("jobs") or []):
            text = _TAG_RE.sub(" ", job.get("content") or "")
            yield company.cik, job.get("absolute_url") or "", text
    elif company.ats_provider == "lever":
        resp = client.get(_LEVER_POSTINGS.format(token=company.ats_token))
        if resp.status_code != 200:
            return
        for post in (resp.json() or []):
            text = " ".join(
                str(post.get(k) or "") for k in ("descriptionPlain", "additionalPlain")
            )
            yield company.cik, post.get("hostedUrl") or "", text
