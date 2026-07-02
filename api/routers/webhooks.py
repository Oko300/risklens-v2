"""
api/routers/webhooks.py — RiskLens v2
========================================
Paystack webhook handler.

Paystack fires POST requests to this endpoint whenever a subscription
event occurs. We verify the HMAC-SHA512 signature on every request
before processing — unsigned or incorrectly signed requests are rejected
with 401 immediately.

Events handled:
  charge.success           — one-time or recurring payment succeeded
  subscription.create      — new subscription activated
  subscription.disable     — subscription cancelled/disabled
  invoice.payment_failed   — payment failed (move to past_due)

How to configure in Paystack:
  Paystack Dashboard → Settings → API Keys & Webhooks
  → Webhook URL: https://your-api.render.com/webhooks/paystack
  → Enable: charge.success, subscription.*, invoice.*

Security:
  Paystack signs every webhook with HMAC-SHA512 using your secret key.
  The signature is in the X-Paystack-Signature header.
  We verify this BEFORE doing any DB work — no signature, no action.
"""

import asyncio
import hashlib
import hmac
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter()

_PAYSTACK_SECRET = os.environ.get("PAYSTACK_SECRET_KEY", "").encode()


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@router.post("/paystack", status_code=status.HTTP_200_OK)
async def paystack_webhook(request: Request):
    """
    Receives and processes Paystack webhook events.

    Always returns 200 on verified requests — even ones we don't handle —
    so Paystack doesn't keep retrying. Unhandled events are logged and
    ignored cleanly.
    """
    raw_body = await request.body()

    # ── Signature verification ────────────────────────────────────────────
    if not _verify_paystack_signature(raw_body, request.headers.get("x-paystack-signature", "")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Paystack signature",
        )

    # ── Parse event ───────────────────────────────────────────────────────
    import json
    try:
        event = json.loads(raw_body)
    except json.JSONDecodeError:
        return {"received": True, "processed": False, "reason": "invalid JSON"}

    event_type: str  = event.get("event", "")
    data: dict       = event.get("data", {})

    print(f"[webhook:paystack] received event={event_type}")

    # ── Route to handler ──────────────────────────────────────────────────
    handlers = {
        "charge.success":         _handle_charge_success,
        "subscription.create":    _handle_subscription_create,
        "subscription.disable":   _handle_subscription_disable,
        "invoice.payment_failed": _handle_invoice_payment_failed,
    }

    handler = handlers.get(event_type)
    if handler:
        try:
            await handler(data)
        except Exception as exc:
            # Log but return 200 — Paystack retries on non-2xx, causing duplicates
            print(f"[webhook:paystack] ERROR handling {event_type}: {exc}")
    else:
        print(f"[webhook:paystack] unhandled event type: {event_type} — ignoring")

    return {"received": True, "event": event_type}


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

async def _handle_charge_success(data: dict) -> None:
    """
    Fires when a payment is successful (first payment or renewal).
    Activates the subscription if it was previously lapsed.
    """
    metadata     = data.get("metadata") or {}
    user_id      = metadata.get("user_id")
    plan         = metadata.get("plan")
    customer     = data.get("customer", {})
    customer_id  = str(customer.get("id", ""))
    customer_code= customer.get("customer_code", "")

    if not user_id or not plan:
        print("[webhook:paystack] charge.success missing user_id or plan in metadata")
        return

    await _upsert_subscription(
        user_id=user_id,
        plan=plan,
        status="active",
        paystack_customer_id=customer_id or customer_code,
    )
    print(f"[webhook:paystack] charge.success → user={user_id} plan={plan} activated")


