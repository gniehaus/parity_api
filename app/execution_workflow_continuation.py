from typing import Any

from .db import (
    claim_workflow_option_submission_after_underlying_fill,
    mark_workflow_action_required,
    mark_workflow_options_submitted,
)
from .execution_preparation import prepare_option_order_draft
from .execution_safety import validate_execution_order_safety
from .execution_submission import submit_prepared_option_order
from .db import mark_execution_order_prepared


class ExecutionWorkflowContinuationError(ValueError):
    pass


def submit_preapproved_option_overlay_after_underlying_fill(
    *,
    parity_user_id: str,
    workflow_id: str,
    lot_id: str,
) -> dict[str, Any] | None:
    """
    Submit the stored, pre-approved overlay once and only once after
    the corresponding equity order is broker-confirmed FILLED.

    Returns None when another refresh already handled the continuation
    or when the workflow is not awaiting it.
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
        option_contracts = (
            workflow.get("approved_option_contracts") or []
        )
        option_limit_price = (
            workflow.get("approved_option_limit_price")
        )
        option_price_effect = (
            workflow.get("approved_option_price_effect")
        )
        option_time_in_force = (
            workflow.get("approved_option_time_in_force") or "Day"
        )

        if not option_contracts:
            raise ExecutionWorkflowContinuationError(
                "Workflow is missing its approved option contracts"
            )

        if option_limit_price is None:
            raise ExecutionWorkflowContinuationError(
                "Workflow is missing its approved option limit"
            )

        if option_price_effect not in {
            "DEBIT",
            "CREDIT",
            "EVEN",
        }:
            raise ExecutionWorkflowContinuationError(
                "Workflow has an invalid approved option price effect"
            )

        option_draft = prepare_option_order_draft(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            lot_id=lot_id,
            sequence=2,
            contracts=option_contracts,
            limit_price=float(option_limit_price),
            price_effect=option_price_effect,
            time_in_force=option_time_in_force,
        )

        validate_execution_order_safety(
            option_draft,
            allowed_statuses={"DRAFT"},
        )

        option_prepared_order = mark_execution_order_prepared(
            parity_user_id=parity_user_id,
            order_id=option_draft["id"],
        )

        option_submission = submit_prepared_option_order(
            parity_user_id=parity_user_id,
            order_id=option_prepared_order["id"],
        )

        updated_workflow = mark_workflow_options_submitted(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )

    except Exception as exc:
        try:
            mark_workflow_action_required(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
            )
        except Exception:
            pass

        raise ExecutionWorkflowContinuationError(
            "The approved protection package requires review"
        ) from exc

    return {
        "workflow": updated_workflow,
        "option_order": option_submission["order"],
        "option_broker_response": (
            option_submission["broker_response"]
        ),
    }