"""FastAPI application factory (Phase 5).

Read-only service over Supabase. ``/health`` stays cheap and dependency-free; the
five signal routes live under /signals and are gated by the RapidAPI proxy secret.
``create_app`` lets tests build an app with a specific settings object (e.g. with
the proxy secret set) and override the repository dependency.
"""

from __future__ import annotations

from fastapi import FastAPI

from api import __version__
from api.auth import RapidApiProxyMiddleware
from api.routes import router as signals_router
from core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Corporate Intent Intelligence API",
        version=__version__,
        description=(
            "Interpreted B2B signals from SEC EDGAR (Form D + 8-K) and public ATS "
            "job boards. Read-only. Funding, hiring velocity, material risks, and "
            "the funded-and-hiring join."
        ),
    )
    app.add_middleware(
        RapidApiProxyMiddleware,
        secret=settings.rapidapi_proxy_secret,
        header=settings.rapidapi_proxy_header,
    )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness probe. Returns 200 with a tiny JSON body; touches nothing."""
        return {"status": "ok", "service": "signals-api", "version": __version__}

    app.include_router(signals_router)
    return app


app = create_app()
