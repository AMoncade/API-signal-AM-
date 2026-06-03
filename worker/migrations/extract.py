"""Regex prefilter + software extraction for JD migration hints.

The prefilter is the cheap gate (HARD RULE #11): only postings whose text matches
migration language are sent to the model. Extraction is behind an interface:
  * ``OllamaExtractor``  - the intended cheap/LOCAL model path (no per-call cost).
  * ``KeywordExtractor`` - a deterministic, dependency-free fallback used offline
    and in tests; matches a curated catalog of named software near a migration verb.

All confidences are LOW: this is enrichment, not a flagship signal.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

# Migration language (HARD RULE #11 list, widened to common stems).
PREFILTER_RE = re.compile(
    r"\b(migrat\w*|replac\w*|switch\w*|moving off|move off|movin'? away from|"
    r"sunset\w*|deprecat\w*|consolidat\w*|rip\s*and\s*replace)\b",
    re.IGNORECASE,
)

# Small catalog of named software commonly cited in migration JDs. Deterministic
# fallback only; the local model handles the long tail.
SOFTWARE_CATALOG = [
    "Salesforce", "HubSpot", "Marketo", "Workday", "SAP", "Oracle", "NetSuite",
    "Zendesk", "Jira", "Confluence", "Snowflake", "Redshift", "BigQuery",
    "Databricks", "MongoDB", "Postgres", "PostgreSQL", "MySQL", "Mailchimp",
    "Segment", "Looker", "Tableau", "Zuora", "Stripe", "Heroku", "Jenkins",
    "GitLab", "GitHub", "Datadog", "Splunk", "Kafka", "Kubernetes", "Terraform",
]


def has_migration_signal(text: str) -> bool:
    """Cheap gate: True if the posting text mentions migration language."""
    return bool(text) and PREFILTER_RE.search(text) is not None


class SoftwareExtractor(Protocol):
    def extract(self, text: str) -> list[tuple[str, float]]: ...


class KeywordExtractor:
    """Deterministic fallback: named software from the catalog appearing within a
    short window of a migration verb. Low, fixed confidence."""

    def __init__(self, confidence: float = 0.35, window: int = 80) -> None:
        self._confidence = confidence
        self._window = window

    def extract(self, text: str) -> list[tuple[str, float]]:
        if not has_migration_signal(text):
            return []
        hits: dict[str, float] = {}
        verb_spans = [m.start() for m in PREFILTER_RE.finditer(text)]
        for sw in SOFTWARE_CATALOG:
            for m in re.finditer(rf"\b{re.escape(sw)}\b", text, re.IGNORECASE):
                if any(abs(m.start() - v) <= self._window for v in verb_spans):
                    hits[sw] = self._confidence
                    break
        return sorted(hits.items())


class OllamaExtractor:
    """Cheap LOCAL model extractor (Ollama). Used only on prefiltered hits.

    Calls a local Ollama server; on any failure it degrades to the KeywordExtractor
    so the pipeline never blocks on the model being down.
    """

    _PROMPT = (
        "Extract ONLY named third-party software products that this job posting says "
        "the company is migrating away from, replacing, or switching off. Return a JSON "
        'array of objects: [{"software": "Name", "confidence": 0.0-1.0}]. If none, return [].'
        "\n\nPosting:\n"
    )

    def __init__(self, client, *, model: str = "llama3.2", url: str = "http://localhost:11434/api/generate") -> None:
        self._client = client
        self._model = model
        self._url = url
        self._fallback = KeywordExtractor()

    def extract(self, text: str) -> list[tuple[str, float]]:
        if not has_migration_signal(text):
            return []
        try:
            resp = self._client.post(
                self._url,
                json={"model": self._model, "prompt": self._PROMPT + text[:8000],
                      "stream": False, "format": "json"},
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "[]")
            data = json.loads(raw)
            out: list[tuple[str, float]] = []
            for item in data if isinstance(data, list) else []:
                sw = item.get("software")
                conf = float(item.get("confidence", 0.3))
                if sw:
                    out.append((sw, min(conf, 0.6)))  # cap: enrichment stays low-confidence
            return out
        except Exception:  # noqa: BLE001 - local model down/garbled -> fall back
            return self._fallback.extract(text)
