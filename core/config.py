"""Central configuration / env loading — the single source for secrets and knobs.

Values come from environment variables (and a local ``.env`` for development).
Secrets default to empty strings so the API can boot for ``/health`` without a
full secret set; the components that actually NEED a given secret enforce it at
the point of use (e.g. the HTTP client refuses sec.gov requests without
``SEC_USER_AGENT``, and the Supabase wrapper refuses to connect without keys).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed view of the environment. Field names map case-insensitively to the
    env vars documented in ``.env.example`` (playbook §1.2)."""

    model_config = SettingsConfigDict(
        env_file=".env",
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

    # --- 8-K classification (Phase 4) — HOSTED model only --------------------
    anthropic_api_key: str = ""
    # Hosted model for 8-K classification (low volume, high stakes — spec §4 says
    # this is the one place to pay the quality premium). Override via env if needed.
    anthropic_model: str = "claude-sonnet-4-6"

    # --- Optional domain resolver (Phase 2) ----------------------------------
    domain_resolver_api_key: str = ""

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


@lru_cache
def get_settings() -> Settings:
    """Process-wide singleton. Cached so every component shares one config."""
    return Settings()
