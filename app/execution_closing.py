import os
from datetime import datetime, timezone
from typing import Any

from parity_collar_engine import fetch_orats_chain

from .db import (
    create_execution_order,
    get_execution_order,
    get_execution_workflow,
    get_execution_workflow_lots,
    get_execution_workflow_orders,
    get_protected_position_exit,
    get_protected_position_lot,
    refresh_execution_order_draft,
    update_execution_workflow_unwind,
)
from .execution_quotes import get_orats_option_quote
from .mleg_payloads import (
    build_limit_mleg_payload,
    build_option_leg,
)
from .snaptrade_service import get_all_account_positions


_OPEN_TO_CLOSE_ACTION = {
    "BUY_TO_OPEN": "SELL_TO_CLOSE",
    "SELL_TO_OPEN": "BUY_TO_CLOSE",
}


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Option quantity is invalid"
        ) from exc


def _orats_token() -> str:
    token = os.getenv("ORATS_TOKEN")

    if not token:
        raise RuntimeError("Missing ORATS_TOKEN environment variable")

    return token


def prepare_close_options_overlay_draft(
    *,
    parity_user_id: str,
    workflow_id: str,
    lot_id: str,
    limit_price: str | float,
    price_effect: str,
    time_in_force: str = "Day",
) -> dict[str, Any]:
    """
    Prepare a fresh-quoted draft that closes the option overlay for one
    completed workflow lot. This never submits an order.

    The user's current SnapTrade option positions must exactly match the
    option legs opened by this lot. Shares are not sold or otherwise changed.
    """

    workflow = get_execution_workflow(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    if not workflow:
        raise ValueError("Execution workflow was not found")

    if workflow["status"] != "COMPLETE":
        raise ValueError(
            "Only completed workflows can prepare an options-close draft"
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

    if lot["status"] != "COMPLETE":
        raise ValueError(
            "Only completed execution lots can close option overlays"
        )

    opening_orders = [
        order
        for order in get_execution_workflow_orders(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
        if (
            str(order["lot_id"]) == str(lot_id)
            and order["execution_phase"] in {
                                "INITIAL",
                                "REQUOTE",
            }"
            and order["status"] == "FILLED"
            and order["order_scope"] in {
                "OPTIONS",
                "OPTIONS_PACKAGE",
            }
        )
    ]

    if not opening_orders:
        raise ValueError(
            "This workflow lot has no filled option overlay to close"
        )

    expected_positions: dict[str, float] = {}
    opening_actions: dict[str, str] = {}

    for order in opening_orders:
        legs = (order.get("order_payload") or {}).get("legs") or []

        for leg in legs:
            instrument = leg.get("instrument") or {}
            symbol = str(instrument.get("symbol") or "").strip()
            action = str(leg.get("action") or "").upper()
            units = _as_float(leg.get("units"))

            if not symbol or action not in _OPEN_TO_CLOSE_ACTION:
                raise ValueError(
                    "Filled workflow order has an invalid opening option leg"
                )

            if units <= 0:
                raise ValueError(
                    "Filled workflow order has an invalid option quantity"
                )

            expected_units = (
                units if action == "BUY_TO_OPEN" else -units
            )

            previous_action = opening_actions.get(symbol)

            if previous_action and previous_action != action:
                raise ValueError(
                    "Workflow contains conflicting option legs"
                )

            opening_actions[symbol] = action
            expected_positions[symbol] = (
                expected_positions.get(symbol, 0.0)
                + expected_units
            )

    live_positions_response = get_all_account_positions(
        parity_user_id=parity_user_id,
        account_id=workflow["account_id"],
    )

    live_option_positions: dict[str, dict[str, Any]] = {}

    for position in live_positions_response["positions"]:
        instrument = position.get("instrument") or {}

        if instrument.get("kind") != "option":
            continue

        symbol = str(instrument.get("symbol") or "").strip()

        if symbol:
            live_option_positions[symbol] = position

    for symbol, expected_units in expected_positions.items():
        position = live_option_positions.get(symbol)

        if not position:
            raise ValueError(
                "Broker does not show the expected option position: "
                f"{symbol}"
            )

        actual_units = _as_float(position.get("units"))

        if actual_units != expected_units:
            raise ValueError(
                "Broker option position does not exactly match the "
                f"completed workflow: {symbol}"
            )

    chain = fetch_orats_chain(
        ticker=workflow["underlying_symbol"],
        token=_orats_token(),
    )

    close_legs = []
    quoted_contracts = []

    for symbol, expected_units in expected_positions.items():
        position = live_option_positions[symbol]
        instrument = position["instrument"]
        underlying = instrument.get("underlying") or {}

        if (
            str(underlying.get("symbol") or "").upper()
            != workflow["underlying_symbol"].upper()
        ):
            raise ValueError(
                "Broker option position uses the wrong underlying symbol"
            )

        opening_action = opening_actions[symbol]
        close_action = _OPEN_TO_CLOSE_ACTION[opening_action]
        contracts = int(abs(expected_units))

        if contracts <= 0 or float(contracts) != abs(expected_units):
            raise ValueError(
                "Broker option position must contain whole contracts"
            )

        option_type = str(instrument.get("option_type") or "").upper()
        expiration = str(instrument.get("expiration_date") or "")
        strike = instrument.get("strike_price")

        quote = get_orats_option_quote(
            chain,
            ticker=workflow["underlying_symbol"],
            expiration=expiration,
            option_type=option_type,
            strike=strike,
        )

        close_legs.append(
            build_option_leg(
                ticker=workflow["underlying_symbol"],
                expiration=expiration,
                option_type=option_type,
                strike=strike,
                action=close_action,
                contracts=contracts,
            )
        )

        quoted_contracts.append(
            {
                "action": close_action,
                "quote": quote,
            }
        )

    order_payload = build_limit_mleg_payload(
        legs=close_legs,
        limit_price=limit_price,
        price_effect=price_effect,
        time_in_force=time_in_force,
    )

    sequence = max(
        order["sequence"]
        for order in opening_orders
    ) + 1

    quote_snapshot = {
        "source": "ORATS",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "close_type": "OPTIONS_OVERLAY",
        "broker_positions_as_of": (
            live_positions_response.get("data_freshness") or {}
        ).get("as_of"),
        "contracts": quoted_contracts,
    }

    order = create_execution_order(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
        lot_id=lot_id,
        sequence=sequence,
        order_role="CLOSE_OPTIONS_OVERLAY",
        order_scope=(
            "OPTIONS"
            if len(close_legs) == 1
            else "OPTIONS_PACKAGE"
        ),
        requested_quantity=int(lot["share_quantity"]) // 100,
        order_payload=order_payload,
        execution_phase="CLOSE_OPTIONS",
        limit_price=float(order_payload["limit_price"]),
        price_effect=order_payload["price_effect"],
        quote_snapshot=quote_snapshot,
    )

    if order["status"] != "DRAFT":
        return order

    return refresh_execution_order_draft(
        parity_user_id=parity_user_id,
        order_id=order["id"],
        order_payload=order_payload,
        limit_price=float(order_payload["limit_price"]),
        price_effect=order_payload["price_effect"],
        quote_snapshot=quote_snapshot,
    )

def prepare_protected_position_equity_sale_draft(
    *,
    parity_user_id: str,
    exit_id: str,
) -> dict[str, Any]:
    """
    Create the 100-share market-sale draft for an approved full exit.

    This is intentionally narrow: it is available only after the
    linked options-close order is broker-confirmed FILLED. It does not
    submit the sale.
    """

    protected_exit = get_protected_position_exit(
        parity_user_id=parity_user_id,
        exit_id=exit_id,
    )

    if not protected_exit:
        raise ValueError("Protected-position exit was not found")

    if protected_exit["exit_mode"] != "SELL_PROTECTED_POSITION":
        raise ValueError(
            "This exit keeps the shares and has no equity sale"
        )

    if protected_exit["status"] != "AWAITING_OPTIONS_FILL":
        raise ValueError(
            "The options overlay must fill before shares can be sold"
        )

    existing_equity_sale_id = (
        protected_exit.get("equity_sale_order_id")
    )

    if existing_equity_sale_id:
        existing_equity_sale = get_execution_order(
            parity_user_id=parity_user_id,
            order_id=str(existing_equity_sale_id),
        )

        if existing_equity_sale:
            return existing_equity_sale

    protected_lot = get_protected_position_lot(
        parity_user_id=parity_user_id,
        protected_lot_id=str(protected_exit["protected_lot_id"]),
    )

    if not protected_lot:
        raise ValueError("Protected position was not found")

    options_close_order_id = (
        protected_exit.get("options_close_order_id")
    )

    if not options_close_order_id:
        raise ValueError(
            "The exit is missing its options-close order"
        )

    options_close_order = get_execution_order(
        parity_user_id=parity_user_id,
        order_id=str(options_close_order_id),
    )

    if not options_close_order or (
        options_close_order["status"] != "FILLED"
    ):
        raise ValueError(
            "The options overlay must be fully filled before shares "
            "can be sold"
        )

    share_quantity = int(
        protected_exit["approved_share_quantity"]
    )

    if share_quantity != 100:
        raise ValueError(
            "Protected-position exits must sell exactly 100 shares"
        )

    positions_response = get_all_account_positions(
        parity_user_id=parity_user_id,
        account_id=protected_lot["account_id"],
    )

    matching_share_position = next(
        (
            position
            for position in positions_response["positions"]
            if (
                str(
                    (position.get("instrument") or {}).get(
                        "symbol"
                    ) or ""
                ).upper()
                == protected_lot["underlying_symbol"].upper()
                and str(
                    (position.get("instrument") or {}).get(
                        "kind"
                    ) or ""
                ).lower()
                != "option"
            )
        ),
        None,
    )

    if not matching_share_position:
        raise ValueError(
            "Broker does not show the shares for this protected lot"
        )

    broker_share_quantity = _as_float(
        matching_share_position.get("units")
    )

    if broker_share_quantity < share_quantity:
        raise ValueError(
            "Broker no longer shows the 100 shares required for "
            "this protected-position exit"
        )

    order_payload = {
        "action": "SELL",
        "order_type": "Market",
        "time_in_force": "Day",
        "symbol": protected_lot["underlying_symbol"].upper(),
        "units": share_quantity,
        "trading_session": "REGULAR",
    }

    quote_snapshot = {
        "source": "WORKFLOW",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "exit_type": "SELL_PROTECTED_POSITION",
        "protected_lot_id": str(protected_lot["id"]),
        "options_close_order_id": str(options_close_order["id"]),
        "share_quantity": share_quantity,
        "broker_positions_as_of": (
            positions_response.get("data_freshness") or {}
        ).get("as_of"),
    }

    return create_execution_order(
        parity_user_id=parity_user_id,
        workflow_id=str(protected_lot["opening_workflow_id"]),
        lot_id=str(protected_lot["opening_workflow_lot_id"]),
        sequence=int(options_close_order["sequence"]) + 1,
        order_role="SELL_UNDERLYING",
        order_scope="EQUITY",
        requested_quantity=share_quantity,
        order_payload=order_payload,
        execution_phase="CLOSE_EQUITY",
        quote_snapshot=quote_snapshot,
    )

def prepare_workflow_unwind_equity_sale_draft(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> dict[str, Any]:
    """
    Prepare a market sale for only the shares acquired by an aborted
    new-position workflow.

    All share-purchase and option-package orders must already be in
    confirmed terminal states. This function never cancels an order
    and never submits the sale.
    """

    workflow = get_execution_workflow(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    if not workflow:
        raise ValueError(
            "Execution workflow was not found"
        )

    if workflow["underlying_source"] != "new":
        raise ValueError(
            "Only new-position workflows can use an unwind sale"
        )

    if not workflow.get("unwind_requested_at"):
        raise ValueError(
            "The workflow does not have an approved unwind request"
        )

    if workflow.get("unwind_status") != "READY_TO_SELL":
        raise ValueError(
            "All working orders must be canceled before shares "
            "can be sold"
        )

    existing_sell_order_id = workflow.get(
        "unwind_sell_order_id"
    )

    if existing_sell_order_id:
        existing_sell_order = get_execution_order(
            parity_user_id=parity_user_id,
            order_id=str(existing_sell_order_id),
        )

        if existing_sell_order:
            return existing_sell_order

    final_share_quantity = workflow.get(
        "unwind_final_share_quantity"
    )

    if final_share_quantity is None:
        raise ValueError(
            "The unwind is missing its final filled share quantity"
        )

    share_quantity = int(final_share_quantity)

    if (
        share_quantity <= 0
        or float(final_share_quantity) != float(share_quantity)
    ):
        raise ValueError(
            "The unwind share quantity must be a positive whole "
            "number"
        )

    if share_quantity > int(workflow["underlying_shares"]):
        raise ValueError(
            "The unwind cannot sell more shares than this workflow "
            "was approved to purchase"
        )

    workflow_orders = get_execution_workflow_orders(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    underlying_order = next(
        (
            order
            for order in workflow_orders
            if order["order_role"] == "BUY_UNDERLYING"
        ),
        None,
    )

    if not underlying_order:
        raise ValueError(
            "The workflow is missing its share-purchase order"
        )

    if float(
        underlying_order.get("filled_quantity") or 0
    ) != float(share_quantity):
        raise ValueError(
            "The confirmed share fill does not match the unwind "
            "sale quantity"
        )

    active_statuses = {
        "DRAFT",
        "PREPARED",
        "SUBMITTING",
        "SUBMITTED",
        "WORKING",
        "PARTIALLY_FILLED",
        "CANCELING",
        "ACTION_REQUIRED",
    }

    def is_confirmed_partial_buy_cancellation(
        order: dict[str, Any],
    ) -> bool:
        broker_order = (
            (order.get("broker_response") or {}).get("order")
            or {}
        )
        broker_status = str(
            broker_order.get("status") or ""
        ).upper()

        return (
            order["order_role"] == "BUY_UNDERLYING"
            and order["status"] == "ACTION_REQUIRED"
            and broker_status in {
                "CANCELED",
                "CANCELLED",
                "PARTIAL_CANCELED",
            }
            and float(
                order.get("filled_quantity") or 0
            ) > 0
        )

    active_orders = [
        order
        for order in workflow_orders
        if (
            str(order["id"])
            != str(
                workflow.get("unwind_sell_order_id") or ""
            )
            and order["status"] in active_statuses
            and not is_confirmed_partial_buy_cancellation(
                order
            )
        )
    ]

    if active_orders:
        raise ValueError(
            "All existing workflow orders must reach a terminal "
            "state before shares can be sold"
        )

    option_orders = [
        order
        for order in workflow_orders
        if order["order_scope"] in {
            "OPTIONS",
            "OPTIONS_PACKAGE",
        }
    ]

    option_fill_detected = any(
        (
            order["status"] == "FILLED"
            or float(order.get("filled_quantity") or 0) > 0
        )
        for order in option_orders
    )

    if option_fill_detected:
        raise ValueError(
            "An option order filled during cancellation. Use the "
            "protected-position exit flow before selling shares"
        )

    existing_unwind_sale = next(
        (
            order
            for order in workflow_orders
            if (
                order["order_role"] == "SELL_UNDERLYING"
                and (
                    order.get("quote_snapshot") or {}
                ).get("exit_type") == "ABORT_NEW_POSITION"
            )
        ),
        None,
    )

    if existing_unwind_sale:
        update_execution_workflow_unwind(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            unwind_status="READY_TO_SELL",
            final_share_quantity=share_quantity,
            sell_order_id=str(existing_unwind_sale["id"]),
            broker_snapshot={
                "recovered_existing_sale_order_id": str(
                    existing_unwind_sale["id"]
                ),
            },
        )
        return existing_unwind_sale

    positions_response = get_all_account_positions(
        parity_user_id=parity_user_id,
        account_id=workflow["account_id"],
    )

    matching_share_position = next(
        (
            position
            for position in positions_response["positions"]
            if (
                str(
                    (position.get("instrument") or {}).get(
                        "symbol"
                    ) or ""
                ).upper()
                == workflow["underlying_symbol"].upper()
                and str(
                    (position.get("instrument") or {}).get(
                        "kind"
                    ) or ""
                ).lower()
                != "option"
            )
        ),
        None,
    )

    if not matching_share_position:
        raise ValueError(
            "The broker does not show the shares acquired by this "
            "workflow"
        )

    broker_share_quantity = _as_float(
        matching_share_position.get("units")
    )

    if broker_share_quantity < share_quantity:
        raise ValueError(
            "The broker no longer shows enough shares to complete "
            "this unwind"
        )

    next_sequence = (
        max(
            int(order["sequence"])
            for order in workflow_orders
        )
        + 1
    )

    order_payload = {
        "action": "SELL",
        "order_type": "Market",
        "time_in_force": "Day",
        "symbol": workflow["underlying_symbol"].upper(),
        "units": share_quantity,
        "trading_session": "REGULAR",
    }

    quote_snapshot = {
        "source": "WORKFLOW",
        "prepared_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "exit_type": "ABORT_NEW_POSITION",
        "workflow_id": str(workflow["id"]),
        "equity_buy_order_id": str(
            underlying_order["id"]
        ),
        "share_quantity": share_quantity,
        "broker_positions_as_of": (
            positions_response.get("data_freshness") or {}
        ).get("as_of"),
    }

    sell_order = create_execution_order(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
        lot_id=str(underlying_order["lot_id"]),
        sequence=next_sequence,
        order_role="SELL_UNDERLYING",
        order_scope="EQUITY",
        requested_quantity=share_quantity,
        order_payload=order_payload,
        execution_phase="CLOSE_EQUITY",
        quote_snapshot=quote_snapshot,
    )

    update_execution_workflow_unwind(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
        unwind_status="READY_TO_SELL",
        final_share_quantity=share_quantity,
        sell_order_id=str(sell_order["id"]),
        broker_snapshot={
            "sale_draft_order_id": str(sell_order["id"]),
        },
    )

    return sell_order