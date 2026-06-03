"""eight_k_events record - aligned 1:1 with /docs/schema.sql.

SIGNALS ONLY: the only free text kept is the <=240-char neutral summary. No FK to
companies (8-K issuers are a different, public-company universe).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class EightKEventRecord:
    accession_number: str
    cik: str
    entity_name: str
    filed_at: datetime
    item_codes: list[str] = field(default_factory=list)
    event_type: str = "other"
    severity: str = "low"
    is_abrupt: bool | None = None
    affected_role: str | None = None
    summary: str | None = None
    confidence: float | None = None
    source_url: str = ""

    def to_row(self) -> dict:
        summary = self.summary
        if summary is not None and len(summary) > 240:
            summary = summary[:240]  # schema CHECK: char_length(summary) <= 240
        return {
            "accession_number": self.accession_number,
            "cik": self.cik,
            "entity_name": self.entity_name,
            "filed_at": self.filed_at.isoformat(),
            "item_codes": self.item_codes,
            "event_type": self.event_type,
            "severity": self.severity,
            "is_abrupt": self.is_abrupt,
            "affected_role": self.affected_role,
            "summary": summary,
            "confidence": self.confidence,
            "source_url": self.source_url,
        }
