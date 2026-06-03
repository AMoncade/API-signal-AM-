"""Phase 4 - severity rubric + LLM output validation (deterministic, no network)."""

from __future__ import annotations

import pytest

from worker.eight_k.classifier import ClassificationError, validate_classification
from worker.eight_k.severity import apply_severity_override


# --- severity rubric ---------------------------------------------------------

def test_severity_fixed_events() -> None:
    assert apply_severity_override("bankruptcy") == "critical"
    assert apply_severity_override("restatement") == "critical"
    assert apply_severity_override("impairment") == "high"
    assert apply_severity_override("delisting_risk") == "high"
    assert apply_severity_override("auditor_change") == "medium"
    assert apply_severity_override("contract_change") == "low"


def test_severity_exec_departure_rules() -> None:
    # abrupt -> high regardless of role
    assert apply_severity_override("exec_departure", is_abrupt=True, affected_role="Director") == "high"
    # CEO/CFO/Chair -> high even if planned
    assert apply_severity_override("exec_departure", is_abrupt=False, affected_role="CFO") == "high"
    assert apply_severity_override("exec_departure", is_abrupt=False, affected_role="Chairman") == "high"
    # planned, non-exec -> medium
    assert apply_severity_override("exec_departure", is_abrupt=False, affected_role="VP of Sales") == "medium"
    assert apply_severity_override("exec_departure", is_abrupt=None, affected_role=None) == "medium"


def test_severity_other_defaults_medium() -> None:
    assert apply_severity_override("other") == "medium"


# --- LLM output validation ---------------------------------------------------

def test_validate_accepts_clean_json() -> None:
    data = validate_classification(
        '{"eventType":"exec_departure","severity":"high","isAbrupt":true,'
        '"affectedRole":"CEO","summary":"X departed.","confidence":0.92}'
    )
    assert data["eventType"] == "exec_departure"
    assert data["isAbrupt"] is True


def test_validate_tolerates_markdown_fences() -> None:
    data = validate_classification('```json\n{"severity":"low","confidence":0.5}\n```')
    assert data["severity"] == "low"


def test_validate_rejects_bad_enums_and_bad_json() -> None:
    with pytest.raises(ClassificationError):
        validate_classification('{"severity":"catastrophic"}')
    with pytest.raises(ClassificationError):
        validate_classification('{"eventType":"alien_invasion"}')
    with pytest.raises(ClassificationError):
        validate_classification("not json at all")
    with pytest.raises(ClassificationError):
        validate_classification('{"confidence": 5}')


def test_validate_truncates_long_summary() -> None:
    long = "x" * 300
    data = validate_classification(f'{{"summary":"{long}","confidence":0.5}}')
    assert len(data["summary"]) == 240
