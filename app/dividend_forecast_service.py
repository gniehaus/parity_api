from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .db import get_conn


def get_expected_dividend_schedule(
    *,
    ticker: str,
    as_of_date: date,
    expiration_date: date,
) -> list[dict[str, Any]]:
    normalized_ticker = ticker.strip().upper()

    if not normalized_ticker:
        raise ValueError("ticker is required")

    if expiration_date < as_of_date:
        raise ValueError(
            "expiration_date cannot precede as_of_date"
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ticker,
                    ex_date,
                    amount_per_share,
                    frequency,
                    source,
                    source_filename,
                    imported_at
                FROM dividend_forecasts
                WHERE ticker = %s
                  AND ex_date > %s
                  AND ex_date <= %s
                  AND amount_per_share > 0
                ORDER BY ex_date ASC
                """,
                (
                    normalized_ticker,
                    as_of_date,
                    expiration_date,
                ),
            )

            return [
                dict(row)
                for row in cur.fetchall()
            ]


def get_expected_dividends_per_share(
    *,
    ticker: str,
    as_of_date: date,
    expiration_date: date,
) -> Decimal:
    schedule = get_expected_dividend_schedule(
        ticker=ticker,
        as_of_date=as_of_date,
        expiration_date=expiration_date,
    )

    return sum(
        (
            Decimal(row["amount_per_share"])
            for row in schedule
        ),
        Decimal("0"),
    )