from typing import Any

from .db import (
    list_active_option_execution_orders,
    list_managed_protected_position_lots,
)
from .occ_symbols import parse_occ_option_symbol
from .snaptrade_service import (
    get_account_recent_orders,
    get_all_account_positions,
)


class ExecutionConflictError(ValueError):
    pass


_TERMINAL_BROKER_ORDER_STATUSES = {
    "CANCELED",
    "CANCELLED",
    "EXECUTED",
    "EXPIRED",
    "FAILED",
    "FILLED",
    "REJECTED",
}


def _as_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_broker_order_ids(
    value: Any,
) -> set[str]:
    """
    Recursively collect brokerage_order_id values from a stored
    SnapTrade response.
    """

    identifiers: set[str] = set()

    if isinstance(value, dict):
        broker_order_id = value.get(
            "brokerage_order_id"
        )

        if broker_order_id:
            identifiers.add(str(broker_order_id))

        for child_value in value.values():
            identifiers.update(
                _extract_broker_order_ids(child_value)
            )

    elif isinstance(value, list):
        for child_value in value:
            identifiers.update(
                _extract_broker_order_ids(child_value)
            )

    return identifiers


def _managed_contract_quantities(
    managed_lots: list[dict],
) -> dict[str, float]:
    """
    Return expected signed brokerage quantities by OCC symbol.

    Long opening contracts are positive. Short opening contracts
    are negative.
    """

    quantities: dict[str, float] = {}

    for lot in managed_lots:
        for contract in (
            lot.get("option_contracts") or []
        ):
            instrument = (
                contract.get("instrument") or {}
            )
            occ_symbol = str(
                instrument.get("symbol") or ""
            ).strip().upper()

            if not occ_symbol:
                continue

            action = str(
                contract.get("action") or ""
            ).strip().upper()

            units = _as_float(
                contract.get("units")
            )

            if action == "BUY_TO_OPEN":
                signed_units = units
            elif action == "SELL_TO_OPEN":
                signed_units = -units
            else:
                continue

            quantities[occ_symbol] = (
                quantities.get(occ_symbol, 0.0)
                + signed_units
            )

    return quantities


def _normalize_option_position(
    position: dict,
) -> dict | None:
    instrument = position.get("instrument") or {}

    if str(
        instrument.get("kind") or ""
    ).strip().lower() != "option":
        return None

    occ_symbol = str(
        instrument.get("symbol")
        or instrument.get("raw_symbol")
        or ""
    ).strip().upper()

    if not occ_symbol:
        return None

    try:
        parsed = parse_occ_option_symbol(
            occ_symbol
        )
    except ValueError:
        return {
            "source": "BROKER_POSITION",
            "occ_symbol": occ_symbol,
            "ticker": None,
            "expiration": None,
            "option_type": None,
            "strike": None,
            "quantity": _as_float(
                position.get("units")
            ),
            "position_side": "UNKNOWN",
            "parse_error": True,
        }

    quantity = _as_float(
        position.get("units")
    )

    return {
        "source": "BROKER_POSITION",
        "occ_symbol": occ_symbol,
        "ticker": parsed["ticker"],
        "expiration": parsed["expiration"],
        "option_type": parsed["option_type"],
        "strike": parsed["strike"],
        "quantity": quantity,
        "position_side": (
            "LONG"
            if quantity > 0
            else "SHORT"
            if quantity < 0
            else "FLAT"
        ),
        "parse_error": False,
    }


def _normalize_option_order(
    order: dict,
) -> dict | None:
    option_symbol = (
        order.get("option_symbol") or {}
    )

    if not option_symbol:
        return None

    occ_symbol = str(
        option_symbol.get("ticker") or ""
    ).strip().upper()

    if not occ_symbol:
        return None

    try:
        parsed = parse_occ_option_symbol(
            occ_symbol
        )
    except ValueError:
        return {
            "source": "BROKER_ORDER",
            "broker_order_id": order.get(
                "brokerage_order_id"
            ),
            "status": str(
                order.get("status") or ""
            ).strip().upper(),
            "action": order.get("action"),
            "occ_symbol": occ_symbol,
            "ticker": None,
            "expiration": option_symbol.get(
                "expiration_date"
            ),
            "option_type": option_symbol.get(
                "option_type"
            ),
            "strike": option_symbol.get(
                "strike_price"
            ),
            "quantity": None,
            "parse_error": True,
        }

    quantity = (
        order.get("units")
        or order.get("quantity")
        or order.get("total_quantity")
        or order.get("original_quantity")
    )

    return {
        "source": "BROKER_ORDER",
        "broker_order_id": order.get(
            "brokerage_order_id"
        ),
        "status": str(
            order.get("status") or ""
        ).strip().upper(),
        "action": order.get("action"),
        "occ_symbol": occ_symbol,
        "ticker": parsed["ticker"],
        "expiration": parsed["expiration"],
        "option_type": parsed["option_type"],
        "strike": parsed["strike"],
        "quantity": (
            _as_float(quantity)
            if quantity is not None
            else None
        ),
        "parse_error": False,
    }


