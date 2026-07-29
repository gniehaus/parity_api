from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal


EASTERN = ZoneInfo("America/New_York")
NYSE = mcal.get_calendar("NYSE")

# Quotes older than this need a fresh user-visible outcome and approval.
MAX_QUOTE_AGE_SECONDS = 300

# Five minutes before the actual market close:
# 3:55pm ET on normal days and 12:55pm ET on early-close days.
ORDER_CUTOFF_MINUTES_BEFORE_CLOSE = 5


class ExecutionSafetyError(ValueError):
    pass


def _as_utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (AttributeError, ValueError) as exc:
        raise ExecutionSafetyError(
            "Quote timestamp is invalid"
        ) from exc

    if parsed.tzinfo is None:
        raise ExecutionSafetyError(
            "Quote timestamp must include a timezone"
        )

    return parsed.astimezone(timezone.utc)


def get_order_submission_window(
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Return the current NYSE session and Parity's order cutoff.

    Uses the NYSE calendar, including market holidays and early closes.
    """

    current_time = now or datetime.now(timezone.utc)
    current_time = current_time.astimezone(timezone.utc)
    eastern_time = current_time.astimezone(EASTERN)

    schedule = NYSE.schedule(
        start_date=eastern_time.date(),
        end_date=eastern_time.date(),
    )

    if schedule.empty:
        return {
            "market_is_open": False,
            "reason": "NYSE is closed today",
            "checked_at": eastern_time.isoformat(),
            "market_open_at": None,
            "order_cutoff_at": None,
            "market_close_at": None,
        }

    market_open_at = schedule.iloc[0]["market_open"].to_pydatetime()
    market_close_at = schedule.iloc[0]["market_close"].to_pydatetime()

    order_cutoff_at = (
        market_close_at
        - timedelta(minutes=ORDER_CUTOFF_MINUTES_BEFORE_CLOSE)
    )

    market_is_open = (
        market_open_at <= current_time < market_close_at
    )

    return {
        "market_is_open": market_is_open,
        "checked_at": eastern_time.isoformat(),
        "market_open_at": market_open_at.isoformat(),
        "order_cutoff_at": order_cutoff_at.isoformat(),
        "market_close_at": market_close_at.isoformat(),
        "reason": None if market_is_open else "NYSE is closed",
    }


def assert_order_submission_window(
    now: datetime | None = None,
) -> dict[str, Any]:
    window = get_order_submission_window(now=now)

    if not window["market_is_open"]:
        raise ExecutionSafetyError(window["reason"])

    current_time = (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )

    cutoff_at = _as_utc_datetime(window["order_cutoff_at"])

    if current_time >= cutoff_at:
        raise ExecutionSafetyError(
            "New orders and replacement orders stop five minutes "
            "before the market closes"
        )

    return window


def validate_execution_order_safety(
    order: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Validate a stored DRAFT order immediately before it can be promoted
    to PREPARED. This function never submits an order.
    """

    if order["status"] != "DRAFT":
        raise ExecutionSafetyError(
            "Only DRAFT orders can be prepared for submission"
        )

    payload = order.get("order_payload") or {}

    if payload.get("order_type") != "LIMIT":
        raise ExecutionSafetyError(
            "Option orders must use a limit price"
        )

    if payload.get("price_effect") not in {
        "DEBIT",
        "CREDIT",
        "EVEN",
    }:
        raise ExecutionSafetyError(
            "Option order has an invalid price effect"
        )

    if not payload.get("limit_price"):
        raise ExecutionSafetyError(
            "Option order is missing a limit price"
        )

    window = assert_order_submission_window(now=now)

    quote_snapshot = order.get("quote_snapshot") or {}
    quoted_contracts = quote_snapshot.get("contracts") or []

    if not quoted_contracts:
        raise ExecutionSafetyError(
            "Option order is missing its ORATS quote snapshot"
        )

    current_time = (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )

    for quoted_contract in quoted_contracts:
        quote = quoted_contract.get("quote") or {}

        bid = quote.get("bid_per_share")
        ask = quote.get("ask_per_share")

        if bid is None or ask is None or bid <= 0 or ask <= 0:
            raise ExecutionSafetyError(
                "Every option leg requires a usable two-sided quote"
            )

        if ask < bid:
            raise ExecutionSafetyError(
                "An option quote has an invalid bid-ask spread"
            )

        quote_timestamp = _as_utc_datetime(
            quote.get("quote_timestamp")
        )

        quote_age_seconds = (
            current_time - quote_timestamp
        ).total_seconds()

        if quote_age_seconds > MAX_QUOTE_AGE_SECONDS:
            raise ExecutionSafetyError(
                "Option quote is stale and requires a refresh"
            )

        if (
            int(quote.get("open_interest") or 0) <= 0
            and int(quote.get("recent_volume") or 0) <= 0
        ):
            raise ExecutionSafetyError(
                "Option leg has no reported open interest or "
                "recent volume and requires human review"
            )

    return {
        "checked_at": current_time.isoformat(),
        "market_window": window,
        "quote_count": len(quoted_contracts),
        "status": "SAFE_TO_PREPARE",
    }