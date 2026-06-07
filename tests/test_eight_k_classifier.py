"""Phase 4 - severity rubric + LLM output validation (deterministic, no network)."""

from __future__ import annotations

import httpx
import pytest

from core.config import Settings
from core.http_client import HttpClient
from worker.eight_k.classifier import (
    AnthropicClassifier,
    ClassificationError,
    OllamaClassifier,
    get_classifier,
    validate_classification,
)
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


def test_validate_coerces_non_authoritative_enums() -> None:
    # eventType/severity from the model are NOT used (item codes + rubric win), so
    # out-of-enum values are coerced rather than crashing the batch.
    d = validate_classification('{"eventType":"alien_invasion","severity":"catastrophic"}')
    assert d["eventType"] == "other"
    assert d["severity"] is None
    # confidence out of range is clamped, not rejected
    assert validate_classification('{"confidence": 5}')["confidence"] == 1.0
    assert validate_classification('{"confidence": -2}')["confidence"] == 0.0


def test_validate_still_rejects_unusable_json() -> None:
    with pytest.raises(ClassificationError):
        validate_classification("not json at all")
    with pytest.raises(ClassificationError):
        validate_classification('["a list, not an object"]')


def test_validate_truncates_long_summary() -> None:
    long = "x" * 300
    data = validate_classification(f'{{"summary":"{long}","confidence":0.5}}')
    assert len(data["summary"]) == 240


def test_validate_coerces_loose_local_model_types() -> None:
    # Local models sometimes return the wrong JSON type; coerce so it can't crash
    # the severity step (this was a real Ollama failure: affectedRole as a list).
    d = validate_classification(
        '{"affectedRole":["CEO","CFO"],"isAbrupt":"true","summary":["A.","B."]}'
    )
    assert d["affectedRole"] == "CEO, CFO"      # list -> joined string
    assert d["isAbrupt"] is True                # "true" -> bool
    assert d["summary"] == "A., B."             # list -> joined string
    # and the severity rubric handles the coerced (or even raw) value safely
    assert apply_severity_override("exec_departure", is_abrupt=True, affected_role=d["affectedRole"]) == "high"
    assert apply_severity_override("exec_departure", affected_role=["CEO"]) == "high"  # defensive


# --- provider selection + local (Ollama) classifier --------------------------

def _client() -> HttpClient:
    return HttpClient(Settings(_env_file=None), transport=httpx.MockTransport(lambda r: httpx.Response(404)))


def test_get_classifier_selects_provider() -> None:
    with _client() as c:
        # free local model: no key required
        assert isinstance(
            get_classifier(c, Settings(_env_file=None, eight_k_provider="ollama")),
            OllamaClassifier,
        )
        # hosted with a key
        assert isinstance(
            get_classifier(c, Settings(_env_file=None, eight_k_provider="anthropic", anthropic_api_key="sk-x")),
            AnthropicClassifier,
        )
        # hosted without a key -> None (8-K skipped, not a crash)
        assert get_classifier(c, Settings(_env_file=None, eight_k_provider="anthropic")) is None


def test_ollama_classifier_parses_local_response() -> None:
    body = (
        '{"eventType":"exec_departure","severity":"high","isAbrupt":true,'
        '"affectedRole":"CEO","summary":"CEO resigned.","confidence":0.8}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "message": {"content": body}, "prompt_eval_count": 120, "eval_count": 40,
        })

    client = HttpClient(Settings(_env_file=None), transport=httpx.MockTransport(handler))
    result = OllamaClassifier(client).classify(text="...", item_codes=["5.02"], entity_name="Acme")
    client.close()
    assert result.data["isAbrupt"] is True
    assert result.input_tokens == 120 and result.output_tokens == 40


def test_ollama_classifier_raises_when_server_down() -> None:
    # transport returns 404 -> raise_for_status fails -> ClassificationError (isolated per filing)
    client = HttpClient(Settings(_env_file=None), transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(ClassificationError):
        OllamaClassifier(client).classify(text="x", item_codes=["1.03"], entity_name="Acme")
    client.close()
