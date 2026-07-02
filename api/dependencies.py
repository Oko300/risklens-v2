"""
api/dependencies.py — RiskLens v2
===================================
FastAPI dependency injection for authentication and authorisation.

Three reusable dependencies:
  get_current_user()    — verifies JWT, returns user dict
  get_subscription()    — returns user's subscription row
  check_usage_limit()   — enforces free-tier daily limit

JWT verification is done LOCALLY using python-jose and the Supabase
JWT secret — no network call to Supabase on every request. This makes
auth fast (< 1ms) and keeps the API responsive under load.

The Supabase JWT secret is found in:
  Supabase Dashboard → Project Settings → API → JWT Secret
"""

import asyncio
import os
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, status as http_status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

_JWT_SECRET = os.environ["SUPABASE_JWT_SECRET"]
_ALGORITHM  = "HS256"
_AUDIENCE   = "authenticated"

_bearer = HTTPBearer(auto_error=True)


# ---------------------------------------------------------------------------
# Core JWT verifier
# ---------------------------------------------------------------------------

def _decode_token(token: str) -> dict:
    """
    Verify and decode a Supabase-issued JWT.
    Raises HTTPException 401 on any failure.
    """
    try:
        payload = jwt.decode(
            token,
            _JWT_SECRET,
            algorithms=[_ALGORITHM],
            audience=_AUDIENCE,
        )
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Dependency: get current user from JWT
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> dict:
    """
    FastAPI dependency. Extracts and verifies the Bearer JWT, then loads
    the matching public.users row from Supabase.

    Returns a dict with user fields: id, email, full_name, ai_provider,
    ai_api_key_enc, ai_model, timezone, created_at, last_active_at.

    Usage in a route:
        @router.get("/me")
        async def me(user: Annotated[dict, Depends(get_current_user)]):
            return user
    """
    payload = _decode_token(credentials.credentials)
    user_id: Optional[str] = payload.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Token payload missing 'sub' claim",
        )

    user = await _fetch_user(user_id)

    if not user:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="User account not found. Please re-register.",
        )

    # Fire-and-forget: update last_active_at without blocking the response
    asyncio.ensure_future(_update_last_active(user_id))

    return user


# ---------------------------------------------------------------------------
# Dependency: get subscription
# ---------------------------------------------------------------------------

async def get_subscription(
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    """
    Returns the user's subscription row.
    The DB trigger guarantees a row exists for every user.
    Falls back to a synthetic free-tier row if missing (should never happen).
    """
    sub = await _fetch_subscription(user["id"])
    if not sub:
        return {"user_id": user["id"], "plan": "free", "status": "active"}
    return sub


# ---------------------------------------------------------------------------
# Combined dependency: user + subscription together
# ---------------------------------------------------------------------------

async def get_user_and_sub(
    user: Annotated[dict, Depends(get_current_user)],
    sub:  Annotated[dict, Depends(get_subscription)],
) -> tuple[dict, dict]:
    """
    Returns (user, subscription) as a tuple.
    FastAPI deduplicates get_current_user — it is called only once
    even when both get_current_user and get_subscription are used.
    """
    return user, sub


# ---------------------------------------------------------------------------
# Dependency: usage gate — enforces daily free-tier limit
# ---------------------------------------------------------------------------

async def check_usage_limit(
    user: Annotated[dict, Depends(get_current_user)],
    sub:  Annotated[dict, Depends(get_subscription)],
) -> tuple[dict, dict]:
    """
    Enforces daily usage limits before a tool run.
    Raises HTTP 429 if the free-tier limit is exhausted.
    Returns (user, sub) so the route handler can use both.
    """
    plan           = sub.get("plan", "free")
    sub_status     = sub.get("status", "active")

    # Lapsed subscriptions fall back to free-tier limits
    effective_plan = plan if sub_status == "active" else "free"

    from services.usage_service import check_usage_allowed
    allowed, error_msg = await check_usage_allowed(
        user_id=user["id"],
        user_timezone=user.get("timezone", "UTC"),
        plan=effective_plan,
    )

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=error_msg,
            headers={"X-RateLimit-Plan": effective_plan},
        )

    return user, sub


# ---------------------------------------------------------------------------
# DB helpers — wrapped in asyncio.to_thread so supabase-py sync calls
# never block the FastAPI event loop
# ---------------------------------------------------------------------------

async def _fetch_user(user_id: str) -> Optional[dict]:
    def _op():
        from db.client import get_admin_client
        result = (
            get_admin_client()
            .table("users")
            .select(
                "id,email,full_name,ai_provider,ai_api_key_enc,"
                "ai_model,timezone,created_at,last_active_at"
            )
            .eq("id", user_id)
            .single()
            .execute()
        )
        return result.data
    try:
        return await asyncio.to_thread(_op)
    except Exception:
        return None


async def _fetch_subscription(user_id: str) -> Optional[dict]:
    def _op():
        from db.client import get_admin_client
        result = (
            get_admin_client()
            .table("subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        return result.data
    try:
        return await asyncio.to_thread(_op)
    except Exception:
        return None


async def _update_last_active(user_id: str) -> None:
    """Non-critical background update — never raises."""
    def _op():
        from db.client import get_admin_client
        from datetime import datetime, timezone
        get_admin_client().table("users").update(
            {"last_active_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", user_id).execute()
    try:
        await asyncio.to_thread(_op)
    except Exception:
        pass
