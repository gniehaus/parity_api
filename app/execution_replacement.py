from typing import Any

from .db import (
    get_execution_order,
    get_execution_order_replacement,
    get_execution_workflow,
    mark_execution_order_prepared,
)
from .execution_cancellation import (
    ExecutionCancellationError,
    request_execution_order_cancellation,
)
from .execution_preparation import prepare_option_order_draft
from .execution_safety import (
    ExecutionSafetyError,
    validate_execution_order_safety,
)

from .execution_closing import (
    prepare_close_options_overlay_draft,
)

class ExecutionReplacementError(ValueError):
    pass


def request_execution_order_replacement(
    *,
    parity_user_id: str,
    order_id: str,
    option_limit_price: float,
    option_price_effect: str,
    option_time_in_force: str = "Day",
) -> dict[str, Any]:
    """
    Prepare a fresh replacement before requesting cancellation of the
    original order.

    The replacement remains PREPARED and must not be submitted until
    the original order is broker-confirmed CANCELED with zero fills.
    """

    original_order = get_execution_order(
        parity_user_id=parity_user_id,
        order_id=order_id,
    )

    if not original_order:
        raise ExecutionReplacementError(
            "Execution order was not found"
        )

    if original_order["order_scope"] not in {
        "OPTIONS",
        "OPTIONS_PACKAGE",
    }:
        raise ExecutionReplacementError(
            "Only option orders can be replaced"
        )

    if original_order["status"] not in {
        "SUBMITTED",
        "WORKING",
    }:
        raise ExecutionReplacementError(
            "Only an unfilled working option order can be replaced"
        )

    if float(original_order.get("filled_quantity") or 0) != 0:
        raise ExecutionReplacementError(
            "A partially filled option order cannot be replaced "
            "automatically"
        )

    if option_time_in_force != "Day":
        raise ExecutionReplacementError(
            "Replacement option orders must use Day time in force"
        )
    normalized_effect = str(option_price_effect).strip().upper()
    normalized_limit = float(option_limit_price)

    if normalized_effect not in {
        "DEBIT",
        "CREDIT",
        "EVEN",
    }:
        raise ExecutionReplacementError(
            "Replacement order has an invalid price effect"
        )

    if normalized_effect == "EVEN":
        if normalized_limit != 0:
            raise ExecutionReplacementError(
                "EVEN replacement orders must have a zero limit price"
            )
    else:
        if normalized_limit <= 0:
            raise ExecutionReplacementError(
                "DEBIT and CREDIT replacement orders must have "
                "a limit price greater than zero"
            )

    existing_replacement = get_execution_order_replacement(
        parity_user_id=parity_user_id,
        original_order_id=order_id,
    )

    if existing_replacement:
        raise ExecutionReplacementError(
            "A replacement already exists for this order"
        )

    workflow = get_execution_workflow(
        parity_user_id=parity_user_id,
        workflow_id=str(original_order["workflow_id"]),
    )

    if not workflow:
        raise ExecutionReplacementError(
            "Execution workflow was not found"
        )

    quoted_contracts = (
        (original_order.get("quote_snapshot") or {}).get(
            "contracts"
        )
        or []
    )
    
    option_contracts = []
    
    for quoted_contract in quoted_contracts:
        quote = quoted_contract.get("quote") or {}
        action = quoted_contract.get("action")
    
        ticker = (
            quote.get("ticker")
            or quote.get("underlying_symbol")
        )
        expiration = (
            quote.get("expiration")
            or quote.get("expiration_date")
        )
        option_type = quote.get("option_type")
        strike = (
            quote.get("strike")
            if quote.get("strike") is not None
            else quote.get("strike_price")
        )
    
        if (
            action
            and ticker
            and expiration
            and option_type
            and strike is not None
        ):
            option_contracts.append(
                {
                    "action": action,
                    "ticker": ticker,
                    "expiration": expiration,
                    "option_type": option_type,
                    "strike": float(strike),
                }
            )
    
    if not option_contracts:
        option_contracts = (
            workflow.get("approved_option_contracts")
            or []
        )
    
    try:
        if original_order["execution_phase"] == "CLOSE_OPTIONS":
            replacement_draft = prepare_close_options_overlay_draft(
                parity_user_id=parity_user_id,
                workflow_id=str(original_order["workflow_id"]),
                lot_id=str(original_order["lot_id"]),
                limit_price=normalized_limit,
                price_effect=ormalized_effect,
                time_in_force=option_time_in_force,
                replaces_order_id=order_id,
            )
        else:
            if not option_contracts:
                raise ExecutionReplacementError(
                    "Original order is missing its option contracts"
                )
    
            replacement_draft = prepare_option_order_draft(
                parity_user_id=parity_user_id,
                workflow_id=str(original_order["workflow_id"]),
                lot_id=str(original_order["lot_id"]),
                sequence=int(original_order["sequence"]),
                contracts=option_contracts,
                limit_price=normalized_limit,
                price_effect=normalized_effect,
                time_in_force=option_time_in_force,
                execution_phase="REQUOTE",
                replaces_order_id=order_id,
                allow_replacement=True,
            )
    
        replacement_safety = validate_execution_order_safety(
            replacement_draft,
            allowed_statuses={"DRAFT"},
        )

        prepared_replacement = mark_execution_order_prepared(
            parity_user_id=parity_user_id,
            order_id=str(replacement_draft["id"]),
        )

        cancellation = request_execution_order_cancellation(
            parity_user_id=parity_user_id,
            order_id=order_id,
        )

    except (
        ExecutionCancellationError,
        ExecutionSafetyError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise ExecutionReplacementError(str(exc)) from exc

    return {
        "original_order": cancellation["order"],
        "replacement_order": prepared_replacement,
        "replacement_safety": replacement_safety,
        "cancellation_broker_response": (
            cancellation["broker_response"]
        ),
    }