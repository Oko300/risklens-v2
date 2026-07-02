"""
api/routers/subscriptions.py — RiskLens v2
============================================
Subscription management routes.

GET  /subscriptions/me       — current plan, status, and today's usage
POST /subscriptions/upgrade  — get a Paystack payment link

Paystack plan codes are read from environment variables so you can
change plans in the Paystack dashboard without a code deploy:
  PAYSTACK_PRO_PLAN_CODE      (e.g. 'PLN_xxxxxxxxxxxx')
  PAYSTACK_BUSINESS_PLAN_CODE (e.g. 'PLN_xxxxxxxxxxxx')

How to find plan codes:
  Paystack Dashboard → Subscriptions → Plans → click a plan → copy the code
"""

import asyncio
import os
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_current_user, get_subscription
from api.schemas.subscriptions import (
    SubscriptionOut, UpgradeLinkRequest, UpgradeLinkResponse,
)

router = APIRouter()

_PAYSTACK_SECRET      = os.environ.get("PAYSTACK_SECRET_KEY", "")
_PAYSTACK_BASE        = "https://api.paystack.co"
_PLAN_CODES: dict[str, str] = {
    "pro":      os.environ.get("PAYSTACK_PRO_PLAN_CODE", ""),
    "business": os.environ.get("PAYSTACK_BUSINESS_PLAN_CODE", ""),
}


# ---------------------------------------------------------------------------
# Get current subscription + usage summary
# ---------------------------------------------------------------------------

@router.get("/me", response_model=SubscriptionOut)
async def get_my_subscription(
    user: Annotated[dict, Depends(get_current_user)],
    sub:  Annotated[dict, Depends(get_subscription)],
):
    """
    Returns the user's current plan, status, and today's usage.

    usage.remaining is None for Pro/Business (unlimited).
    usage.daily_limit is None for Pro/Business.
    """
    from services.usage_service import get_usage_summary

    plan = sub.get("plan", "free")
    usage = await get_usage_summary(
        user_id=user["id"],
        user_timezone=user.get("timezone", "UTC"),
        plan=plan,
    )

    return SubscriptionOut(
        id=sub["id"],
        user_id=sub["user_id"],
        plan=plan,
        status=sub.get("status", "active"),
        current_period_start=sub.get("current_period_start"),
        current_period_end=sub.get("current_period_end"),
        team_seats=sub.get("team_seats", 1),
        created_at=sub["created_at"],
        updated_at=sub["updated_at"],
        usage=usage,
    )


# ---------------------------------------------------------------------------
# Get Paystack payment link
# ---------------------------------------------------------------------------

@router.post("/upgrade", response_model=UpgradeLinkResponse)
async def get_upgrade_link(
    body: UpgradeLinkRequest,
    user: Annotated[dict, Depends(get_current_user)],
    sub:  Annotated[dict, Depends(get_subscription)],
):
    """
    Generates a Paystack subscription payment link for the requested plan.

    The user is redirected to this URL to complete payment. On success,
    Paystack fires a webhook to POST /webhooks/paystack, which activates
    the subscription automatically.

    Plan codes must be configured in your Paystack dashboard and set
    in the PAYSTACK_PRO_PLAN_CODE / PAYSTACK_BUSINESS_PLAN_CODE env vars.
    """
    if body.plan not in ("pro", "business"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="plan must be 'pro' or 'business'",
        )

    if sub.get("plan") == body.plan and sub.get("status") == "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You are already on the {body.plan.upper()} plan.",
        )

    plan_code = _PLAN_CODES.get(body.plan, "")
    if not plan_code:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"PAYSTACK_{body.plan.upper()}_PLAN_CODE is not configured. "
                   "Contact support.",
        )

    if not _PAYSTACK_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment system not configured. Contact support.",
        )

    email_to_use = body.email or user.get("email", "")
    reference    = f"rl_{body.plan}_{user['id'][:8]}_{uuid.uuid4().hex[:8]}"

    # Create a Paystack subscription initialization
    payment_url = await _create_paystack_link(
        email=email_to_use,
        plan_code=plan_code,
        reference=reference,
        metadata={
            "user_id":     user["id"],
            "plan":        body.plan,
            "risklens_v":  "2",
        },
    )

    return UpgradeLinkResponse(
        plan=body.plan,
        payment_url=payment_url,
        reference=reference,
        message=f"Complete your {body.plan.upper()} upgrade at the payment link.",
    )


# ---------------------------------------------------------------------------
# Paystack API helper
# ---------------------------------------------------------------------------

async def _create_paystack_link(
    email: str, plan_code: str, reference: str, metadata: dict,
) -> str:
    """
    Calls Paystack's /transaction/initialize endpoint.
    Returns the authorization_url to redirect the user to.
    """
    headers = {
        "Authorization": f"Bearer {_PAYSTACK_SECRET}",
        "Content-Type":  "application/json",
    }
    payload = {
        "email":     email,
        "plan":      plan_code,
        "reference": reference,
        "metadata":  metadata,
        # Paystack amount is in kobo (NGN) or smallest currency unit.
        # For plan-based subscriptions the amount is set on the plan itself
        # so this is typically 0 here — Paystack uses the plan's amount.
        "amount":    0,
    }

    def _request():
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{_PAYSTACK_BASE}/transaction/initialize",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    try:
        data = await asyncio.to_thread(_request)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack error: {exc.response.status_code} — {exc.response.text[:200]}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach Paystack: {exc}",
        )

    if not data.get("status"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack returned error: {data.get('message', 'unknown')}",
        )

    url = data.get("data", {}).get("authorization_url", "")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Paystack did not return a payment URL.",
        )

    return url
