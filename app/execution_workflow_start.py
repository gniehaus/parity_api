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
    validate_option_workflow_step,
)

from .execution_conflicts import (
    ExecutionConflictError,
    require_no_option_execution_conflicts,
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
    expected_dividends_per_share_through_expiration: float | None = None,
) -> dict[str, Any]:
    """
    Record one explicit approval for a new position and submit only
    its initial equity market order.

    The stored protection package is not submitted here. A later,
    server-side status refresh may submit it only after the equity
    order is broker-confirmed FILLED.
    """


    normalized_effect = str(option_price_effect).strip().upper()
    normalized_limit = float(option_limit_price)
    
    if normalized_effect == "EVEN":
        if normalized_limit != 0:
            raise ExecutionWorkflowStartError(
                "EVEN option packages must have a zero limit price"
            )
    else:
        if normalized_limit <= 0:
            raise ExecutionWorkflowStartError(
                "DEBIT and CREDIT option packages must have "
                "a limit price greater than zero"
            )
        
    if option_time_in_force != "Day":
        raise ExecutionWorkflowStartError(
            "Option orders must use Day time in force"
        )

    try:
        workflow, option_step = (
            validate_option_workflow_step(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
                sequence=2,
                contracts=option_contracts,
            )
        )

        option_approval_snapshot = {
            "source": "USER_APPROVED_TERMS",
            "contracts": option_contracts,
            "limit_price": option_limit_price,
            "price_effect": option_price_effect,
            "time_in_force": option_time_in_force,
            "expected_dividends_per_share_through_expiration": (
                expected_dividends_per_share_through_expiration
            ),
        }
        if workflow["underlying_source"] != "new":
            raise ExecutionWorkflowStartError(
                "This endpoint is only for new-position workflows"
            )

        initial_option_conflict_check = (
            require_no_option_execution_conflicts(
                parity_user_id=parity_user_id,
                account_id=workflow["account_id"],
                underlying_symbol=workflow[
                    "underlying_symbol"
                ],
            )
        )

        _get_new_position_steps(workflow)

        lots = get_execution_workflow_lots(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        if len(lots) != 1:
            raise ExecutionWorkflowStartError(
                "New-position execution requires one aggregate "
                "workflow lot"
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

    except ExecutionWorkflowStartError:
        raise

    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise ExecutionWorkflowStartError(str(exc)) from exc
        
    approval_recorded = False

    try:
        # Build and safety-check only the equity order before changing
        # the workflow approval state. No option quote is requested here.
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

        approved_workflow = (
            record_new_position_workflow_approval(
                parity_user_id=parity_user_id,
                workflow_id=workflow_id,
                option_contracts=option_contracts,
                option_limit_price=option_limit_price,
                option_price_effect=option_price_effect,
                option_time_in_force=option_time_in_force,
                option_quote_snapshot=(
                    option_approval_snapshot
                ),
            )
        )
        approval_recorded = True

        equity_prepared_order = (
            mark_execution_order_prepared(
                parity_user_id=parity_user_id,
                order_id=equity_draft["id"],
            )
        )

        final_option_conflict_check = (
            require_no_option_execution_conflicts(
                parity_user_id=parity_user_id,
                account_id=workflow["account_id"],
                underlying_symbol=workflow[
                    "underlying_symbol"
                ],
            )
        )

        equity_submission = submit_prepared_option_order(
            parity_user_id=parity_user_id,
            order_id=equity_prepared_order["id"],
        )

    except ExecutionConflictError as exc:
        if approval_recorded:
            try:
                mark_workflow_action_required(
                    parity_user_id=parity_user_id,
                    workflow_id=workflow_id,
                )
            except Exception:
                pass

        raise ExecutionWorkflowStartError(
            str(exc)
        ) from exc

    except Exception as exc:
        message = str(exc)
    
        if (
            "market is closed" in message.lower()
            or "nyse is closed" in message.lower()
            or "outside trading hours" in message.lower()
            or "outside allowed trading window" in message.lower()
        ):
            raise ExecutionWorkflowStartError(
                message
            ) from exc
    
        if approval_recorded:
            try:
                mark_workflow_action_required(
                    parity_user_id=parity_user_id,
                    workflow_id=workflow_id,
                )
            except Exception:
                pass
    
        raise ExecutionWorkflowStartError(
            "The equity order could not be submitted safely"
        ) from exc
        
    return {
        "workflow": approved_workflow,
        "underlying_order": equity_submission["order"],
        "underlying_broker_response": (
            equity_submission["broker_response"]
        ),
        "underlying_safety": equity_safety,
        "approved_option_step": option_step,
        "prepared_option_order": None,
        "prepared_option_safety": None,
        "approved_option_quote_snapshot": (
            option_approval_snapshot
        ),
        "option_conflict_check": (
            final_option_conflict_check
        ),
    }