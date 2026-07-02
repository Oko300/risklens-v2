"""
db/client.py — RiskLens v2
============================
Supabase client singleton.

Two clients are maintained:
  - anon_client  — uses the ANON key; respects Row Level Security.
                   Used for all user-authenticated operations where
                   the JWT is passed in the request header.
  - admin_client — uses the SERVICE ROLE key; bypasses RLS.
                   Used ONLY for server-side operations (webhook
                   handlers, background jobs, admin actions).
                   Never expose this client to user-controlled input.

Both clients are created once at import time and reused. This avoids
the overhead of creating a new httpx connection pool per request.
"""

import os
from functools import lru_cache
from supabase import create_client, Client

_SUPABASE_URL           = os.environ["SUPABASE_URL"]
_SUPABASE_ANON_KEY      = os.environ["SUPABASE_ANON_KEY"]
_SUPABASE_SERVICE_KEY   = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


@lru_cache(maxsize=1)
def get_anon_client() -> Client:
    """
    Returns the singleton anon (public) Supabase client.
    Respects RLS — all operations run as the requesting user.
    Pass the user's JWT via: client.auth.set_session(access_token, refresh_token)
    or use the admin client and filter by user_id explicitly.
    """
    return create_client(_SUPABASE_URL, _SUPABASE_ANON_KEY)


@lru_cache(maxsize=1)
def get_admin_client() -> Client:
    """
    Returns the singleton admin (service role) Supabase client.
    Bypasses RLS — use only for trusted server-side operations.
    NEVER use this for anything driven by user input without
    explicit user_id filtering.
    """
    return create_client(_SUPABASE_URL, _SUPABASE_SERVICE_KEY)


# Convenience aliases used throughout the codebase
supabase: Client      = None   # populated on first call to get_anon_client()
admin_db: Client      = None   # populated on first call to get_admin_client()


def init_clients() -> None:
    """
    Called once from api/main.py startup to warm both clients.
    Avoids cold-start latency on the first real request.
    """
    global supabase, admin_db
    supabase = get_anon_client()
    admin_db = get_admin_client()
