from typing import Any

from .db import (
    get_execution_workflow_lots,
    mark_execution_order_prepared,
    mark_workflow_action_required,
    record_new_position_workflow_approval,
)
from .execution_plan import build_execution_plan
from .execution_preparation import (
    prepare_equity_order_draft,
    validate_and_quote_option_workflow_step,
)
from .execution_safety import validate_execution_order_safety
from .execution_submission import submit_prepared_option_order


class ExecutionWorkflowStartError(ValueError):
    pass


def _get_new_position_steps(
    workflow: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    execution_plan = (
        workflow.get("execution_plan")
        or build_execution_plan(
            workflow["strategy_type"],
            workflow["underlying_source"],
        )
    )

    if len(execution_plan) != 2:
        raise ExecutionWorkflowStartError(
            "New-position workflow must contain exactly two steps"
        )

    equity_step, option_step = execution_plan

    if (
        equity_step["sequence"] != 1
        or equity_step["order_role"] != "BUY_UNDERLYING"
        or equity_step["order_scope"] != "EQUITY"
        or equity_step["requires_previous_fill"] is not False
    ):
        raise ExecutionWorkflowStartError(
            "New-position workflow has an invalid equity step"
        )

    if (
        option_step["sequence"] != 2
        or option_step["order_scope"]
        not in {"OPTIONS", "OPTIONS_PACKAGE"}
        or option_step["requires_previous_fill"] is not True
    ):
        raise ExecutionWorkflowStartError(
            "New-position workflow has an invalid protection step"
        )

    return equity_step, option_step


def start_approved_new_position_workflow(
    *,
    parity_user_id: str,
    workflow_id: str,
    lot_id: str,
    option_contracts: list[dict[str, Any]],
    option_limit_price: float,
    option_price_effect: str,
    option_time_in_force: str = "Day",
) -> dict[str, Any]:
    """
    Record one explicit approval for a new position and submit only
    its initial equity market order.

    The stored protection package is not submitted here. A later,
    server-side status refresh may submit it only after the equity
    order is broker-confirmed FILLED.
    """

    if option_time_in_force != "Day":
        raise ExecutionWorkflowStartError(
            "Option orders must use Day time in force"
        )

    workflow, option_step, option_quote_snapshot = (
        validate_and_quote_option_workflow_step(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            sequence=2,
            contracts=option_contracts,
        )
    )

    if workflow["underlying_source"] != "new":
        raise ExecutionWorkflowStartError(
            "This endpoint is only for new-position workflows"
        )

    _get_new_position_steps(workflow)

    lots = get_execution_workflow_lots(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    lot = next(
        (
            candidate
            for candidate in lots
            if str(candidate["id"]) == str(lot_id)
        ),
        None,
    )

    if not lot:
        raise ExecutionWorkflowStartError(
            "Execution lot was not found"
        )

    if lot["status"] != "UNSTARTED":
        raise ExecutionWorkflowStartError(
            "This execution lot is not available to start"
        )

    approval_recorded = False

    try:
        approved_workflow = record_new_position_workflow_approval(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            option_contracts=option_contracts,
            option_limit_price=option_limit_price,
            option_price_effect=option_price_effect,
            option_time_in_force=option_time_in_force,
            option_quote_snapshot=option_quote_snapshot,
        )
        approval_recorded = True

        equity_draft = prepare_equity_order_draft(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            lot_id=lot_id,
            sequence=1,
            time_in_force="Day",
        )

        equity_safety = validate_execution_order_safety(
            equity_draft,
            allowed_statuses={"DRAFT"},
        )

        equity_prepared_order = mark_execution_order_prepared(
            parity_user_id=parity_user_id,
            order_id=equity_draft["id"],
        )

        equity_submission = submit_prepared_option_order(
            parity_user_id=parity_user_id,
            order_id=equity_prepared_order["id"],
        )

    except Exception as exc:
        if approval_recorded:
            try:
                mark_workflow_action_required(
                    parity_user_id=parity_user_id,
                    workflow_id=workflow_id,
                )
            except Exception:
                pass

        raise ExecutionWorkflowStartError(
            "The approved workflow requires review before retrying"
        ) from exc

    return {
        "workflow": approved_workflow,
        "underlying_order": equity_submission["order"],
        "underlying_broker_response": (
            equity_submission["broker_response"]
        ),
        "underlying_safety": equity_safety,
        "approved_option_step": option_step,
        "approved_option_quote_snapshot": option_quote_snapshot,
    }