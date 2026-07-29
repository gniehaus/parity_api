from typing import Any

from .db import (
    claim_execution_order_cancellation,
    get_execution_order,
    mark_execution_order_cancellation_action_required,
    record_execution_order_cancellation_request,
)
from .snaptrade_service import (
    _to_plain,
    get_or_create_snaptrade_user,
    snaptrade,
)


class ExecutionCancellationError(ValueError):
    pass


def _cancellation_target_order_ids(
    order: dict[str, Any],
) -> list[str]:
    """
    Cancel a multi-leg order through its SnapTrade group ID. Single-leg
    orders use their brokerage order ID directly.
    """

    broker_order_id = order.get("broker_order_id")

    if not broker_order_id:
        raise ExecutionCancellationError(
            "Execution order is missing its brokerage order ID"
        )

    return [str(broker_order_id)]


def request_execution_order_cancellation(
    parity_user_id: str,
    order_id: str,
) -> dict[str, Any]:
    """
    Explicitly request cancellation of one working order.

    This function may call SnapTrade, but never creates, replaces, or
    resubmits an order.
    """

    order = get_execution_order(
        parity_user_id=parity_user_id,
        order_id=order_id,
    )

    if not order:
        raise ExecutionCancellationError(
            "Execution order was not found"
        )

    if order["status"] not in {
        "SUBMITTED",
        "WORKING",
        "PARTIALLY_FILLED",
        "ACTION_REQUIRED",
    }:
        raise ExecutionCancellationError(
            "Only working orders can be canceled"
        )

    target_order_ids = _cancellation_target_order_ids(
        order
    )

    canceling_order = claim_execution_order_cancellation(
        parity_user_id=parity_user_id,
        order_id=order_id,
    )

    responses = []

    try:
        snaptrade_user = get_or_create_snaptrade_user(
            parity_user_id
        )

        for brokerage_order_id in target_order_ids:
            response = snaptrade.trading.cancel_order(
                brokerage_order_id=brokerage_order_id,
                account_id=canceling_order["account_id"],
                user_id=snaptrade_user["snaptrade_user_id"],
                user_secret=snaptrade_user["user_secret"],
            )

            responses.append(
                {
                    "brokerage_order_id": brokerage_order_id,
                    "response": _to_plain(response.body),
                }
            )

    except Exception as exc:
        failure_response = {
            "requested_broker_order_ids": target_order_ids,
            "responses": responses,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }

        try:
            mark_execution_order_cancellation_action_required(
                parity_user_id=parity_user_id,
                order_id=order_id,
                reason=(
                    "Cancellation outcome requires review before "
                    "any further action."
                ),
                broker_response=failure_response,
            )
        except Exception:
            pass

        raise ExecutionCancellationError(
            "Cancellation outcome requires review before retrying"
        ) from exc

    cancellation_response = {
        "requested_broker_order_ids": target_order_ids,
        "responses": responses,
    }

    updated_order = record_execution_order_cancellation_request(
        parity_user_id=parity_user_id,
        order_id=order_id,
        broker_response=cancellation_response,
    )

    return {
        "order": updated_order,
        "broker_response": cancellation_response,
    }