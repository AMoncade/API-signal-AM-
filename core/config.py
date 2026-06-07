"""Central configuration / env loading — the single source for secrets and knobs.

Values come from environment variables (and a local ``.env`` for development).
Secrets default to empty strings so the API can boot for ``/health`` without a
full secret set; the components that actually NEED a given secret enforce it at
the point of use (e.g. the HTTP client refuses sec.gov requests without
``SEC_USER_AGENT``, and the Supabase wrapper refuses to connect without keys).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path to the repo's .env (one level up from this file). Using an absolute
# path means the worker/API find .env no matter which directory they are started
# from, so `python -m worker ...` works from anywhere (not just the repo root).
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Typed view of the environment. Field names map case-insensitively to the
    env vars documented in ``.env.example`` (playbook §1.2)."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- SEC EDGAR -----------------------------------------------------------
    # HARD RULE #1: a descriptive User-Agent is mandatory on every sec.gov
    # request; its absence is the #1 cause of 403s.
    sec_user_agent: str = ""

    # --- Supabase (worker writes + api reads, both with the service-role key) -
    supabase_url: str = ""
    supabase_service_key: str = ""

    # --- 8-K classification (Phase 4) ----------------------------------------
    # Provider for the 8-K prose step:
    #   'anthropic' (hosted, high quality, costs per call) or
    #   'ollama'    (a LOCAL model via Ollama — free, no API key, no per-call cost).
    eight_k_provider: str = "anthropic"
    anthropic_api_key: str = ""
    # Hosted model. Default Sonnet; set ANTHROPIC_MODEL=claude-haiku-4-5-20251001 for
    # ~10x cheaper + faster classification (good enough since Item codes are
    # deterministic and the model only fills specifics + severity).
    anthropic_model: str = "claude-sonnet-4-6"
    # Local model (used when eight_k_provider='ollama'). Free; needs Ollama running.
    ollama_chat_url: str = "http://localhost:11434/api/chat"
    ollama_model: str = "llama3.1"
    # Cost control: skip the low-value, high-volume 8-K items (8.01 Other, 1.01/1.02
    # contract changes) so the model is only called on genuinely material events.
    eight_k_skip_low_value: bool = False

    # --- Demo mode (Phase 5 / dashboard) -------------------------------------
    # When true, the read API serves seeded SAMPLE data (api/demo_data.py) with no
    # database and no supabase package, so the /demo dashboard renders populated.
    # Strictly opt-in; leave false in production.
    signals_demo_data: bool = False

    # --- RapidAPI proxy secret (Phase 5) -------------------------------------
    rapidapi_proxy_secret: str = ""
    # Header RapidAPI injects with the proxy secret. CONFIRM against current
    # provider docs before launch (it has changed historically); configurable so a
    # rename can be fixed via env, not a redeploy.
    rapidapi_proxy_header: str = "X-RapidAPI-Proxy-Secret"

    # --- Shared HTTP client knobs --------------------------------------------
    # HARD RULE #1: <= 10 requests/second across ALL sec.gov domains. The cap is
    # enforced GLOBALLY in the one shared client, so keep this <= 10.
    http_max_requests_per_second: float = 10.0
    http_timeout_seconds: float = 30.0
    # Transient-failure retries (429/5xx + network errors). Each retry re-acquires
    # the global rate slot, so retrying never breaches the req/s cap. 0 disables.
    http_max_retries: int = 3
    http_retry_backoff_seconds: float = 0.5

    @field_validator("signals_demo_data", mode="before")
    @classmethod
    def _blank_bool_is_false(cls, v: object) -> object:
        # An empty env value (e.g. `SIGNALS_DEMO_DATA=` in .env) means "off", not a
        # parse error. Without this, a blank line crashes Settings() on boot.
        if isinstance(v, str) and v.strip() == "":
            return False
        return v


@lru_cache
def get_settings() -> Settings:
    """Process-wide singleton. Cached so every component shares one config."""
    return Settings()
