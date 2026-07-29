from typing import Any

from .db import (
    get_execution_order,
    update_execution_order_broker_status,
    advance_execution_lot_after_fill,
    get_execution_workflow,
    mark_execution_workflow_complete_if_all_lots_complete,
)
from .snaptrade_service import (
    _to_plain,
    get_or_create_snaptrade_user,
    snaptrade,
)


class ExecutionStatusError(ValueError):
    pass

def _full_account_orders(
    *,
    snaptrade_user: dict[str, Any],
    account_id: str,
) -> list[dict[str, Any]]:
    response = snaptrade.account_information.get_user_account_orders(
        user_id=snaptrade_user["snaptrade_user_id"],
        user_secret=snaptrade_user["user_secret"],
        account_id=account_id,
        days=1,
    )

    body = _to_plain(response.body) or []

    if isinstance(body, list):
        return body

    if isinstance(body, dict):
        return body.get("orders") or []

    raise ExecutionStatusError(
        "SnapTrade returned an invalid account-orders response"
    )

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
        "CANCELING",
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

    submitted_broker_response = (
        order.get("broker_response") or {}
    )
    submitted_children = (
        submitted_broker_response.get("orders")
        or (
            submitted_broker_response.get("order") or {}
        ).get("orders")
        or []
    )

    expected_child_order_ids = {
        str(child.get("brokerage_order_id"))
        for child in submitted_children
        if child.get("brokerage_order_id")
    }

      if order["status"] == "CANCELING":
        full_order_history = _full_account_orders(
            snaptrade_user=snaptrade_user,
            account_id=order["account_id"],
        )

        if order["order_scope"] == "OPTIONS_PACKAGE":
            history_has_package_legs = any(
                str(candidate.get("brokerage_order_id"))
                in expected_child_order_ids
                for candidate in full_order_history
            )

            if history_has_package_legs:
                broker_orders = full_order_history
        else:
            history_has_order = any(
                str(candidate.get("brokerage_order_id"))
                == str(broker_order_id)
                for candidate in full_order_history
            )

            if history_has_order:
                broker_orders = full_order_history
    if (
        order["order_scope"] == "OPTIONS_PACKAGE"
        and expected_child_order_ids
    ):
        matched_child_orders = [
            broker_order
            for broker_order in broker_orders
            if str(
                broker_order.get("brokerage_order_id")
            ) in expected_child_order_ids
        ]

        if not matched_child_orders:
            return {
                "found": False,
                "order": order,
                "message": (
                    "SnapTrade has not returned this option package "
                    "in the account-orders feed yet"
                ),
            }

        child_statuses = {
            str(child.get("status") or "").upper()
            for child in matched_child_orders
        }

        child_filled_quantities = [
            _as_float(child.get("filled_quantity"))
            for child in matched_child_orders
        ]

        all_children_executed = (
            len(matched_child_orders)
            == len(expected_child_order_ids)
            and child_statuses == {"EXECUTED"}
        )
        
        all_children_canceled = (
            len(matched_child_orders)
            == len(expected_child_order_ids)
            and child_statuses <= {
                "CANCELED",
                "CANCELLED",
            }
        )
        any_child_filled = any(
            quantity > 0
            for quantity in child_filled_quantities
        )

        terminal_child_statuses = {
            "CANCELED",
            "CANCELLED",
            "PARTIAL_CANCELED",
            "EXPIRED",
            "FAILED",
            "REJECTED",
            "REPLACED",
        }

        if all_children_executed:
            local_status = "FILLED"
            filled_quantity = float(
                order["requested_quantity"]
            )

            buy_total = sum(
                _as_float(child.get("execution_price"))
                for child in matched_child_orders
                if str(child.get("action") or "").upper()
                .startswith("BUY")
            )
            sell_total = sum(
                _as_float(child.get("execution_price"))
                for child in matched_child_orders
                if str(child.get("action") or "").upper()
                .startswith("SELL")
            )

            if order.get("price_effect") == "CREDIT":
                average_fill_price = sell_total - buy_total
            elif order.get("price_effect") == "DEBIT":
                average_fill_price = buy_total - sell_total
            else:
                average_fill_price = 0.0
        elif all_children_canceled:
            local_status = "CANCELED"
            filled_quantity = 0.0
            average_fill_price = None

        elif (
            any_child_filled
            or child_statuses & terminal_child_statuses
        ):
            local_status = "ACTION_REQUIRED"
            filled_quantity = min(child_filled_quantities)
            average_fill_price = None

        else:
            local_status = "WORKING"
            filled_quantity = 0.0
            average_fill_price = None

        matched_broker_order = {
            "brokerage_order_id": order["broker_order_id"],
            "orders": matched_child_orders,
        }

    else:
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
  
    if order["status"] == "CANCELING":
        if local_status == "WORKING":
            local_status = "CANCELING"
        elif local_status == "PARTIALLY_FILLED":
            local_status = "ACTION_REQUIRED"
                
    rejection_reason = None

    if local_status in {"REJECTED", "ACTION_REQUIRED"}:
        rejection_reason = (
            "One or more option-package legs require review"
            if order["order_scope"] == "OPTIONS_PACKAGE"
            else str(matched_broker_order.get("status"))
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

    updated_lot = None
    updated_workflow = None

    if local_status == "FILLED":
        workflow = get_execution_workflow(
            parity_user_id=parity_user_id,
            workflow_id=updated_order["workflow_id"],
        )

        if not workflow:
            raise ExecutionStatusError(
                "Execution workflow was not found"
            )

        execution_plan = workflow.get("execution_plan") or []

        if not execution_plan:
            raise ExecutionStatusError(
                "Execution workflow is missing its execution plan"
            )

        final_sequence = max(
            step["sequence"]
            for step in execution_plan
        )

        updated_lot = advance_execution_lot_after_fill(
            parity_user_id=parity_user_id,
            workflow_id=updated_order["workflow_id"],
            lot_id=updated_order["lot_id"],
            is_final_step=(
                updated_order["sequence"] == final_sequence
            ),
        )

        if updated_lot["status"] == "COMPLETE":
            updated_workflow = (
                mark_execution_workflow_complete_if_all_lots_complete(
                    parity_user_id=parity_user_id,
                    workflow_id=updated_order["workflow_id"],
                )
            )

    return {
        "found": True,
        "order": updated_order,
        "lot": updated_lot,
        "workflow": updated_workflow,
        "broker_order": matched_broker_order,
    }