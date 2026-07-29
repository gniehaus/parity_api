from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be a valid number"
        ) from exc


def get_orats_option_quote(
    chain: pd.DataFrame,
    *,
    ticker: str,
    expiration: str,
    option_type: str,
    strike: str | float | Decimal,
) -> dict[str, Any]:
    """
    Return one fresh, execution-only ORATS quote for an exact contract.

    This function does not alter outcome calculations, write to the database,
    call SnapTrade, or submit an order.
    """

    if chain.empty:
        raise ValueError("ORATS chain is empty")

    required_columns = {
        "ticker",
        "expirDate",
        "strike",
        "quoteDate",
        "updatedAt",
        "callBidPrice",
        "callAskPrice",
        "callBidSize",
        "callAskSize",
        "callOpenInterest",
        "callVolume",
        "putBidPrice",
        "putAskPrice",
        "putBidSize",
        "putAskSize",
        "putOpenInterest",
        "putVolume",
    }

    missing_columns = required_columns.difference(chain.columns)

    if missing_columns:
        raise ValueError(
            "ORATS chain is missing required fields: "
            + ", ".join(sorted(missing_columns))
        )

    normalized_ticker = ticker.strip().upper()
    normalized_expiration = str(expiration).strip()
    normalized_type = option_type.strip().upper()
    target_strike = _decimal(strike, "strike")

    if normalized_type not in {"CALL", "C", "PUT", "P"}:
        raise ValueError("option_type must be CALL, C, PUT, or P")

    matching_rows = chain[
        (chain["ticker"].astype(str).str.upper() == normalized_ticker)
        & (
            chain["expirDate"].astype(str)
            == normalized_expiration
        )
        & (
            chain["strike"].map(
                lambda value: _decimal(value, "chain strike")
            )
            == target_strike
        )
    ].copy()

    if matching_rows.empty:
        raise ValueError(
            "ORATS did not return the requested option contract"
        )

    row = matching_rows.iloc[0]

    is_call = normalized_type in {"CALL", "C"}

    prefix = "call" if is_call else "put"

    bid = float(row[f"{prefix}BidPrice"])
    ask = float(row[f"{prefix}AskPrice"])

    if bid <= 0 or ask <= 0 or ask < bid:
        raise ValueError(
            "The requested option does not have a usable two-sided quote"
        )

    return {
        "ticker": normalized_ticker,
        "expiration": normalized_expiration,
        "option_type": "CALL" if is_call else "PUT",
        "strike": float(target_strike),
        "bid_per_share": bid,
        "ask_per_share": ask,
        "mid_per_share": (bid + ask) / 2,
        "spread_per_share": ask - bid,
        "bid_size": int(row[f"{prefix}BidSize"] or 0),
        "ask_size": int(row[f"{prefix}AskSize"] or 0),
        "open_interest": int(row[f"{prefix}OpenInterest"] or 0),
        "recent_volume": int(row[f"{prefix}Volume"] or 0),
        "quote_timestamp": str(row["quoteDate"]),
        "source_updated_at": str(row["updatedAt"]),
    }