async def _handle_subscription_create(data: dict) -> None:
    """
    Fires when a Paystack subscription is created.
    Contains the subscription_code used for cancellation.
    """
    customer          = data.get("customer", {})
    customer_code     = str(customer.get("id", "")) or customer.get("customer_code", "")
    subscription_code = data.get("subscription_code", "")
    plan_data         = data.get("plan", {})
    plan_code         = plan_data.get("plan_code", "")
    next_date         = data.get("next_payment_date")

    # Look up user by Paystack customer info
    user_id = await _find_user_by_paystack_customer(customer_code)
    if not user_id:
        print(f"[webhook:paystack] subscription.create — user not found for customer={customer_code}")
        return

    # Determine plan from plan_code
    plan = _plan_from_code(plan_code)

    update = {
        "plan":                       plan,
        "status":                     "active",
        "paystack_customer_id":       customer_code,
        "paystack_subscription_code": subscription_code,
        "paystack_plan_code":         plan_code,
    }
    if next_date:
        update["current_period_end"] = next_date

    await _update_subscription_by_user(user_id, update)
    print(f"[webhook:paystack] subscription.create → user={user_id} plan={plan}")


async def _handle_subscription_disable(data: dict) -> None:
    """
    Fires when a subscription is cancelled or disabled.
    Moves the user to 'cancelled' status — NOT back to free immediately,
    they keep access until current_period_end.
    """
    subscription_code = data.get("subscription_code", "")
    if not subscription_code:
        return

    def _op():
        from db.client import get_admin_client
        get_admin_client().table("subscriptions").update(
            {"status": "cancelled"}
        ).eq("paystack_subscription_code", subscription_code).execute()

    await asyncio.to_thread(_op)
    print(f"[webhook:paystack] subscription.disable → code={subscription_code} → cancelled")


async def _handle_invoice_payment_failed(data: dict) -> None:
    """
    Fires when a renewal payment fails.
    Marks subscription as 'past_due' — the user can retry payment.
    """
    subscription     = data.get("subscription", {})
    subscription_code = subscription.get("subscription_code", "")
    if not subscription_code:
        return

    def _op():
        from db.client import get_admin_client
        get_admin_client().table("subscriptions").update(
            {"status": "past_due"}
        ).eq("paystack_subscription_code", subscription_code).execute()

    await asyncio.to_thread(_op)
    print(f"[webhook:paystack] invoice.payment_failed → code={subscription_code} → past_due")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _upsert_subscription(
    user_id: str,
    plan: str,
    status: str,
    paystack_customer_id: str = "",
) -> None:
    def _op():
        from db.client import get_admin_client
        update: dict[str, Any] = {"plan": plan, "status": status}
        if paystack_customer_id:
            update["paystack_customer_id"] = paystack_customer_id
        get_admin_client().table("subscriptions").update(update).eq("user_id", user_id).execute()

    await asyncio.to_thread(_op)


async def _update_subscription_by_user(user_id: str, fields: dict) -> None:
    def _op():
        from db.client import get_admin_client
        get_admin_client().table("subscriptions").update(fields).eq("user_id", user_id).execute()

    await asyncio.to_thread(_op)


async def _find_user_by_paystack_customer(customer_code: str) -> str | None:
    """
    Find a user_id by their Paystack customer code stored in subscriptions.
    Falls back to checking if the customer code matches a stored customer_id.
    """
    def _op():
        from db.client import get_admin_client
        result = (
            get_admin_client()
            .table("subscriptions")
            .select("user_id")
            .eq("paystack_customer_id", customer_code)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0]["user_id"] if rows else None

    return await asyncio.to_thread(_op)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_paystack_signature(body: bytes, signature: str) -> bool:
    """
    Verify the X-Paystack-Signature header using HMAC-SHA512.
    Returns True only if the signature matches exactly.
    """
    if not _PAYSTACK_SECRET or not signature:
        return False
    expected = hmac.new(_PAYSTACK_SECRET, body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def _plan_from_code(plan_code: str) -> str:
    """Map Paystack plan_code back to our plan name."""
    pro_code      = os.environ.get("PAYSTACK_PRO_PLAN_CODE", "")
    business_code = os.environ.get("PAYSTACK_BUSINESS_PLAN_CODE", "")
    if plan_code == pro_code:
        return "pro"
    if plan_code == business_code:
        return "business"
    return "pro"  # safe default for unrecognised codes
