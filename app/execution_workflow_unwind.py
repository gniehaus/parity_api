from typing import Any

from .db import (
    cancel_unsubmitted_orders_for_workflow_unwind,
    claim_execution_workflow_unwind,
    get_execution_order,
    get_execution_workflow,
    get_execution_workflow_orders,
    mark_execution_order_prepared,
    update_execution_workflow_unwind,
)
from .execution_cancellation import (
    ExecutionCancellationError,
    request_execution_order_cancellation,
)
from .execution_closing import (
    prepare_workflow_unwind_equity_sale_draft,
)
from .execution_safety import validate_execution_order_safety
from .execution_status import (
    ExecutionStatusError,
    refresh_execution_order_status,
)
from .execution_submission import (
    ExecutionSubmissionError,
    submit_prepared_option_order,
)


class ExecutionWorkflowUnwindError(ValueError):
    pass


_ACTIVE_BROKER_STATUSES = {
    "SUBMITTED",
    "WORKING",
    "PARTIALLY_FILLED",
    "CANCELING",
}

_CANCELABLE_STATUSES = {
    "SUBMITTED",
    "WORKING",
    "PARTIALLY_FILLED",
}

_TERMINAL_FAILURE_STATUSES = {
    "CANCELED",
    "EXPIRED",
    "REJECTED",
    "FAILED",
    "REQUOTE_REQUIRED",
    "ACTION_REQUIRED",
}


def _broker_status(
    order: dict[str, Any],
) -> str:
    broker_order = (
        (order.get("broker_response") or {}).get("order")
        or {}
    )

    return str(
        broker_order.get("status") or ""
    ).upper()


def _confirmed_partial_buy_cancellation(
    order: dict[str, Any],
) -> bool:
    return (
        order["order_role"] == "BUY_UNDERLYING"
        and order["status"] == "ACTION_REQUIRED"
        and _broker_status(order) in {
            "CANCELED",
            "CANCELLED",
            "PARTIAL_CANCELED",
        }
        and float(
            order.get("filled_quantity") or 0
        ) > 0
    )


def _option_fill_detected(
    orders: list[dict[str, Any]],
) -> bool:
    return any(
        (
            order["order_scope"] in {
                "OPTIONS",
                "OPTIONS_PACKAGE",
            }
            and (
                order["status"] == "FILLED"
                or float(
                    order.get("filled_quantity") or 0
                ) > 0
            )
        )
        for order in orders
    )


def _unwind_result(
    *,
    parity_user_id: str,
    workflow_id: str,
    message: str,
) -> dict[str, Any]:
    return {
        "workflow": get_execution_workflow(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        ),
        "orders": get_execution_workflow_orders(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        ),
        "message": message,
    }


