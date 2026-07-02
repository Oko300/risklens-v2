"""
services/usage_service.py — RiskLens v2
=========================================
Daily usage limit enforcement with per-user timezone reset.

Free tier: 2 analyses per day, reset at midnight in the USER's timezone.
This means a user in Lagos (WAT, UTC+1) gets a fresh 2 analyses at
midnight Lagos time — not midnight UTC. This makes the free tier feel
fair and personal rather than arbitrary.

How timezone-aware daily limits work:
  1. User's timezone is stored in public.users.timezone (default 'UTC')
  2. On every tool run, we compute the user's local date:
       local_date = datetime.now(ZoneInfo(user.timezone)).date()
  3. We count usage_logs rows where user_id = X AND usage_date = local_date
  4. If count >= FREE_DAILY_LIMIT, we reject the request

Plan limits:
  Free     → 2 analyses/day, resets at midnight local time
  Pro      → unlimited
  Business → unlimited (+ team features)
"""

import asyncio
from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FREE_DAILY_LIMIT = 2


def _get_user_local_date(timezone_str: str) -> date:
    """
    Returns today's date in the user's timezone.
    Falls back to UTC if the timezone string is invalid.
    """
    try:
        tz = ZoneInfo(timezone_str or "UTC")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


async def get_today_usage_count(user_id: str, user_timezone: str) -> int:
    """
    Returns how many analyses the user has run today (in their timezone).
    Queries the usage_logs table.
    """
    from db.client import get_admin_client

    local_date = _get_user_local_date(user_timezone)
    date_str = local_date.isoformat()   # e.g. '2025-06-26'

    def _query():
        client = get_admin_client()
        result = (
            client.table("usage_logs")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .eq("usage_date", date_str)
            .execute()
        )
        return result.count or 0

    return await asyncio.to_thread(_query)


async def check_usage_allowed(
    user_id: str,
    user_timezone: str,
    plan: str,
) -> tuple[bool, Optional[str]]:
    """
    Returns (allowed: bool, error_message: Optional[str]).

    Called before every tool run. Pro and Business users are always
    allowed. Free users are checked against the daily limit.

    Returns:
        (True, None)            — allowed, proceed
        (False, error_message)  — blocked, show error to user
    """
    if plan in ("pro", "business"):
        return True, None

    count = await get_today_usage_count(user_id, user_timezone)

    if count >= FREE_DAILY_LIMIT:
        tz_label = user_timezone or "UTC"
        return False, (
            f"Free tier limit reached: {count}/{FREE_DAILY_LIMIT} analyses used today "
            f"(resets at midnight {tz_label}). "
            "Upgrade to Pro for unlimited analyses."
        )

    return True, None


async def log_usage(user_id: str, user_timezone: str, tool_name: str, ticker: str) -> None:
    """
    Inserts a usage_log row for this tool run.
    Called AFTER a successful tool run — failed runs don't count.
    """
    from db.client import get_admin_client

    local_date = _get_user_local_date(user_timezone)

    def _insert():
        client = get_admin_client()
        client.table("usage_logs").insert({
            "user_id":    user_id,
            "tool_name":  tool_name,
            "ticker":     ticker.upper() if ticker else None,
            "usage_date": local_date.isoformat(),
        }).execute()

    await asyncio.to_thread(_insert)


async def get_usage_summary(user_id: str, user_timezone: str, plan: str) -> dict:
    """
    Returns a usage summary dict for the /subscriptions/me endpoint.
    Shows today's usage and remaining analyses.
    """
    count = await get_today_usage_count(user_id, user_timezone)
    local_date = _get_user_local_date(user_timezone)

    if plan in ("pro", "business"):
        return {
            "plan":           plan,
            "today_count":    count,
            "daily_limit":    None,         # unlimited
            "remaining":      None,
            "reset_date":     local_date.isoformat(),
            "reset_timezone": user_timezone or "UTC",
        }

    remaining = max(0, FREE_DAILY_LIMIT - count)
    return {
        "plan":           "free",
        "today_count":    count,
        "daily_limit":    FREE_DAILY_LIMIT,
        "remaining":      remaining,
        "reset_date":     local_date.isoformat(),
        "reset_timezone": user_timezone or "UTC",
    }
