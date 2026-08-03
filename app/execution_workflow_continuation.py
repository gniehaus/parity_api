from typing import Any

from .db import (
    claim_workflow_option_submission_after_underlying_fill,
    mark_execution_order_prepared,
    mark_workflow_action_required,
    mark_workflow_option_submission_retry_required,
    mark_workflow_options_submitted,
)
from .execution_conflicts import (
    ExecutionConflictError,
    require_no_option_execution_conflicts,
)
from .execution_preparation import prepare_option_order_draft
from .execution_safety import (
    ExecutionSafetyError,
    validate_execution_order_safety,
)
from .execution_submission import (
    ExecutionSubmissionError,
    submit_prepared_option_order,
)
from .thetadata_quotes import ThetaDataQuoteError


class ExecutionWorkflowContinuationError(ValueError):
    pass


def _mark_retryable(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> None:
    """
    Return the workflow to a state where continuation can try again.

    This is used only before an option order reaches the broker.
    """

    try:
        mark_workflow_option_submission_retry_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
    except Exception:
        pass


def _mark_action_required(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> None:
    """
    Mark a genuine conflict or uncertain submission for review.
    """

    try:
        mark_workflow_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
    except Exception:
        pass


def submit_preapproved_option_overlay_after_underlying_fill(
    *,
    parity_user_id: str,
    workflow_id: str,
    lot_id: str,
) -> dict[str, Any] | None:
    """
    Prepare and submit the one protection package already approved
    by the user after the equity order is broker-confirmed FILLED.

    The approved contracts, limit, price effect, and time in force
    remain unchanged. Fresh ThetaData quotes are collected only to
    create the execution snapshot. They do not replace the user's
    approved limit, and a midpoint sign change does not reject the
    workflow.
    """

    try:
        workflow = (
            claim_workflow_option_submission_after_underlying_fill(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
            )
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExecutionWorkflowContinuationError(
            str(exc)
        ) from exc

    if not workflow:
        return None

    option_contracts = (
        workflow.get("approved_option_contracts")
        or []
    )
    approved_limit = workflow.get(
        "approved_option_limit_price"
    )
    approved_effect = workflow.get(
        "approved_option_price_effect"
    )
    approved_time_in_force = (
        workflow.get("approved_option_time_in_force")
        or "Day"
    )

    if not option_contracts:
        _mark_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        raise ExecutionWorkflowContinuationError(
            "Workflow is missing its approved option contracts"
        )

    if approved_limit is None:
        _mark_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        raise ExecutionWorkflowContinuationError(
            "Workflow is missing its approved option limit"
        )

    if approved_effect not in {
        "DEBIT",
        "CREDIT",
        "EVEN",
    }:
        _mark_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        raise ExecutionWorkflowContinuationError(
            "Workflow has an invalid approved option price effect"
        )

    if approved_time_in_force != "Day":
        _mark_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        raise ExecutionWorkflowContinuationError(
            "Workflow has an invalid approved time in force"
        )

    try:
        option_conflict_check = (
            require_no_option_execution_conflicts(
                parity_user_id=parity_user_id,
                account_id=workflow["account_id"],
                underlying_symbol=workflow[
                    "underlying_symbol"
                ],
            )
        )

    except ExecutionConflictError as exc:
        _mark_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        raise ExecutionWorkflowContinuationError(
            str(exc)
        ) from exc

    except Exception as exc:
        _mark_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        raise ExecutionWorkflowContinuationError(
            "Current brokerage positions could not be verified"
        ) from exc

    try:
        option_draft = prepare_option_order_draft(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            lot_id=lot_id,
            sequence=2,
            contracts=option_contracts,
            limit_price=float(approved_limit),
            price_effect=approved_effect,
            refresh_draft=True,
            time_in_force=approved_time_in_force,
            allow_before_previous_fill=False,
        )

        option_safety = validate_execution_order_safety(
            option_draft,
            allowed_statuses={"DRAFT"},
        )

        option_prepared_order = mark_execution_order_prepared(
            parity_user_id=parity_user_id,
            order_id=option_draft["id"],
        )

    except (
        ThetaDataQuoteError,
        ExecutionSafetyError,
        KeyError,
        TypeError,
        ValueError,
        RuntimeError,
    ) as exc:
        _mark_retryable(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        raise ExecutionWorkflowContinuationError(
            "The protection package needs a fresh quote before "
            "submission"
        ) from exc

    try:
        option_submission = submit_prepared_option_order(
            parity_user_id=parity_user_id,
            order_id=option_prepared_order["id"],
        )

        updated_workflow = mark_workflow_options_submitted(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )

    except ExecutionSubmissionError as exc:
        _mark_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        raise ExecutionWorkflowContinuationError(
            "The protection-order submission requires review"
        ) from exc

    except Exception as exc:
        _mark_action_required(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        raise ExecutionWorkflowContinuationError(
            "The protection-order result requires review"
        ) from exc

    return {
        "status": "OPTIONS_SUBMITTED",
        "workflow": updated_workflow,
        "option_order": option_submission["order"],
        "option_broker_response": (
            option_submission["broker_response"]
        ),
        "option_safety": option_safety,
        "option_conflict_check": (
            option_conflict_check
        ),
    }