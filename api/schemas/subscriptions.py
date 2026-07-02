"""api/schemas/subscriptions.py — Subscription and usage response models."""

from typing import Optional
from pydantic import BaseModel


class UsageSummary(BaseModel):
    plan:           str
    today_count:    int
    daily_limit:    Optional[int]    # None = unlimited
    remaining:      Optional[int]    # None = unlimited
    reset_date:     str              # ISO date in user's timezone
    reset_timezone: str


class SubscriptionOut(BaseModel):
    id:                         str
    user_id:                    str
    plan:                       str
    status:                     str
    current_period_start:       Optional[str]
    current_period_end:         Optional[str]
    team_seats:                 int
    created_at:                 str
    updated_at:                 str
    # Injected at response time, not stored
    usage:                      UsageSummary


class UpgradeLinkRequest(BaseModel):
    plan:  str   # 'pro' | 'business'
    email: Optional[str] = None   # pre-fill Paystack checkout


class UpgradeLinkResponse(BaseModel):
    plan:         str
    payment_url:  str
    reference:    str
    message:      str
