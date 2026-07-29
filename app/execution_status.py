from typing import Any

from .db import (
    get_execution_order,
    update_execution_order_broker_status,
)
from .snaptrade_service import (
    _to_plain,
    get_or_create_snaptrade_user,
    snaptrade,
)


class ExecutionStatusError(ValueError):
    pass


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0

    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ExecutionStatusError(
            "Broker returned an invalid quantity or execution price"
        ) from exc


def _map_broker_status(
    broker_status: str,
    filled_quantity: float,
) -> str:
    normalized_status = str(broker_status or "").upper()

    if normalized_status == "EXECUTED":
        return "FILLED"

    if normalized_status == "PARTIAL":
        return "PARTIALLY_FILLED"

    if normalized_status in {
        "PENDING",
        "ACCEPTED",
        "QUEUED",
        "TRIGGERED",
        "ACTIVATED",
        "CANCEL_PENDING",
        "REPLACE_PENDING",
        "NONE",
    }:
        return "WORKING"

    if normalized_status in {"CANCELED", "CANCELLED"}:
        return (
            "ACTION_REQUIRED"
            if filled_quantity > 0
            else "CANCELED"
        )

    if normalized_status == "PARTIAL_CANCELED":
        return "ACTION_REQUIRED"

    if normalized_status == "EXPIRED":
        return "REQUOTE_REQUIRED"

    if normalized_status in {"FAILED", "REJECTED"}:
        return "REJECTED"

    if normalized_status == "REPLACED":
        return "ACTION_REQUIRED"

    return "ACTION_REQUIRED"


def refresh_execution_order_status(
    parity_user_id: str,
    order_id: str,
) -> dict[str, Any]:
    """
    Read the latest SnapTrade order data and reconcile one local order.

    This function never submits, cancels, replaces, or re-prices an order.
    """

    order = get_execution_order(
        parity_user_id=parity_user_id,
        order_id=order_id,
    )

    if not order:
        raise ExecutionStatusError(
            "Execution order was not found"
        )

    if order["status"] not in {
        "SUBMITTED",
        "WORKING",
        "PARTIALLY_FILLED",
        "ACTION_REQUIRED",
    }:
        raise ExecutionStatusError(
            "Only submitted execution orders can be refreshed"
        )

    broker_order_id = order.get("broker_order_id")

    if not broker_order_id:
        raise ExecutionStatusError(
            "Execution order is missing its brokerage order ID"
        )

    snaptrade_user = get_or_create_snaptrade_user(
        parity_user_id
    )

    response = (
        snaptrade.account_information
        .get_user_account_recent_orders(
            user_id=snaptrade_user["snaptrade_user_id"],
            user_secret=snaptrade_user["user_secret"],
            account_id=order["account_id"],
            only_executed=False,
        )
    )

    recent_orders_response = _to_plain(response.body)

    if not isinstance(recent_orders_response, dict):
        raise ExecutionStatusError(
            "SnapTrade returned an unexpected recent-orders response"
        )

    broker_orders = recent_orders_response.get("orders") or []

    if not isinstance(broker_orders, list):
        raise ExecutionStatusError(
            "SnapTrade returned an invalid recent-orders list"
        )

    matched_broker_order = next(
        (
            broker_order
            for broker_order in broker_orders
            if str(
                broker_order.get("brokerage_order_id")
            ) == str(broker_order_id)
        ),
        None,
    )

    if not matched_broker_order:
        return {
            "found": False,
            "order": order,
            "message": (
                "SnapTrade has not returned this order in the "
                "account-orders feed yet"
            ),
        }

    filled_quantity = _as_float(
        matched_broker_order.get("filled_quantity")
    )

    execution_price_value = (
        matched_broker_order.get("execution_price")
    )

    average_fill_price = (
        _as_float(execution_price_value)
        if execution_price_value is not None
        else None
    )

    local_status = _map_broker_status(
        broker_status=matched_broker_order.get("status"),
        filled_quantity=filled_quantity,
    )

    rejection_reason = None

    if local_status in {"REJECTED", "ACTION_REQUIRED"}:
        rejection_reason = str(
            matched_broker_order.get("status")
        )

    updated_order = update_execution_order_broker_status(
        parity_user_id=parity_user_id,
        order_id=order_id,
        status=local_status,
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        broker_response={
            "source": "SnapTrade",
            "order": matched_broker_order,
        },
        rejection_reason=rejection_reason,
    )

    return {
        "found": True,
        "order": updated_order,
        "broker_order": matched_broker_order,
    }