"""FastAPI application factory + the Phase 0 healthcheck.

This service is READ-ONLY against Supabase. No signal logic exists yet — the five
signal endpoints (/signals/...) are added in Phase 5. Keep ``/health`` cheap and
dependency-free so an uptime probe never depends on the database being reachable.
"""

from __future__ import annotations

from fastapi import FastAPI

from api import __version__

app = FastAPI(
    title="Corporate Intent Intelligence API",
    version=__version__,
    description=(
        "Interpreted B2B signals from SEC EDGAR (Form D + 8-K) and public ATS "
        "job boards. Read-only. (Phase 0 scaffold — only /health is live.)"
    ),
)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe. Returns 200 with a tiny JSON body; touches nothing."""
    return {"status": "ok", "service": "signals-api", "version": __version__}
