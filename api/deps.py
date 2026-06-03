"""FastAPI dependencies. ``get_repository`` is overridden in tests via
``app.dependency_overrides`` to inject a fake repository (no database)."""

from __future__ import annotations

from api.repository import Repository, SupabaseRepository


def get_repository() -> Repository:
    return SupabaseRepository()
