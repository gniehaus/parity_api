from typing import Any

from .db import (
    claim_workflow_option_submission_after_underlying_fill,
    get_execution_workflow_orders,
    mark_workflow_action_required,
    mark_workflow_options_submitted,
)
from .execution_submission import submit_prepared_option_order

from .execution_conflicts import (
    ExecutionConflictError,
    require_no_option_execution_conflicts,
)


class ExecutionWorkflowContinuationError(ValueError):
    pass


def submit_preapproved_option_overlay_after_underlying_fill(
    *,
    parity_user_id: str,
    workflow_id: str,
    lot_id: str,
) -> dict[str, Any] | None:
    """
    Submit the already-prepared, pre-approved protection order once
    and only once after the corresponding equity order is confirmed
    FILLED.

    This function never builds or reprices an option order.
    """

    workflow = (
        claim_workflow_option_submission_after_underlying_fill(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
    )

    if not workflow:
        return None

    try:
        orders = get_execution_workflow_orders(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )

        prepared_option_orders = [
            order
            for order in orders
            if (
                str(order["lot_id"]) == str(lot_id)
                and order["sequence"] == 2
                and order["order_scope"]
                in {"OPTIONS", "OPTIONS_PACKAGE"}
                and order["status"] == "PREPARED"
            )
        ]

        if len(prepared_option_orders) != 1:
            raise ExecutionWorkflowContinuationError(
                "Workflow must contain exactly one prepared "
                "protection order"
            )

        option_prepared_order = prepared_option_orders[0]

        approved_limit = workflow.get(
            "approved_option_limit_price"
        )
        approved_effect = workflow.get(
            "approved_option_price_effect"
        )

        if approved_limit is None:
            raise ExecutionWorkflowContinuationError(
                "Workflow is missing its approved option limit"
            )

        if approved_effect not in {
            "DEBIT",
            "CREDIT",
            "EVEN",
        }:
            raise ExecutionWorkflowContinuationError(
                "Workflow has an invalid approved option price effect"
            )

        if (
            float(option_prepared_order["limit_price"])
            != float(approved_limit)
        ):
            raise ExecutionWorkflowContinuationError(
                "Prepared protection limit does not match approval"
            )

        if (
            option_prepared_order["price_effect"]
            != approved_effect
        ):
            raise ExecutionWorkflowContinuationError(
                "Prepared protection price effect does not match "
                "approval"
            )

        option_conflict_check = (
            require_no_option_execution_conflicts(
                parity_user_id=parity_user_id,
                account_id=workflow["account_id"],
                underlying_symbol=workflow[
                    "underlying_symbol"
                ],
            )
        )

        
        option_submission = submit_prepared_option_order(
            parity_user_id=parity_user_id,
            order_id=option_prepared_order["id"],
            allow_preapproved_quote_age=True,
        )

        updated_workflow = mark_workflow_options_submitted(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )


    except ExecutionConflictError as exc:
        try:
            mark_workflow_action_required(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
            )
        except Exception:
            pass

        raise ExecutionWorkflowContinuationError(
            str(exc)
        ) from exc

    
    except Exception as exc:
        try:
            mark_workflow_action_required(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
            )
        except Exception:
            pass

        raise ExecutionWorkflowContinuationError(
            "The prepared protection package requires review"
        ) from exc

    return {
        "workflow": updated_workflow,
        "option_order": option_submission["order"],
        "option_broker_response": (
            option_submission["broker_response"]
        ),
        "option_conflict_check": (
            option_conflict_check
        ),
    }