"""Phase 4 orchestration: EDGAR 8-K -> Item codes -> (event-bearing?) -> classify.

Flow per filing:
  1. Fetch the full submission .txt (one request); split off the SGML header.
  2. Recover Item codes from the header titles (deterministic, free).
  3. headline_event(codes): if the filing has no event-bearing item, SKIP it -- no
     LLM cost on routine 8-Ks (earnings, votes, RegFD, exhibits).
  4. For event-bearing filings, extract the primary document's plain text and call
     the HOSTED model for specifics + a proposed severity.
  5. eventType comes from the Item codes (NOT the model); severity is overridden by
     the deterministic rubric. Store signals only (no raw text). Log to ingestion_runs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from core.http_client import HttpClient
from worker.eight_k.classifier import Classifier
from worker.eight_k.item_codes import headline_event, item_codes_from_header
from worker.eight_k.models import EightKEventRecord
from worker.eight_k.severity import apply_severity_override
from worker.edgar.daily_index import FilingRef, fetch_filing_refs
from worker.store import Store

log = logging.getLogger("worker.eight_k")

_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_MAX_TEXT_CHARS = 50_000  # bound the LLM input; 8-K bodies are short, exhibits are not


def submission_txt_url(ref: FilingRef) -> str:
    return f"{_ARCHIVES}/{int(ref.cik)}/{ref.accession_number}.txt"


def filing_index_url(ref: FilingRef) -> str:
    folder = ref.accession_number.replace("-", "")
    return f"{_ARCHIVES}/{int(ref.cik)}/{folder}/{ref.accession_number}-index.htm"


def split_header(full_txt: str) -> str:
    end = full_txt.find("</SEC-HEADER>")
    return full_txt[: end + len("</SEC-HEADER>")] if end != -1 else full_txt[:8000]


def extract_primary_text(full_txt: str) -> str:
    """Plain text of the primary 8-K document (tags stripped, bounded length).

    Never persisted - this is transient input to the classifier only.
    """
    # Find the first <DOCUMENT> whose <TYPE> starts with 8-K, else the first doc.
    docs = re.split(r"<DOCUMENT>", full_txt)[1:]
    chosen = None
    for doc in docs:
        m = re.search(r"<TYPE>([^\s<]+)", doc)
        if m and m.group(1).upper().startswith("8-K"):
            chosen = doc
            break
    if chosen is None and docs:
        chosen = docs[0]
    if not chosen:
        return ""
    tm = re.search(r"<TEXT>(.*?)</TEXT>", chosen, re.S)
    body = tm.group(1) if tm else chosen
    text = _TAG_RE.sub(" ", body)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text[:_MAX_TEXT_CHARS]


@dataclass(slots=True)
class EightKStats:
    seen: int = 0
    classified: int = 0
    skipped_non_event: int = 0
    skipped_existing: int = 0
    fetch_errors: int = 0
    classify_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def summary(self) -> str:
        return (
            f"seen={self.seen} classified={self.classified} "
            f"non_event_skipped={self.skipped_non_event} already_present={self.skipped_existing} "
            f"fetch_errors={self.fetch_errors} classify_errors={self.classify_errors} "
            f"tokens(in/out)={self.input_tokens}/{self.output_tokens}"
        )


def classify_one(
    client: HttpClient, store: Store, classifier: Classifier, ref: FilingRef, stats: EightKStats
) -> EightKEventRecord | None:
    if store.eight_k_exists(ref.accession_number):
        stats.skipped_existing += 1
        return None
    resp = client.get(submission_txt_url(ref))
    if resp.status_code != 200:
        stats.fetch_errors += 1
        return None
    full_txt = resp.text
    codes = item_codes_from_header(split_header(full_txt))
    event = headline_event(codes)
    if event is None:
        stats.skipped_non_event += 1
        return None
    event_type, _default_sev = event

    text = extract_primary_text(full_txt)
    from worker.eight_k.classifier import ClassificationError

    try:
        result = classifier.classify(text=text, item_codes=codes, entity_name=ref.company_name)
    except ClassificationError as exc:
        # One unusable model response must not abort the whole batch.
        stats.classify_errors += 1
        log.warning("classification failed for %s: %s", ref.accession_number, exc)
        return None
    stats.input_tokens += result.input_tokens
    stats.output_tokens += result.output_tokens
    llm = result.data

    severity = apply_severity_override(
        event_type, is_abrupt=llm.get("isAbrupt"), affected_role=llm.get("affectedRole")
    )
    record = EightKEventRecord(
        accession_number=ref.accession_number,
        cik=ref.cik10,
        entity_name=ref.company_name,
        filed_at=ref.filed_at,
        item_codes=codes,
        event_type=event_type,          # from Item codes, NOT the model
        severity=severity,              # deterministic override
        is_abrupt=llm.get("isAbrupt"),
        affected_role=llm.get("affectedRole"),
        summary=llm.get("summary"),
        confidence=llm.get("confidence"),
        source_url=filing_index_url(ref),
    )
    store.insert_eight_k_event(record.to_row())
    stats.classified += 1
    return record


def ingest_eight_k(
    client: HttpClient,
    store: Store,
    classifier: Classifier,
    *,
    date: str | None = None,
    limit: int | None = None,
) -> EightKStats:
    stats = EightKStats()
    run_id = store.start_run("eight_k")
    try:
        refs = fetch_filing_refs(client, forms={"8-K"}, date=date)
        if limit is not None:
            refs = refs[:limit]
        stats.seen = len(refs)
        log.info("8-K ingest: %d filings to scan", stats.seen)
        for ref in refs:
            classify_one(client, store, classifier, ref, stats)
        store.finish_run(
            run_id, status="success", items_processed=stats.classified, notes=stats.summary()
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("8-K ingest failed")
        store.finish_run(run_id, status="error", items_processed=stats.classified, notes=repr(exc))
        raise
    log.info("8-K ingest done: %s", stats.summary())
    return stats


def preview_event_bearing(
    client: HttpClient, *, date: str | None = None, limit: int | None = None
) -> list[dict]:
    """Deterministic, LLM-free view: which 8-Ks are event-bearing and their codes
    -> eventType + default severity. Powers the credential-free dry-run demo."""
    refs = fetch_filing_refs(client, forms={"8-K"}, date=date)
    if limit is not None:
        refs = refs[:limit]
    out: list[dict] = []
    for ref in refs:
        resp = client.get(submission_txt_url(ref))
        if resp.status_code != 200:
            continue
        codes = item_codes_from_header(split_header(resp.text))
        event = headline_event(codes)
        if event is None:
            continue
        out.append(
            {
                "accession": ref.accession_number,
                "entity": ref.company_name,
                "item_codes": codes,
                "event_type": event[0],
                "default_severity": event[1],
            }
        )
    return out
