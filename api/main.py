"""FastAPI application factory (Phase 5).

Read-only service over Supabase. ``/health`` stays cheap and dependency-free; the
five signal routes live under /signals and are gated by the RapidAPI proxy secret.
``create_app`` lets tests build an app with a specific settings object (e.g. with
the proxy secret set) and override the repository dependency.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api import __version__
from api.auth import RapidApiProxyMiddleware
from api.routes import router as signals_router
from core.config import Settings, get_settings

# The single-file demo dashboard (sales demo / local smoke check). Served at /demo
# so the page and its /signals fetches share an origin (no CORS needed there).
_DEMO_PAGE = Path(__file__).resolve().parent.parent / "demo" / "dashboard.html"


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
    # Outermost (added last = runs first): permissive read-only CORS so a browser
    # page hosted elsewhere (or opened from disk) can call the API. This does NOT
    # weaken auth - the proxy-secret gate above still runs on every /signals call.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness probe. Returns 200 with a tiny JSON body; touches nothing."""
        return {"status": "ok", "service": "signals-api", "version": __version__}

    @app.get("/demo", tags=["meta"], include_in_schema=False)
    def demo() -> FileResponse:
        """The bundled demo dashboard (demo/dashboard.html). Static shell only."""
        return FileResponse(_DEMO_PAGE, media_type="text/html")

    app.include_router(signals_router)
    return app


app = create_app()
