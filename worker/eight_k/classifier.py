"""8-K prose classification via a HOSTED model (HARD RULE #9).

The model NEVER decides the event TYPE (that comes from the Item codes); it only
extracts specifics (affectedRole, summary, isAbrupt) and proposes a severity, using
the exact system prompt + output schema from spec Appendix B. The call is made with
the shared HttpClient (api.anthropic.com is not sec.gov, so no SEC UA is attached;
the global rate cap still applies). Returns the validated JSON plus token usage so
the caller can report cost per filing.

The classifier is an interface so the ingest pipeline is testable without a key:
tests inject a fake; AnthropicClassifier is the real, key-backed implementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from core.config import Settings, get_settings
from core.http_client import HttpClient

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

EVENT_TYPES = {
    "bankruptcy", "restatement", "restructuring_layoffs", "impairment",
    "debt_acceleration", "delisting_risk", "cyber_incident", "exec_departure",
    "auditor_change", "contract_change", "other",
}
SEVERITIES = {"low", "medium", "high", "critical"}

# Exact system prompt from spec Appendix B.
SYSTEM_PROMPT = """\
You are a financial-filing event classifier. You will receive (1) the plain text of a
single SEC Form 8-K and (2) the list of its Item codes. Output ONLY a JSON object matching
the schema provided - no preamble, no markdown fences, no commentary.

Rules:
- Use the Item codes as the PRIMARY signal for eventType. Use the prose only to fill
  specifics (affectedRole, summary) and to set severity.
- If the event matches no defined category, set eventType to "other".
- Never state a fact not present in the text. If a field is unknown, use null.
- summary must be neutral and factual: what happened, who, effective when. No adjectives,
  no speculation, <=240 characters.
- For Item 5.02 (departures): set isAbrupt=true ONLY if the text indicates the departure is
  effective immediately, follows a disagreement, or names no successor; otherwise false.
  Raise severity to "high" when isAbrupt is true OR the role is CEO/CFO/Chair; else "medium".
- confidence is your calibrated certainty the classification is correct (0.0-1.0).
- Output valid, parseable JSON only."""

OUTPUT_SCHEMA = {
    "cik": "string", "entityName": "string", "accessionNo": "string",
    "filedAt": "ISO-8601", "sourceUrl": "string", "itemCodes": ["5.02"],
    "eventType": "exec_departure", "severity": "low | medium | high | critical",
    "isAbrupt": True, "affectedRole": "string | null",
    "summary": "string, <=240 chars, neutral and factual", "confidence": 0.0,
}


@dataclass(slots=True)
class ClassificationResult:
    data: dict = field(default_factory=dict)   # validated LLM JSON (specifics + proposed severity)
    input_tokens: int = 0
    output_tokens: int = 0


class ClassificationError(ValueError):
    """Raised when the model output is missing/invalid JSON or fails validation."""


class Classifier(Protocol):
    def classify(self, *, text: str, item_codes: list[str], entity_name: str) -> ClassificationResult: ...


def validate_classification(raw: str) -> dict:
    """Parse + normalize the model's JSON output (tolerating accidental fences).

    Only genuinely unusable output (non-JSON / not an object) raises. The model's
    ``eventType`` and ``severity`` are NOT authoritative -- the ingest layer sets
    event_type from the Item codes and severity from the deterministic rubric -- so
    out-of-enum values from the model are coerced rather than allowed to crash the
    run. We keep the fields the model actually contributes (isAbrupt, affectedRole,
    summary, confidence) clean.
    """
    body = raw.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.find("{") : body.rfind("}") + 1]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ClassificationError(f"model did not return valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ClassificationError("model output is not a JSON object")
    # Coerce non-authoritative fields instead of raising.
    if data.get("eventType") not in EVENT_TYPES:
        data["eventType"] = "other"
    if data.get("severity") not in SEVERITIES:
        data["severity"] = None
    conf = data.get("confidence")
    if conf is not None:
        try:
            data["confidence"] = min(1.0, max(0.0, float(conf)))
        except (TypeError, ValueError):
            data["confidence"] = None
    summary = data.get("summary")
    if isinstance(summary, str) and len(summary) > 240:
        data["summary"] = summary[:240]
    return data


class AnthropicClassifier:
    """Real hosted-model classifier (requires ANTHROPIC_API_KEY)."""

    def __init__(
        self,
        client: HttpClient,
        settings: Settings | None = None,
        *,
        max_tokens: int = 600,
    ) -> None:
        self._client = client
        self._settings = settings or get_settings()
        if not self._settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is required for 8-K classification (HARD RULE #9)."
            )
        self._model = self._settings.anthropic_model
        self._max_tokens = max_tokens

    def classify(self, *, text: str, item_codes: list[str], entity_name: str) -> ClassificationResult:
        user = (
            f"Item codes: {', '.join(item_codes)}\n"
            f"Issuer: {entity_name}\n\n"
            f"Output JSON matching exactly this schema:\n{json.dumps(OUTPUT_SCHEMA)}\n\n"
            f"8-K plain text:\n{text}"
        )
        resp = self._client.post(
            _ANTHROPIC_URL,
            headers={
                "x-api-key": self._settings.anthropic_api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user}],
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        out_text = "".join(
            block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text"
        )
        usage = payload.get("usage", {})
        return ClassificationResult(
            data=validate_classification(out_text),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )


class OllamaClassifier:
    """Free, LOCAL classifier via Ollama (no API key, no per-call cost).

    A capable local model (e.g. llama3.1, qwen2.5) is plenty here because the event
    TYPE is already fixed by the Item codes and severity is overridden by the rubric;
    the model only fills specifics + a proposed severity. Requires a running Ollama
    server with the model pulled (``ollama pull llama3.1``)."""

    def __init__(self, client: HttpClient, *, model: str = "llama3.1",
                 url: str = "http://localhost:11434/api/chat") -> None:
        self._client = client
        self._model = model
        self._url = url

    def classify(self, *, text: str, item_codes: list[str], entity_name: str) -> ClassificationResult:
        user = (
            f"Item codes: {', '.join(item_codes)}\nIssuer: {entity_name}\n\n"
            f"Output JSON matching exactly this schema:\n{json.dumps(OUTPUT_SCHEMA)}\n\n"
            f"8-K plain text:\n{text}"
        )
        try:
            resp = self._client.post(
                self._url,
                json={
                    "model": self._model,
                    "format": "json",
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user},
                    ],
                },
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - server down / unreachable
            raise ClassificationError(f"Ollama request failed: {exc}") from exc
        payload = resp.json()
        out_text = (payload.get("message") or {}).get("content", "")
        return ClassificationResult(
            data=validate_classification(out_text),
            input_tokens=int(payload.get("prompt_eval_count", 0)),
            output_tokens=int(payload.get("eval_count", 0)),
        )


def get_classifier(client: HttpClient, settings: Settings | None = None) -> Classifier | None:
    """Pick the 8-K classifier from config. Returns None (skip 8-K) only when the
    hosted provider is selected but no ANTHROPIC_API_KEY is set."""
    settings = settings or get_settings()
    if settings.eight_k_provider.lower() == "ollama":
        return OllamaClassifier(client, model=settings.ollama_model, url=settings.ollama_chat_url)
    if settings.anthropic_api_key:
        return AnthropicClassifier(client, settings)
    return None
