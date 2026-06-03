"""The ONE Supabase client wrapper.

Both the worker (writes) and the read API (reads) connect with the SERVICE-ROLE
key, which bypasses RLS. The tables have RLS enabled with no anon policies, so
they are not publicly readable via Supabase's auto-generated API — see the
SECURITY section of /docs/schema.sql.
"""

from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from core.config import get_settings


@lru_cache
def get_supabase() -> Client:
    """Return a process-wide Supabase client built from the service-role key.

    Raises a clear error if the connection settings are missing, rather than
    failing deep inside a query later.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set to connect to "
            "Supabase. Copy .env.example to .env and fill them in."
        )
    return create_client(settings.supabase_url, settings.supabase_service_key)
