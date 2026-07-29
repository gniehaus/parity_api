from typing import Any

from .db import (
    claim_execution_order_submission,
    get_execution_order,
    mark_execution_order_action_required,
    mark_execution_order_submitted,
)
from .execution_safety import (
    ExecutionSafetyError,
    validate_execution_order_safety,
)
from .snaptrade_service import (
    _to_plain,
    get_or_create_snaptrade_user,
    snaptrade,
)


class ExecutionSubmissionError(ValueError):
    pass


def _extract_broker_order_id(
    broker_response: dict[str, Any],
) -> str | None:
    """
    SnapTrade normally returns brokerage_order_id at the top level.
    Retain a fallback for brokers that return it only per child order.
    """

    top_level_order_id = broker_response.get(
        "brokerage_order_id"
    )

    if top_level_order_id:
        return str(top_level_order_id)

    child_orders = broker_response.get("orders") or []

    if len(child_orders) == 1:
        child_order_id = child_orders[0].get(
            "brokerage_order_id"
        )

        if child_order_id:
            return str(child_order_id)

    return None


def submit_prepared_option_order(
    parity_user_id: str,
    order_id: str,
) -> dict[str, Any]:
    """
    Submit one explicitly approved, PREPARED option order to SnapTrade.

    This function must only be called by an authenticated endpoint that
    has rechecked can_execute_new_orders immediately before this call.
    """

    order = get_execution_order(
        parity_user_id=parity_user_id,
        order_id=order_id,
    )

    if not order:
        raise ExecutionSubmissionError(
            "Execution order was not found"
        )

    if order["status"] != "PREPARED":
        raise ExecutionSubmissionError(
            "Only PREPARED orders can be submitted"
        )

    if order["order_scope"] not in {
        "OPTIONS",
        "OPTIONS_PACKAGE",
    }:
        raise ExecutionSubmissionError(
            "This submission service supports option orders only"
        )

    try:
        validate_execution_order_safety(
            order,
            allowed_statuses={"PREPARED"},
        )
    except ExecutionSafetyError as exc:
        raise ExecutionSubmissionError(str(exc)) from exc

    submitting_order = claim_execution_order_submission(
        parity_user_id=parity_user_id,
        order_id=order_id,
    )

    try:
        safety = validate_execution_order_safety(
            submitting_order,
            allowed_statuses={"SUBMITTING"},
        )

        snaptrade_user = get_or_create_snaptrade_user(
            parity_user_id
        )

        response = snaptrade.trading.place_mleg_order(
            body=submitting_order["order_payload"],
            account_id=submitting_order["account_id"],
            user_id=snaptrade_user["snaptrade_user_id"],
            user_secret=snaptrade_user["user_secret"],
        )

        broker_response = _to_plain(response.body)

        if not isinstance(broker_response, dict):
            raise ExecutionSubmissionError(
                "SnapTrade returned an unexpected order response"
            )

        broker_order_id = _extract_broker_order_id(
            broker_response
        )

        submitted_order = mark_execution_order_submitted(
            parity_user_id=parity_user_id,
            order_id=order_id,
            broker_response=broker_response,
            broker_order_id=broker_order_id,
        )

    except Exception as exc:
        failure_response = {
            "error_type": type(exc).__name__,
            "message": str(exc),
        }

        try:
            mark_execution_order_action_required(
                parity_user_id=parity_user_id,
                order_id=order_id,
                reason=(
                    "Broker submission did not return a confirmed "
                    "acknowledgement. Review before retrying."
                ),
                broker_response=failure_response,
            )
        except Exception:
            pass

        raise ExecutionSubmissionError(
            "Broker submission outcome requires review before retrying"
        ) from exc

    return {
        "order": submitted_order,
        "broker_response": broker_response,
        "safety": safety,
    }