# expense_ratio_service.py

from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf


def normalize_symbol(symbol: str) -> str:
    """
    Convert brokerage ticker formats into Yahoo-compatible symbols.

    Examples:
        BRK_B -> BRK-B
        BRK.B -> BRK-B
    """
    return (
        symbol
        .strip()
        .upper()
        .replace("_", "-")
        .replace(".", "-")
    )


def normalize_expense_ratio(value: Any) -> float | None:
    """
    Convert an expense ratio into decimal form.

    Examples:
        0.09% -> 0.0009
        0.0009 -> 0.0009
    """
    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip().replace(",", "")

        if not value or value.lower() in {
            "-",
            "--",
            "n/a",
            "none",
            "nan",
        }:
            return None

        if value.endswith("%"):
            try:
                return float(value[:-1]) / 100
            except ValueError:
                return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if pd.isna(number):
        return None

    # yfinance fund tables may use 0.09 to mean 0.09%.
    if number > 0.02:
        return number / 100

    return number


def extract_expense_ratio(
    operations: pd.DataFrame | None,
) -> float | None:
    """
    Extract the fund's expense ratio from the yfinance
    fund_operations DataFrame.
    """
    if operations is None or operations.empty:
        return None

    matching_rows = [
        index
        for index in operations.index
        if "expense ratio" in str(index).lower()
    ]

    if not matching_rows:
        return None

    # Prefer annual report or net expense ratio.
    matching_rows.sort(
        key=lambda index: (
            "annual report" not in str(index).lower(),
            "net" not in str(index).lower(),
            "gross" in str(index).lower(),
        )
    )

    columns = list(operations.columns)

    # Prefer the fund's value over a category average.
    columns.sort(
        key=lambda column: (
            "fund" not in str(column).lower(),
            "category" in str(column).lower(),
        )
    )

    for row in matching_rows:
        for column in columns:
            value = operations.loc[row, column]

            if isinstance(value, pd.Series):
                value = value.iloc[0]

            ratio = normalize_expense_ratio(value)

            if ratio is not None:
                return ratio

    return None


def get_expense_ratio(symbol: str) -> dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)

    try:
        ticker = yf.Ticker(normalized_symbol)
        operations = ticker.funds_data.fund_operations

        expense_ratio = extract_expense_ratio(operations)

        if expense_ratio is None:
            return {
                "symbol": normalized_symbol,
                "expense_ratio": None,
                "expense_ratio_percent": None,
                "status": "not_etf_or_unavailable",
            }

        return {
            "symbol": normalized_symbol,
            "expense_ratio": expense_ratio,
            "expense_ratio_percent": expense_ratio * 100,
            "status": "found",
        }

    except Exception as exc:
        return {
            "symbol": normalized_symbol,
            "expense_ratio": None,
            "expense_ratio_percent": None,
            "status": "not_etf_or_unavailable",
            "error": str(exc),
        }