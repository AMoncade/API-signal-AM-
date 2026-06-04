"""FastAPI dependencies. ``get_repository`` is overridden in tests via
``app.dependency_overrides`` to inject a fake repository (no database)."""

from __future__ import annotations

from api.repository import Repository, SupabaseRepository
from core.config import get_settings


def get_repository() -> Repository:
    # Opt-in demo mode: serve seeded sample data with no DB / no supabase package.
    # Lets the /demo dashboard render fully populated for a sales demo or smoke test.
    if get_settings().signals_demo_data:
        from api.demo_data import DemoRepository  # lazy: only imported in demo mode

        return DemoRepository()
    return SupabaseRepository()
