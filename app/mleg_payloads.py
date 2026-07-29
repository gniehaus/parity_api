from decimal import Decimal, InvalidOperation
from typing import Any

from .occ_symbols import format_occ_option_symbol


_OPTION_ACTIONS = {
    "BUY_TO_OPEN",
    "BUY_TO_CLOSE",
    "SELL_TO_OPEN",
    "SELL_TO_CLOSE",
}

_PRICE_EFFECTS = {
    "DEBIT",
    "CREDIT",
    "EVEN",
}


def _format_price(value: str | float | Decimal) -> str:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("limit_price must be a valid number") from exc

    if price <= 0:
        raise ValueError("limit_price must be greater than zero")

    return format(price.normalize(), "f")


def build_option_leg(
    *,
    ticker: str,
    expiration: str,
    option_type: str,
    strike: str | float | Decimal,
    action: str,
    contracts: int,
) -> dict[str, Any]:
    """
    Build one SnapTrade multi-leg option leg from an ORATS-selected contract.
    This function does not call SnapTrade.
    """

    normalized_action = action.strip().upper()

    if normalized_action not in _OPTION_ACTIONS:
        raise ValueError(
            "action must be BUY_TO_OPEN, BUY_TO_CLOSE, "
            "SELL_TO_OPEN, or SELL_TO_CLOSE"
        )

    if contracts <= 0:
        raise ValueError("contracts must be greater than zero")

    return {
        "instrument": {
            "symbol": format_occ_option_symbol(
                ticker=ticker,
                expiration=expiration,
                option_type=option_type,
                strike=strike,
            ),
            "instrument_type": "OPTION",
        },
        "action": normalized_action,
        "units": contracts,
    }


def build_limit_mleg_payload(
    *,
    legs: list[dict[str, Any]],
    limit_price: str | float | Decimal,
    price_effect: str,
    time_in_force: str = "Day",
) -> dict[str, Any]:
    """
    Build a limit-order payload for SnapTrade's place_mleg_order endpoint.

    This only creates a dictionary. It does not submit an order.
    """

    if not legs:
        raise ValueError("At least one leg is required")

    normalized_effect = price_effect.strip().upper()

    if normalized_effect not in _PRICE_EFFECTS:
        raise ValueError(
            "price_effect must be DEBIT, CREDIT, or EVEN"
        )

    if time_in_force not in {"Day", "GTC", "IOC", "FOK"}:
        raise ValueError(
            "time_in_force must be Day, GTC, IOC, or FOK"
        )

    return {
        "order_type": "LIMIT",
        "time_in_force": time_in_force,
        "limit_price": _format_price(limit_price),
        "price_effect": normalized_effect,
        "legs": legs,
    }