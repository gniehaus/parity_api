import os
import secrets
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import get_parity_user_id
from .db import (
    get_subscription_access,
    start_complimentary_snapshot,
    sync_parity_subscription,
)


router = APIRouter(
    prefix="/api/subscriptions",
    tags=["subscriptions"],
)


SUBSCRIPTION_SYNC_SECRET = os.getenv(
    "SUBSCRIPTION_SYNC_SECRET"
)


SubscriptionTier = Literal[
    "free",
    "connected",
    "complete",
]

SubscriptionStatus = Literal[
    "none",
    "incomplete",
    "trialing",
    "active",
    "past_due",
    "canceled",
    "unpaid",
    "paused",
]

SubscriptionEventType = Literal[
    "signup",
    "upgrade",
    "downgrade_scheduled",
    "downgrade_applied",
    "cancel_scheduled",
    "canceled",
    "reactivated",
    "payment_failed",
    "payment_recovered",
    "admin_correction",
]


class SubscriptionSyncRequest(BaseModel):
    """
    Full subscription state sent by the trusted billing workflow.

    event_key must be unique. Use the billing provider's event ID when
    available. Repeating the same event_key is safe and will not apply the
    change twice.
    """

    event_key: str = Field(min_length=1, max_length=255)
    parity_user_id: str = Field(min_length=1, max_length=255)
    event_type: SubscriptionEventType

    subscription_tier: SubscriptionTier
    subscription_status: SubscriptionStatus

    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    stripe_price_id: str | None = None

    current_period_start: datetime | None = None
    current_period_end: datetime | None = None

    cancel_at_period_end: bool = False
    canceled_at: datetime | None = None

    pending_tier: Literal[
        "connected",
        "complete",
    ] | None = None
    pending_change_at: datetime | None = None

    access_grace_until: datetime | None = None
    event_data: dict[str, Any] = Field(default_factory=dict)


def verify_subscription_sync_secret(
    supplied_secret: str | None,
) -> None:
    """
    Prevents an untrusted browser from assigning itself a paid tier.

    SUBSCRIPTION_SYNC_SECRET must exist only in Render and in the trusted
    Base44 backend action that sends subscription updates. Never place it in
    browser JavaScript.
    """

    if not SUBSCRIPTION_SYNC_SECRET:
        raise HTTPException(
            status_code=503,
            detail=(
                "Subscription synchronization is not configured"
            ),
        )

    if (
        not supplied_secret
        or not secrets.compare_digest(
            supplied_secret,
            SUBSCRIPTION_SYNC_SECRET,
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid subscription synchronization secret",
        )


def datetime_to_iso(
    value: datetime | None,
) -> str | None:
    return value.isoformat() if value else None


@router.post("/sync")
def subscription_sync(
    payload: SubscriptionSyncRequest,
    x_subscription_sync_secret: str | None = Header(
        default=None,
        alias="X-Subscription-Sync-Secret",
    ),
):
    """
    Trusted push endpoint.

    Call this after signup, upgrade, downgrade, cancellation, reactivation,
    or a material payment-status change.
    """

    verify_subscription_sync_secret(
        x_subscription_sync_secret
    )

    try:
        subscription = sync_parity_subscription(
            parity_user_id=payload.parity_user_id,
            event_key=payload.event_key,
            event_type=payload.event_type,
            subscription_tier=payload.subscription_tier,
            subscription_status=payload.subscription_status,
            stripe_customer_id=payload.stripe_customer_id,
            stripe_subscription_id=(
                payload.stripe_subscription_id
            ),
            stripe_price_id=payload.stripe_price_id,
            current_period_start=datetime_to_iso(
                payload.current_period_start
            ),
            current_period_end=datetime_to_iso(
                payload.current_period_end
            ),
            cancel_at_period_end=(
                payload.cancel_at_period_end
            ),
            canceled_at=datetime_to_iso(
                payload.canceled_at
            ),
            pending_tier=payload.pending_tier,
            pending_change_at=datetime_to_iso(
                payload.pending_change_at
            ),
            access_grace_until=datetime_to_iso(
                payload.access_grace_until
            ),
            event_data=payload.event_data,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Subscription state could not be saved",
        ) from exc

    return {
        "status": "synced",
        "subscription": subscription,
    }


@router.get("/access")
def subscription_access(request: Request):
    """
    Authenticated pull endpoint used by the frontend.

    The user ID comes from the existing authentication layer, not from a URL
    parameter or request body.
    """

    parity_user_id = get_parity_user_id(request)
    access = get_subscription_access(parity_user_id)

    if not access:
        raise HTTPException(
            status_code=404,
            detail="Parity user does not exist",
        )

    return access


@router.post("/snapshot/start")
def complimentary_snapshot_start(request: Request):
    """
    Starts the authenticated user's one complimentary 24-hour snapshot.
    Calling the endpoint again does not reset the expiration.
    """

    parity_user_id = get_parity_user_id(request)

    try:
        snapshot = start_complimentary_snapshot(
            parity_user_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    access = get_subscription_access(parity_user_id)

    return {
        "status": "started",
        "snapshot": snapshot,
        "access": access,
    }


def require_subscription_feature(
    request: Request,
    feature_name: str,
) -> dict[str, Any]:
    """
    Server-side feature guard for execution, live connections, exports, etc.

    Example:

        require_subscription_feature(
            request,
            "can_execute_new_orders",
        )

    Frontend gating is useful for display, but sensitive API endpoints should
    call this function too.
    """

    parity_user_id = get_parity_user_id(request)
    access = get_subscription_access(parity_user_id)

    if not access:
        raise HTTPException(
            status_code=404,
            detail="Parity user does not exist",
        )

    if feature_name not in access:
        raise HTTPException(
            status_code=500,
            detail=f"Unknown subscription feature: {feature_name}",
        )

    if not access[feature_name]:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Your current plan does not include this feature",
                "required_feature": feature_name,
                "effective_tier": access["effective_tier"],
            },
        )

    return access
