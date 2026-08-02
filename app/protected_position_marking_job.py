from datetime import date, datetime
from pprint import pprint
from typing import Any
from zoneinfo import ZoneInfo

from .db import list_active_positions_for_daily_mark
from .protected_position_analytics import (
    calculate_protected_position_mark,
)
from .protected_position_reconciliation import (
    reconcile_active_protected_positions,
)

MARKET_TIMEZONE = ZoneInfo("America/New_York")


def run_daily_protected_position_marks(
    *,
    market_date: date | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """
    Create one official daily closing mark for every eligible active
    protected position.

    One position failure does not prevent the remaining positions from
    being marked.
    """

    effective_market_date = (
        market_date
        or datetime.now(MARKET_TIMEZONE).date()
    )

    reconciliation = (
        reconcile_active_protected_positions(
            limit=limit,
        )
    )

    if effective_market_date.weekday() >= 5:
        return {
            "market_date": effective_market_date.isoformat(),
            "skipped": True,
            "reason": "Market date falls on a weekend",
            "scanned": 0,
            "marked": 0,
            "failed": 0,
            "results": [],
            "errors": [],
            "reconciliation": reconciliation,
        }

    positions = list_active_positions_for_daily_mark(
        market_date=effective_market_date,
        limit=limit,
    )

    results = []
    errors = []

    for position in positions:
        protected_lot_id = str(position["id"])
        parity_user_id = str(position["parity_user_id"])

        try:
            result = calculate_protected_position_mark(
                parity_user_id=parity_user_id,
                protected_lot_id=protected_lot_id,
                mark_type="DAILY_CLOSE",
                market_date=effective_market_date,
            )

            mark = result["mark"]

            results.append(
                {
                    "protected_lot_id": protected_lot_id,
                    "symbol": position["underlying_symbol"],
                    "mark_id": str(mark["id"]),
                    "market_date": str(mark["market_date"]),
                    "marked_at": str(mark["marked_at"]),
                    "strategy_market_value": str(
                        mark["strategy_market_value"]
                    ),
                    "pnl_dollars": str(
                        mark["pnl_dollars"]
                    ),
                    "pnl_percent": str(
                        mark["pnl_percent"]
                    ),
                }
            )

        except Exception as exc:
            errors.append(
                {
                    "protected_lot_id": protected_lot_id,
                    "symbol": position["underlying_symbol"],
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    return {
        "market_date": effective_market_date.isoformat(),
        "skipped": False,
        "scanned": len(positions),
        "marked": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
        "reconciliation": reconciliation,
    }


if __name__ == "__main__":
    pprint(
        run_daily_protected_position_marks()
    )