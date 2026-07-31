import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from parity_collar_engine import fetch_orats_chain

from .db import (
    create_execution_order,
    refresh_execution_order_draft,
    get_execution_workflow,
    get_execution_workflow_lots,
    get_execution_workflow_orders,
)
from .execution_plan import build_execution_plan
from .execution_quotes import get_orats_option_quote
from .mleg_payloads import (
    build_limit_mleg_payload,
    build_option_leg,
)


_STEP_LEG_RULES = {
    "BUY_PROTECTIVE_PUT": Counter({
        ("PUT", "BUY_TO_OPEN"): 1,
    }),
    "SELL_COVERED_CALL": Counter({
        ("CALL", "SELL_TO_OPEN"): 1,
    }),
    "COLLAR_OPTIONS_PACKAGE": Counter({
        ("PUT", "BUY_TO_OPEN"): 1,
        ("CALL", "SELL_TO_OPEN"): 1,
    }),
    "BUFFER_OPTIONS_PACKAGE": Counter({
        ("PUT", "BUY_TO_OPEN"): 1,
        ("PUT", "SELL_TO_OPEN"): 1,
        ("CALL", "SELL_TO_OPEN"): 1,
    }),
    "BUY_PUT_SPREAD_PACKAGE": Counter({
        ("PUT", "BUY_TO_OPEN"): 1,
        ("PUT", "SELL_TO_OPEN"): 1,
    }),
}


def _get_orats_token() -> str:
    token = os.getenv("ORATS_TOKEN")

    if not token:
        raise RuntimeError("Missing ORATS_TOKEN environment variable")

    return token


def _normalized_option_type(value: str) -> str:
    normalized = value.strip().upper()

    if normalized in {"CALL", "C"}:
        return "CALL"

    if normalized in {"PUT", "P"}:
        return "PUT"

    raise ValueError("option_type must be CALL, C, PUT, or P")


def _normalized_action(value: str) -> str:
    normalized = value.strip().upper()

    valid_actions = {
        "BUY_TO_OPEN",
        "BUY_TO_CLOSE",
        "SELL_TO_OPEN",
        "SELL_TO_CLOSE",
    }

    if normalized not in valid_actions:
        raise ValueError("Invalid option action")

    return normalized


def _get_workflow_step(
    workflow: dict[str, Any],
    sequence: int,
) -> dict[str, Any]:
    execution_plan = (
        workflow.get("execution_plan")
        or build_execution_plan(
            workflow["strategy_type"],
            workflow["underlying_source"],
        )
    )

    for step in execution_plan:
        if step["sequence"] == sequence:
            return step

    raise ValueError(
        f"Workflow does not have execution sequence {sequence}"
    )


def _validate_contracts_for_step(
    *,
    step: dict[str, Any],
    contracts: list[dict[str, Any]],
    underlying_symbol: str,
) -> None:
    expected_legs = _STEP_LEG_RULES.get(step["order_role"])

    if expected_legs is None:
        raise ValueError(
            "This workflow step is not an option-order step"
        )

    actual_legs: Counter[tuple[str, str]] = Counter()

    for contract in contracts:
        ticker = str(contract["ticker"]).strip().upper()

        if ticker != underlying_symbol:
            raise ValueError(
                "Every option leg must use the workflow's "
                "underlying symbol"
            )

        actual_legs[
            (
                _normalized_option_type(contract["option_type"]),
                _normalized_action(contract["action"]),
            )
        ] += 1

    if actual_legs != expected_legs:
        raise ValueError(
            "Option legs do not match the required workflow step"
        )

    if step["order_scope"] == "OPTIONS_PACKAGE":
        expirations = {
            str(contract["expiration"]).strip()
            for contract in contracts
        }

        if len(expirations) != 1:
            raise ValueError(
                "Every leg in an options package must use "
                "the same expiration"
            )