def _mark_unwind_action_required(
    *,
    parity_user_id: str,
    workflow_id: str,
    message: str,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    update_execution_workflow_unwind(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
        unwind_status="ACTION_REQUIRED",
        error=message,
        broker_snapshot=snapshot or {},
    )

    return _unwind_result(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
        message=message,
    )


def request_workflow_unwind_and_sell(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> dict[str, Any]:
    """
    Record the user's unwind approval before touching any brokerage
    order, then advance the unwind once.
    """

    try:
        claim_execution_workflow_unwind(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )

        cancel_unsubmitted_orders_for_workflow_unwind(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )

        return advance_workflow_unwind_and_sell(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )

    except ValueError as exc:
        raise ExecutionWorkflowUnwindError(
            str(exc)
        ) from exc


def advance_workflow_unwind_and_sell(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> dict[str, Any]:
    """
    Advance one safe step of an approved workflow unwind.

    Repeated calls are idempotent. Shares are never sold until all
    earlier brokerage orders have confirmed terminal outcomes.
    """

    workflow = get_execution_workflow(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    if not workflow:
        raise ExecutionWorkflowUnwindError(
            "Execution workflow was not found"
        )

    if not workflow.get("unwind_requested_at"):
        raise ExecutionWorkflowUnwindError(
            "The workflow does not have an approved unwind request"
        )

    if workflow.get("unwind_status") == "COMPLETE":
        return _unwind_result(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message="The workflow unwind is complete.",
        )

    sell_order_id = workflow.get(
        "unwind_sell_order_id"
    )

    if sell_order_id:
        sell_order = get_execution_order(
            parity_user_id=parity_user_id,
            order_id=str(sell_order_id),
        )

        if not sell_order:
            return _mark_unwind_action_required(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
                message=(
                    "The unwind sale order could not be found."
                ),
            )

        if sell_order["status"] == "SUBMITTING":
            return _mark_unwind_action_required(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
                message=(
                    "The share-sale submission has an uncertain "
                    "brokerage outcome and must be reconciled."
                ),
                snapshot={
                    "submitting_sale_order_id": str(
                        sell_order["id"]
                    ),
                },
            )

        if sell_order["status"] in {
            "DRAFT",
            "PREPARED",
        }:
            try:
                if sell_order["status"] == "DRAFT":
                    validate_execution_order_safety(
                        sell_order,
                        allowed_statuses={"DRAFT"},
                    )

                    sell_order = mark_execution_order_prepared(
                        parity_user_id=parity_user_id,
                        order_id=str(sell_order["id"]),
                    )

                sale_submission = submit_prepared_option_order(
                    parity_user_id=parity_user_id,
                    order_id=str(sell_order["id"]),
                )

                sell_order = sale_submission["order"]

                update_execution_workflow_unwind(
                    parity_user_id=parity_user_id,
                    workflow_id=workflow_id,
                    unwind_status="SELL_SUBMITTED",
                    sell_order_id=str(sell_order["id"]),
                    broker_snapshot={
                        "recovered_sale_submission_id": str(
                            sell_order["id"]
                        ),
                    },
                )

            except (
                ValueError,
                ExecutionSubmissionError,
            ) as exc:
                return _mark_unwind_action_required(
                    parity_user_id=parity_user_id,
                    workflow_id=workflow_id,
                    message=str(exc),
                )

        if sell_order["status"] in _ACTIVE_BROKER_STATUSES:
            try:
                refresh_execution_order_status(
                    parity_user_id=parity_user_id,
                    order_id=str(sell_order["id"]),
                )
            except (
                ExecutionStatusError,
                ValueError,
            ) as exc:
                return _mark_unwind_action_required(
                    parity_user_id=parity_user_id,
                    workflow_id=workflow_id,
                    message=str(exc),
                )

            sell_order = get_execution_order(
                parity_user_id=parity_user_id,
                order_id=str(sell_order["id"]),
            )

        if sell_order["status"] == "FILLED":
            expected_quantity = int(
                workflow.get(
                    "unwind_final_share_quantity"
                )
                or 0
            )
            actual_quantity = int(
                float(
                    sell_order.get("filled_quantity")
                    or 0
                )
            )

            if actual_quantity != expected_quantity:
                return _mark_unwind_action_required(
                    parity_user_id=parity_user_id,
                    workflow_id=workflow_id,
                    message=(
                        "The unwind sale fill quantity does not "
                        "match the approved share quantity."
                    ),
                )

            update_execution_workflow_unwind(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
                unwind_status="COMPLETE",
                final_share_quantity=actual_quantity,
                sell_order_id=str(sell_order["id"]),
                broker_snapshot={
                    "completed_sale_order_id": str(
                        sell_order["id"]
                    ),
                    "completed_share_quantity": (
                        actual_quantity
                    ),
                },
            )

            return _unwind_result(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
                message=(
                    f"Sold {actual_quantity} shares. "
                    "The workflow unwind is complete."
                ),
            )

        if sell_order["status"] in _TERMINAL_FAILURE_STATUSES:
            return _mark_unwind_action_required(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
                message=(
                    "The share-sale order requires review before "
                    "the unwind can continue."
                ),
                snapshot={
                    "sell_order_id": str(sell_order["id"]),
                    "sell_order_status": (
                        sell_order["status"]
                    ),
                },
            )

        update_execution_workflow_unwind(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            unwind_status="SELL_SUBMITTED",
            sell_order_id=str(sell_order["id"]),
        )

        return _unwind_result(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message="The acquired shares are being sold.",
        )

    try:
        cancel_unsubmitted_orders_for_workflow_unwind(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
    except ValueError as exc:
        return _mark_unwind_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=str(exc),
        )

    orders = get_execution_workflow_orders(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    canceling_orders = [
        order
        for order in orders
        if order["status"] == "CANCELING"
    ]

    for order in canceling_orders:
        try:
            refresh_execution_order_status(
                parity_user_id=parity_user_id,
                order_id=str(order["id"]),
            )
        except (
            ExecutionStatusError,
            ValueError,
        ) as exc:
            return _mark_unwind_action_required(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
                message=str(exc),
                snapshot={
                    "status_refresh_order_id": str(
                        order["id"]
                    ),
                },
            )

    orders = get_execution_workflow_orders(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    submitting_orders = [
        order
        for order in orders
        if order["status"] == "SUBMITTING"
    ]

    if submitting_orders:
        return _mark_unwind_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=(
                "An order submission has an uncertain brokerage "
                "outcome and must be reconciled before shares can "
                "be sold."
            ),
            snapshot={
                "submitting_order_ids": [
                    str(order["id"])
                    for order in submitting_orders
                ],
            },
        )

    if _option_fill_detected(orders):
        return _mark_unwind_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=(
                "The protection package filled during cancellation. "
                "Use the protected-position exit flow before selling "
                "the shares."
            ),
        )

    uncertain_orders = [
        order
        for order in orders
        if (
            order["status"] == "ACTION_REQUIRED"
            and not _confirmed_partial_buy_cancellation(
                order
            )
        )
    ]

    if uncertain_orders:
        return _mark_unwind_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=(
                "An order has an uncertain brokerage outcome and "
                "must be reconciled before shares can be sold."
            ),
            snapshot={
                "uncertain_order_ids": [
                    str(order["id"])
                    for order in uncertain_orders
                ],
            },
        )

    cancellation_requested = False

    for order in orders:
        if order["status"] not in _CANCELABLE_STATUSES:
            continue

        if not order.get("broker_order_id"):
            return _mark_unwind_action_required(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
                message=(
                    "A working order is missing its brokerage "
                    "order ID and requires review."
                ),
                snapshot={
                    "missing_broker_order_id": str(
                        order["id"]
                    ),
                },
            )

        try:
            request_execution_order_cancellation(
                parity_user_id=parity_user_id,
                order_id=str(order["id"]),
            )
            cancellation_requested = True

        except (
            ExecutionCancellationError,
            ValueError,
        ) as exc:
            return _mark_unwind_action_required(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
                message=str(exc),
                snapshot={
                    "cancellation_order_id": str(
                        order["id"]
                    ),
                },
            )

    if cancellation_requested:
        update_execution_workflow_unwind(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            unwind_status="CANCELING_ORDERS",
            broker_snapshot={
                "cancellation_requested": True,
            },
        )

        return _unwind_result(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=(
                "Canceling active orders before selling acquired "
                "shares."
            ),
        )

    orders = get_execution_workflow_orders(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    remaining_active_orders = [
        order
        for order in orders
        if (
            order["status"] in _ACTIVE_BROKER_STATUSES
            or (
                order["status"] == "ACTION_REQUIRED"
                and not _confirmed_partial_buy_cancellation(
                    order
                )
            )
        )
    ]

    if remaining_active_orders:
        update_execution_workflow_unwind(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            unwind_status="CANCELING_ORDERS",
        )

        return _unwind_result(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=(
                "Waiting for brokerage cancellation confirmation."
            ),
        )

    underlying_order = next(
        (
            order
            for order in orders
            if order["order_role"] == "BUY_UNDERLYING"
        ),
        None,
    )

    if not underlying_order:
        return _mark_unwind_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=(
                "The workflow is missing its share-purchase order."
            ),
        )

    filled_quantity_value = float(
        underlying_order.get("filled_quantity") or 0
    )

    if filled_quantity_value < 0:
        return _mark_unwind_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=(
                "The broker returned an invalid share quantity."
            ),
        )

    final_share_quantity = int(
        filled_quantity_value
    )

    if float(final_share_quantity) != filled_quantity_value:
        return _mark_unwind_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=(
                "The broker returned a fractional share quantity "
                "for this workflow."
            ),
        )

    if final_share_quantity > int(
        workflow["underlying_shares"]
    ):
        return _mark_unwind_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=(
                "The confirmed share fill exceeds the workflow's "
                "approved quantity."
            ),
        )

    if final_share_quantity == 0:
        update_execution_workflow_unwind(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            unwind_status="COMPLETE",
            final_share_quantity=0,
            broker_snapshot={
                "completed_without_share_sale": True,
            },
        )

        return _unwind_result(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=(
                "No shares filled. The workflow was canceled."
            ),
        )

    update_execution_workflow_unwind(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
        unwind_status="READY_TO_SELL",
        final_share_quantity=final_share_quantity,
    )

    try:
        sell_draft = (
            prepare_workflow_unwind_equity_sale_draft(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
            )
        )

        validate_execution_order_safety(
            sell_draft,
            allowed_statuses={"DRAFT"},
        )

        prepared_sell_order = mark_execution_order_prepared(
            parity_user_id=parity_user_id,
            order_id=str(sell_draft["id"]),
        )

        sale_submission = submit_prepared_option_order(
            parity_user_id=parity_user_id,
            order_id=str(prepared_sell_order["id"]),
        )

        submitted_sell_order = sale_submission["order"]

        update_execution_workflow_unwind(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            unwind_status="SELL_SUBMITTED",
            final_share_quantity=final_share_quantity,
            sell_order_id=str(submitted_sell_order["id"]),
            broker_snapshot={
                "submitted_sale_order_id": str(
                    submitted_sell_order["id"]
                ),
            },
        )

    except (
        ValueError,
        ExecutionSubmissionError,
    ) as exc:
        return _mark_unwind_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            message=str(exc),
        )

    return _unwind_result(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
        message=(
            f"Selling {final_share_quantity} acquired shares."
        ),
    )