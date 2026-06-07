"""Deterministic severity rubric (Appendix B), applied as an OVERRIDE after the LLM.

The LLM proposes specifics + a severity from the prose; we then clamp/override
severity from the structured facts so a model slip can't mislabel a critical event.
"""

from __future__ import annotations

_C_SUITE = {"ceo", "cfo", "chair", "chairman", "chairperson", "chief executive", "chief financial"}

# eventType -> fixed severity (independent of the prose).
_FIXED: dict[str, str] = {
    "bankruptcy": "critical",
    "restatement": "critical",
    "restructuring_layoffs": "high",
    "impairment": "high",
    "debt_acceleration": "high",
    "delisting_risk": "high",
    "cyber_incident": "high",
    "auditor_change": "medium",
    "contract_change": "low",
}


def apply_severity_override(
    event_type: str,
    *,
    is_abrupt: bool | None = None,
    affected_role: str | None = None,
) -> str:
    """Return the authoritative severity for an event, regardless of the LLM's guess.

    - Fixed-severity events use the rubric value directly.
    - exec_departure: high when abrupt OR a CEO/CFO/Chair is involved, else medium.
    - other / unknown: medium (it reached us as event-bearing, so not 'low').
    """
    if event_type in _FIXED:
        return _FIXED[event_type]
    if event_type == "exec_departure":
        # defensive: affected_role should be a string by now, but never assume
        role = str(affected_role or "").lower()
        if is_abrupt or any(key in role for key in _C_SUITE):
            return "high"
        return "medium"
    return "medium"