def validate_and_quote_option_workflow_step(
    *,
    parity_user_id: str,
    workflow_id: str,
    sequence: int,
    contracts: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """
    Validate the user-selected option legs against a workflow step and
    collect a fresh server-side ORATS snapshot.

    This never creates, prepares, or submits an execution order.
    """

    workflow = get_execution_workflow(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    if not workflow:
        raise ValueError("Execution workflow was not found")

    step = _get_workflow_step(
        workflow=workflow,
        sequence=sequence,
    )

    if step["order_scope"] not in {"OPTIONS", "OPTIONS_PACKAGE"}:
        raise ValueError(
            "This workflow step requires an equity order, "
            "not an option order"
        )

    _validate_contracts_for_step(
        step=step,
        contracts=contracts,
        underlying_symbol=workflow["underlying_symbol"].upper(),
    )

    chain = fetch_orats_chain(
        ticker=workflow["underlying_symbol"],
        token=_get_orats_token(),
    )

    quoted_contracts = []

    for contract in contracts:
        normalized_type = _normalized_option_type(
            contract["option_type"]
        )
        normalized_action = _normalized_action(
            contract["action"]
        )

        quote = get_orats_option_quote(
            chain,
            ticker=workflow["underlying_symbol"],
            expiration=contract["expiration"],
            option_type=normalized_type,
            strike=contract["strike"],
        )

        quoted_contracts.append(
            {
                "action": normalized_action,
                "quote": quote,
            }
        )

    quote_snapshot = {
        "source": "ORATS",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "contracts": quoted_contracts,
    }

    return workflow, step, quote_snapshot




def prepare_option_order_draft(
    *,
    parity_user_id: str,
    workflow_id: str,
    lot_id: str,
    sequence: int,
    contracts: list[dict[str, Any]],
    limit_price: str | float,
    price_effect: str,
    refresh_draft: bool = False,
    time_in_force: str = "Day",
    allow_before_previous_fill: bool = False,
) -> dict[str, Any]:
    """
    Build and persist one ORATS-quoted option-order draft.

    This does not call SnapTrade and cannot submit a trade.
    """

    workflow = get_execution_workflow(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    if not workflow:
        raise ValueError("Execution workflow was not found")

    step = _get_workflow_step(
        workflow=workflow,
        sequence=sequence,
    )

    if step["order_scope"] not in {"OPTIONS", "OPTIONS_PACKAGE"}:
        raise ValueError(
            "This workflow step requires an equity order, "
            "not an option order"
        )

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
        raise ValueError("Execution lot was not found")

    if lot["status"] not in {
        "UNSTARTED",
        "WAITING_FOR_NEXT_STEP",
    }:
        raise ValueError(
            "This lot is not available for a new draft order"
        )


    orders = get_execution_workflow_orders(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    if allow_before_previous_fill:
        if (
            workflow["underlying_source"] != "new"
            or sequence != 2
            or lot["status"] != "UNSTARTED"
        ):
            raise ValueError(
                "Early option preparation is only allowed for the "
                "protection step of an unstarted new-position workflow"
            )
    else:
        prior_steps = [
            plan_step
            for plan_step in workflow["execution_plan"]
            if plan_step["sequence"] < sequence
        ]

        for prior_step in prior_steps:
            prior_order_filled = any(
                str(order["lot_id"]) == str(lot_id)
                and order["sequence"] == prior_step["sequence"]
                and order["status"] == "FILLED"
                and float(order["filled_quantity"] or 0)
                >= float(order["requested_quantity"] or 0)
                for order in orders
            )

            if not prior_order_filled:
                raise ValueError(
                    "The preceding workflow step must fill before "
                    "this option order can be prepared"
                )

    
    _validate_contracts_for_step(
        step=step,
        contracts=contracts,
        underlying_symbol=workflow["underlying_symbol"].upper(),
    )

    contracts_for_position = int(lot["share_quantity"]) // 100

    if contracts_for_position <= 0:
        raise ValueError(
            "Execution lot must contain at least 100 shares"
        )

    chain = fetch_orats_chain(
        ticker=workflow["underlying_symbol"],
        token=_get_orats_token(),
    )

    option_legs = []
    quoted_contracts = []

    for contract in contracts:
        normalized_type = _normalized_option_type(
            contract["option_type"]
        )
        normalized_action = _normalized_action(
            contract["action"]
        )

        quote = get_orats_option_quote(
            chain,
            ticker=workflow["underlying_symbol"],
            expiration=contract["expiration"],
            option_type=normalized_type,
            strike=contract["strike"],
        )

        option_legs.append(
            build_option_leg(
                ticker=workflow["underlying_symbol"],
                expiration=contract["expiration"],
                option_type=normalized_type,
                strike=contract["strike"],
                action=normalized_action,
                contracts=contracts_for_position,
            )
        )

        quoted_contracts.append(
            {
                "action": normalized_action,
                "quote": quote,
            }
        )

    order_payload = build_limit_mleg_payload(
        legs=option_legs,
        limit_price=limit_price,
        price_effect=price_effect,
        time_in_force=time_in_force,
    )

    quote_snapshot = {
        "source": "ORATS",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "contracts": quoted_contracts,
    }

    order = create_execution_order(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
        lot_id=lot_id,
        sequence=step["sequence"],
        order_role=step["order_role"],
        order_scope=step["order_scope"],
        requested_quantity=contracts_for_position,
        order_payload=order_payload,
        execution_phase="INITIAL",
        limit_price=float(order_payload["limit_price"]),
        price_effect=order_payload["price_effect"],
        quote_snapshot=quote_snapshot,
    )

    if refresh_draft:
        return refresh_execution_order_draft(
            parity_user_id=parity_user_id,
            order_id=order["id"],
            order_payload=order_payload,
            limit_price=float(order_payload["limit_price"]),
            price_effect=order_payload["price_effect"],
            quote_snapshot=quote_snapshot,
        )

    return order

def prepare_equity_order_draft(
    *,
    parity_user_id: str,
    workflow_id: str,
    lot_id: str,
    sequence: int,
    time_in_force: str = "Day",
) -> dict[str, Any]:
    """
    Build and persist the next equity child-order draft.

    This does not call SnapTrade and cannot submit a trade.
    """

    workflow = get_execution_workflow(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    if not workflow:
        raise ValueError("Execution workflow was not found")

    step = _get_workflow_step(
        workflow=workflow,
        sequence=sequence,
    )

    if step["order_scope"] != "EQUITY":
        raise ValueError(
            "This workflow step requires an option order, "
            "not an equity order"
        )

    if step["order_role"] != "BUY_UNDERLYING":
        raise ValueError("Unsupported equity workflow step")

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
        raise ValueError("Execution lot was not found")

    if lot["status"] not in {
        "UNSTARTED",
        "WAITING_FOR_NEXT_STEP",
    }:
        raise ValueError(
            "This lot is not available for a new equity draft"
        )

    orders = get_execution_workflow_orders(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    prior_steps = [
        plan_step
        for plan_step in workflow["execution_plan"]
        if plan_step["sequence"] < sequence
    ]

    for prior_step in prior_steps:
        prior_order_filled = any(
            str(order["lot_id"]) == str(lot_id)
            and order["sequence"] == prior_step["sequence"]
            and order["status"] == "FILLED"
            and float(order["filled_quantity"] or 0) >= 1
            for order in orders
        )

        if not prior_order_filled:
            raise ValueError(
                "The preceding workflow step must fill before "
                "this equity order can be prepared"
            )

    share_quantity = int(lot["share_quantity"])

    if share_quantity <= 0 or share_quantity % 100 != 0:
        raise ValueError(
            "Equity workflow lots must contain whole option lots"
        )

    order_payload = {
        "action": "BUY",
        "order_type": "Market",
        "time_in_force": time_in_force,
        "symbol": workflow["underlying_symbol"],
        "units": share_quantity,
        "trading_session": "REGULAR",
    }

    quote_snapshot = {
        "source": "WORKFLOW",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "underlying_symbol": workflow["underlying_symbol"],
        "share_quantity": share_quantity,
    }

    return create_execution_order(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
        lot_id=lot_id,
        sequence=step["sequence"],
        order_role=step["order_role"],
        order_scope=step["order_scope"],
        requested_quantity=share_quantity,
        order_payload=order_payload,
        execution_phase="INITIAL",
        quote_snapshot=quote_snapshot,
    )
    