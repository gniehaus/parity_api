from typing import Any

from .db import list_reconcilable_execution_orders
from .execution_status import refresh_execution_order_status


def reconcile_active_execution_orders(
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Refresh active broker orders independently of browser polling.

    Each order is isolated: one failure does not prevent the remaining
    orders from being reconciled.
    """

    orders = list_reconcilable_execution_orders(
        limit=limit,
    )

    results = []
    errors = []

    for order in orders:
        order_id = str(order["id"])
        parity_user_id = str(order["parity_user_id"])
        previous_status = str(order["status"])

        try:
            result = refresh_execution_order_status(
                parity_user_id=parity_user_id,
                order_id=order_id,
            )

            refreshed_order = result.get("order") or {}

            results.append(
                {
                    "order_id": order_id,
                    "previous_status": previous_status,
                    "current_status": refreshed_order.get(
                        "status"
                    ),
                    "filled_quantity": refreshed_order.get(
                        "filled_quantity"
                    ),
                    "workflow_status": (
                        (result.get("workflow") or {}).get(
                            "status"
                        )
                    ),
                    "lot_status": (
                        (result.get("lot") or {}).get(
                            "status"
                        )
                    ),
                }
            )

        except Exception as exc:
            errors.append(
                {
                    "order_id": order_id,
                    "previous_status": previous_status,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    return {
        "scanned": len(orders),
        "reconciled": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }