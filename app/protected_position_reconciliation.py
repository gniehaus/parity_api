from collections import defaultdict
from typing import Any

from .db import (
    list_active_protected_positions_for_reconciliation,
    update_protected_position_lot_status,
)
from .occ_symbols import (
    format_occ_option_symbol,
    parse_occ_option_symbol,
)
from .snaptrade_service import get_all_account_positions


class ProtectedPositionReconciliationError(ValueError):
    pass


def _as_float(
    value: Any,
    field_name: str,
) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProtectedPositionReconciliationError(
            f"{field_name} is invalid"
        ) from exc


def _canonical_occ_symbol(symbol: str) -> str:
    parsed = parse_occ_option_symbol(symbol)

    return format_occ_option_symbol(
        ticker=parsed["ticker"],
        expiration=parsed["expiration"],
        option_type=parsed["option_type"],
        strike=parsed["strike"],
    )


def _expected_option_quantities(
    positions: list[dict[str, Any]],
) -> dict[str, float]:
    expected: dict[str, float] = defaultdict(float)

    for position in positions:
        for contract in position.get("option_contracts") or []:
            action = str(
                contract.get("action") or ""
            ).upper()

            if action not in {
                "BUY_TO_OPEN",
                "SELL_TO_OPEN",
            }:
                raise ProtectedPositionReconciliationError(
                    "Protected position has an unsupported "
                    "option action"
                )

            instrument = contract.get("instrument") or {}
            symbol = instrument.get("symbol")

            if not symbol:
                raise ProtectedPositionReconciliationError(
                    "Protected position is missing an OCC symbol"
                )

            units = _as_float(
                contract.get("units"),
                "Protected option quantity",
            )

            if units <= 0:
                raise ProtectedPositionReconciliationError(
                    "Protected option quantity must be positive"
                )

            signed_units = (
                units
                if action == "BUY_TO_OPEN"
                else -units
            )

            expected[
                _canonical_occ_symbol(str(symbol))
            ] += signed_units

    return dict(expected)


def _broker_positions_for_symbol(
    *,
    broker_positions: list[dict[str, Any]],
    underlying_symbol: str,
) -> tuple[float, dict[str, float]]:
    normalized_underlying = (
        underlying_symbol.strip().upper()
    )

    share_units = 0.0
    option_quantities: dict[str, float] = defaultdict(float)

    for position in broker_positions:
        instrument = position.get("instrument") or {}

        instrument_kind = str(
            instrument.get("kind") or ""
        ).lower()

        instrument_symbol = str(
            instrument.get("symbol")
            or instrument.get("raw_symbol")
            or ""
        )

        if instrument_kind == "option":
            try:
                parsed = parse_occ_option_symbol(
                    instrument_symbol
                )
            except ValueError:
                continue

            if (
                parsed["ticker"].strip().upper()
                != normalized_underlying
            ):
                continue

            canonical_symbol = (
                format_occ_option_symbol(
                    ticker=parsed["ticker"],
                    expiration=parsed["expiration"],
                    option_type=parsed["option_type"],
                    strike=parsed["strike"],
                )
            )

            option_quantities[canonical_symbol] += (
                _as_float(
                    position.get("units"),
                    "Broker option quantity",
                )
            )

            continue

        normalized_symbol = (
            instrument_symbol.strip().upper()
        )

        if normalized_symbol == normalized_underlying:
            share_units += _as_float(
                position.get("units"),
                "Broker share quantity",
            )

    return share_units, dict(option_quantities)


def reconcile_active_protected_positions(
    *,
    limit: int = 5000,
) -> dict[str, Any]:
    """
    Compare every active Parity protected position with current
    brokerage holdings.

    Missing or mismatched holdings require reconciliation. This never
    closes a position and never places, cancels, or replaces an order.
    """

    positions = (
        list_active_protected_positions_for_reconciliation(
            limit=limit,
        )
    )

    positions_by_account: dict[
        tuple[str, str],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for position in positions:
        positions_by_account[
            (
                str(position["parity_user_id"]),
                str(position["account_id"]),
            )
        ].append(position)

    results = []
    errors = []

    for (
        parity_user_id,
        account_id,
    ), account_positions in positions_by_account.items():
        try:
            broker_response = get_all_account_positions(
                parity_user_id=parity_user_id,
                account_id=account_id,
            )

            broker_positions = (
                broker_response.get("positions") or []
            )

            if not isinstance(broker_positions, list):
                raise ProtectedPositionReconciliationError(
                    "Broker returned an invalid positions list"
                )

        except Exception as exc:
            errors.append(
                {
                    "parity_user_id": parity_user_id,
                    "account_id": account_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            continue

        positions_by_symbol: dict[
            str,
            list[dict[str, Any]],
        ] = defaultdict(list)

        for position in account_positions:
            positions_by_symbol[
                str(
                    position["underlying_symbol"]
                ).strip().upper()
            ].append(position)

        for symbol, symbol_positions in (
            positions_by_symbol.items()
        ):
            try:
                expected_shares = sum(
                    int(position["share_quantity"])
                    for position in symbol_positions
                )

                expected_options = (
                    _expected_option_quantities(
                        symbol_positions
                    )
                )

                (
                    broker_shares,
                    broker_options,
                ) = _broker_positions_for_symbol(
                    broker_positions=broker_positions,
                    underlying_symbol=symbol,
                )

                shares_match = (
                    broker_shares >= expected_shares
                )

                options_match = (
                    broker_options == expected_options
                )

                if shares_match and options_match:
                    results.append(
                        {
                            "symbol": symbol,
                            "status": "MATCHED",
                            "protected_lot_ids": [
                                str(position["id"])
                                for position in symbol_positions
                            ],
                            "expected_shares": expected_shares,
                            "broker_shares": broker_shares,
                            "expected_options": expected_options,
                            "broker_options": broker_options,
                            "data_freshness": (
                                broker_response.get(
                                    "data_freshness"
                                )
                            ),
                        }
                    )
                    continue

                reasons = []

                if not shares_match:
                    reasons.append(
                        "UNDERLYING_QUANTITY_MISMATCH"
                    )

                if not options_match:
                    reasons.append(
                        "OPTION_POSITION_MISMATCH"
                    )

                updated_lot_ids = []

                for position in symbol_positions:
                    updated = (
                        update_protected_position_lot_status(
                            parity_user_id=parity_user_id,
                            protected_lot_id=str(
                                position["id"]
                            ),
                            status=(
                                "RECONCILIATION_REQUIRED"
                            ),
                        )
                    )
                    updated_lot_ids.append(
                        str(updated["id"])
                    )

                results.append(
                    {
                        "symbol": symbol,
                        "status": (
                            "RECONCILIATION_REQUIRED"
                        ),
                        "reasons": reasons,
                        "protected_lot_ids": (
                            updated_lot_ids
                        ),
                        "expected_shares": expected_shares,
                        "broker_shares": broker_shares,
                        "expected_options": expected_options,
                        "broker_options": broker_options,
                        "data_freshness": (
                            broker_response.get(
                                "data_freshness"
                            )
                        ),
                    }
                )

            except Exception as exc:
                errors.append(
                    {
                        "parity_user_id": parity_user_id,
                        "account_id": account_id,
                        "symbol": symbol,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )

    return {
        "scanned": len(positions),
        "matched": sum(
            1
            for result in results
            if result["status"] == "MATCHED"
        ),
        "reconciliation_required": sum(
            len(result["protected_lot_ids"])
            for result in results
            if result["status"]
            == "RECONCILIATION_REQUIRED"
        ),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }