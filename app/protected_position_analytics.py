from datetime import date, datetime, timezone
from typing import Any

from .db import (
    create_protected_position_mark,
    get_protected_position_lot,
)
from .snaptrade_service import get_all_account_positions
from .thetadata_quotes import get_thetadata_option_quote


class ProtectedPositionAnalyticsError(ValueError):
    pass


def _as_float(
    value: Any,
    field_name: str,
) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProtectedPositionAnalyticsError(
            f"{field_name} is invalid"
        ) from exc


def calculate_protected_position_mark(
    *,
    parity_user_id: str,
    protected_lot_id: str,
    mark_type: str = "MANUAL",
    market_date: date | None = None,
) -> dict[str, Any]:
    """
    Calculate and persist one current midpoint valuation for an active
    protected position.
    """

    protected_lot = get_protected_position_lot(
        parity_user_id=parity_user_id,
        protected_lot_id=protected_lot_id,
    )

    if not protected_lot:
        raise ProtectedPositionAnalyticsError(
            "Protected position was not found"
        )

    if protected_lot["status"] != "ACTIVE":
        raise ProtectedPositionAnalyticsError(
            "Only active protected positions can be marked"
        )

    if protected_lot.get("entry_strategy_value") is None:
        raise ProtectedPositionAnalyticsError(
            "Protected position is missing its entry valuation"
        )

    entry_snapshot = (
        protected_lot.get("entry_outcome_snapshot") or {}
    )

    contracts = entry_snapshot.get("contracts") or []

    if not contracts:
        raise ProtectedPositionAnalyticsError(
            "Protected position is missing its option contracts"
        )

    share_quantity = int(
        protected_lot["share_quantity"]
    )

    if share_quantity <= 0:
        raise ProtectedPositionAnalyticsError(
            "Protected position has an invalid share quantity"
        )

    positions_response = get_all_account_positions(
        parity_user_id=parity_user_id,
        account_id=protected_lot["account_id"],
    )

    share_position = next(
        (
            position
            for position in positions_response["positions"]
            if (
                str(
                    (
                        position.get("instrument") or {}
                    ).get("symbol")
                    or ""
                ).upper()
                == protected_lot["underlying_symbol"].upper()
                and str(
                    (
                        position.get("instrument") or {}
                    ).get("kind")
                    or ""
                ).lower()
                != "option"
            )
        ),
        None,
    )

    if not share_position:
        raise ProtectedPositionAnalyticsError(
            "Broker does not show the protected shares"
        )

    broker_share_units = _as_float(
        share_position.get("units"),
        "Broker share quantity",
    )

    if broker_share_units < share_quantity:
        raise ProtectedPositionAnalyticsError(
            "Broker share quantity is below the protected quantity"
        )

    underlying_price = _as_float(
        share_position.get("price"),
        "Underlying price",
    )

    if underlying_price <= 0:
        raise ProtectedPositionAnalyticsError(
            "Underlying price must be greater than zero"
        )

    underlying_market_value = (
        underlying_price * share_quantity
    )

    contract_quantity = _as_float(
        entry_snapshot.get(
            "option_contract_quantity",
            share_quantity / 100,
        ),
        "Option contract quantity",
    )

    if contract_quantity <= 0:
        raise ProtectedPositionAnalyticsError(
            "Option contract quantity must be greater than zero"
        )

    option_market_value = 0.0
    marked_legs = []
    quote_times = []

    for contract in contracts:
        action = str(
            contract.get("action") or ""
        ).upper()

        if action not in {
            "BUY_TO_OPEN",
            "SELL_TO_OPEN",
        }:
            raise ProtectedPositionAnalyticsError(
                "Protected position has an unsupported option action"
            )

        quote = get_thetadata_option_quote(
            ticker=contract["ticker"],
            expiration=contract["expiration"],
            option_type=contract["option_type"],
            strike=contract["strike"],
        )

        midpoint = _as_float(
            quote.get("mid_per_share"),
            "Option midpoint",
        )

        signed_multiplier = (
            1.0
            if action == "BUY_TO_OPEN"
            else -1.0
        )

        leg_market_value = (
            signed_multiplier
            * midpoint
            * 100
            * contract_quantity
        )

        option_market_value += leg_market_value

        quote_time = datetime.fromisoformat(
            str(quote["quote_timestamp"]).replace(
                "Z",
                "+00:00",
            )
        ).astimezone(timezone.utc)

        quote_times.append(quote_time)

        marked_legs.append(
            {
                "ticker": contract["ticker"],
                "expiration": contract["expiration"],
                "option_type": contract["option_type"],
                "strike": float(contract["strike"]),
                "action": action,
                "contracts": contract_quantity,
                "bid_per_share": quote["bid_per_share"],
                "ask_per_share": quote["ask_per_share"],
                "mid_per_share": midpoint,
                "market_value": leg_market_value,
                "quote_timestamp": quote["quote_timestamp"],
            }
        )

    strategy_market_value = (
        underlying_market_value + option_market_value
    )

    entry_strategy_value = _as_float(
        protected_lot["entry_strategy_value"],
        "Entry strategy value",
    )

    if entry_strategy_value == 0:
        raise ProtectedPositionAnalyticsError(
            "Entry strategy value cannot be zero"
        )

    pnl_dollars = (
        strategy_market_value - entry_strategy_value
    )

    pnl_percent = (
        pnl_dollars / entry_strategy_value * 100
    )

    marked_at = (
        min(quote_times)
        if quote_times
        else datetime.now(timezone.utc)
    )

    quote_snapshot = {
        "valuation_method": "MIDPOINT",
        "underlying": {
            "symbol": protected_lot["underlying_symbol"],
            "price": underlying_price,
            "units": share_quantity,
            "market_value": underlying_market_value,
            "broker_data_freshness": (
                positions_response.get("data_freshness")
            ),
        },
        "option_legs": marked_legs,
    }

    mark = create_protected_position_mark(
        parity_user_id=parity_user_id,
        protected_lot_id=protected_lot_id,
        underlying_price=underlying_price,
        underlying_market_value=underlying_market_value,
        option_market_value=option_market_value,
        strategy_market_value=strategy_market_value,
        pnl_dollars=pnl_dollars,
        pnl_percent=pnl_percent,
        quote_source="THETADATA_OPRA_MIDPOINT",
        quote_snapshot=quote_snapshot,
        marked_at=marked_at,
        mark_type=mark_type,
        market_date=market_date,
    )

    return {
        "position": protected_lot,
        "mark": mark,
        "entry_strategy_value": entry_strategy_value,
        "current_strategy_value": strategy_market_value,
        "pnl_dollars": pnl_dollars,
        "pnl_percent": pnl_percent,
        "underlying": quote_snapshot["underlying"],
        "option_legs": marked_legs,
    }