def inspect_option_execution_conflicts(
    *,
    parity_user_id: str,
    account_id: str,
    underlying_symbol: str,
) -> dict:
    """
    Inspect live same-underlying option exposure before a new Parity
    protection workflow.

    This function is read-only. It never submits, changes, cancels,
    or replaces an order.
    """

    normalized_symbol = (
        underlying_symbol.strip().upper()
    )

    if not normalized_symbol:
        raise ExecutionConflictError(
            "underlying_symbol is required"
        )

    managed_lots = (
        list_managed_protected_position_lots(
            parity_user_id=parity_user_id,
            account_id=account_id,
            underlying_symbol=normalized_symbol,
        )
    )

    parity_orders = (
        list_active_option_execution_orders(
            parity_user_id=parity_user_id,
            account_id=account_id,
            underlying_symbol=normalized_symbol,
        )
    )

    positions_response = get_all_account_positions(
        parity_user_id=parity_user_id,
        account_id=account_id,
    )

    orders_response = get_account_recent_orders(
        parity_user_id=parity_user_id,
        account_id=account_id,
    )

    expected_managed_quantities = (
        _managed_contract_quantities(
            managed_lots
        )
    )

    live_option_positions = []

    for position in positions_response["positions"]:
        normalized_position = (
            _normalize_option_position(position)
        )

        if not normalized_position:
            continue

        if (
            normalized_position["ticker"]
            != normalized_symbol
        ):
            continue

        if normalized_position["quantity"] == 0:
            continue

        live_option_positions.append(
            normalized_position
        )

    managed_option_positions = []
    external_option_positions = []

    for position in live_option_positions:
        occ_symbol = position["occ_symbol"]
        expected_quantity = (
            expected_managed_quantities.get(
                occ_symbol,
                0.0,
            )
        )
        live_quantity = position["quantity"]

        if live_quantity == expected_quantity:
            managed_option_positions.append(
                {
                    **position,
                    "managed_by_parity": True,
                }
            )
            continue

        external_quantity = (
            live_quantity - expected_quantity
        )

        external_option_positions.append(
            {
                **position,
                "managed_by_parity": False,
                "parity_managed_quantity": (
                    expected_quantity
                ),
                "external_quantity": (
                    external_quantity
                ),
            }
        )

    known_parity_broker_order_ids: set[str] = set()

    for parity_order in parity_orders:
        broker_order_id = parity_order.get(
            "broker_order_id"
        )

        if broker_order_id:
            known_parity_broker_order_ids.add(
                str(broker_order_id)
            )

        known_parity_broker_order_ids.update(
            _extract_broker_order_ids(
                parity_order.get(
                    "broker_response"
                )
            )
        )

    active_option_orders = []

    for broker_order in orders_response["orders"]:
        normalized_order = (
            _normalize_option_order(
                broker_order
            )
        )

        if not normalized_order:
            continue

        if (
            normalized_order["ticker"]
            != normalized_symbol
        ):
            continue

        if (
            normalized_order["status"]
            in _TERMINAL_BROKER_ORDER_STATUSES
        ):
            continue

        active_option_orders.append(
            normalized_order
        )

    parity_option_orders = []
    external_option_orders = []

    for broker_order in active_option_orders:
        broker_order_id = broker_order.get(
            "broker_order_id"
        )

        if (
            broker_order_id
            and str(broker_order_id)
            in known_parity_broker_order_ids
        ):
            parity_option_orders.append(
                {
                    **broker_order,
                    "managed_by_parity": True,
                }
            )
        else:
            external_option_orders.append(
                {
                    **broker_order,
                    "managed_by_parity": False,
                }
            )

    has_external_exposure = bool(
        external_option_positions
        or external_option_orders
    )

    has_parity_position = bool(
        managed_lots
    )

    has_parity_order = bool(
        parity_orders
        or parity_option_orders
    )

    if has_external_exposure:
        reason_code = (
            "EXTERNAL_OPTION_EXPOSURE"
        )
        message = (
            "Parity cannot execute because existing options or "
            "working option orders were found outside a recognized "
            "Parity workflow"
        )
    elif has_parity_position:
        reason_code = (
            "PARITY_POSITION_EXISTS"
        )
        message = (
            "This underlying already has a Parity-managed "
            "protected position"
        )
    elif has_parity_order:
        reason_code = (
            "PARITY_OPTION_ORDER_ACTIVE"
        )
        message = (
            "This underlying already has a Parity option order "
            "in progress"
        )
    else:
        reason_code = "NO_OPTION_CONFLICT"
        message = (
            "No same-underlying option conflicts were found"
        )

    return {
        "eligible": not (
            has_external_exposure
            or has_parity_position
            or has_parity_order
        ),
        "reason_code": reason_code,
        "message": message,
        "underlying_symbol": normalized_symbol,
        "managed_protected_lots": managed_lots,
        "managed_option_positions": (
            managed_option_positions
        ),
        "external_option_positions": (
            external_option_positions
        ),
        "active_parity_execution_orders": parity_orders,
        "parity_option_orders": (
            parity_option_orders
        ),
        "external_option_orders": (
            external_option_orders
        ),
        "broker_data_freshness": (
            positions_response.get(
                "data_freshness"
            )
        ),
    }


def require_no_option_execution_conflicts(
    *,
    parity_user_id: str,
    account_id: str,
    underlying_symbol: str,
) -> dict:
    """
    Require a clean cash-or-underlying starting state for a new
    Parity protection workflow.
    """

    result = inspect_option_execution_conflicts(
        parity_user_id=parity_user_id,
        account_id=account_id,
        underlying_symbol=underlying_symbol,
    )

    if not result["eligible"]:
        raise ExecutionConflictError(
            result["message"]
        )

    return result