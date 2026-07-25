import csv
import io
import os
from datetime import datetime, timezone

import psycopg2
import requests


ORATS_SUMMARY_URL = (
    "https://api.orats.io/datav2/live/one-minute/summaries"
)


def optional_float(value):
    if value in (None, "", "null", "None"):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_orats_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed
    except ValueError:
        return None


def fetch_orats_dividend(ticker: str) -> dict:
    token = os.getenv("ORATS_TOKEN")

    if not token:
        raise RuntimeError("ORATS_TOKEN is not configured.")

    response = requests.get(
        ORATS_SUMMARY_URL,
        params={
            "token": token,
            "ticker": ticker.upper(),
        },
        timeout=20,
    )
    response.raise_for_status()

    rows = list(csv.DictReader(io.StringIO(response.text)))

    if not rows:
        raise ValueError(
            f"ORATS returned no summary data for {ticker.upper()}."
        )

    row = rows[0]

    stock_price = optional_float(row.get("stockPrice"))
    annual_dividend = optional_float(row.get("annActDiv")) or 0.0
    annual_implied_dividend = optional_float(row.get("annIdiv"))

    dividend_yield = None
    if stock_price and stock_price > 0:
        dividend_yield = annual_dividend / stock_price

    source_updated_at = parse_orats_datetime(
        row.get("updatedAt") or row.get("quoteDate")
    )

    return {
        "ticker": ticker.upper(),
        "stock_price": stock_price,
        "annual_dividend_per_share": annual_dividend,
        "annual_implied_dividend": annual_implied_dividend,
        "next_dividend_per_share": optional_float(
            row.get("nextDiv")
        ),
        "implied_next_dividend_per_share": optional_float(
            row.get("impliedNextDiv")
        ),
        "dividend_yield": dividend_yield,
        "source_updated_at": source_updated_at,
    }


def save_dividend_snapshot(data: dict) -> dict:
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    connection = psycopg2.connect(database_url)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dividend_snapshots (
                    ticker,
                    stock_price,
                    annual_dividend_per_share,
                    annual_implied_dividend,
                    next_dividend_per_share,
                    implied_next_dividend_per_share,
                    dividend_yield,
                    source_updated_at,
                    fetched_at,
                    source
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, NOW(), 'ORATS'
                )
                ON CONFLICT (ticker)
                DO UPDATE SET
                    stock_price = EXCLUDED.stock_price,
                    annual_dividend_per_share =
                        EXCLUDED.annual_dividend_per_share,
                    annual_implied_dividend =
                        EXCLUDED.annual_implied_dividend,
                    next_dividend_per_share =
                        EXCLUDED.next_dividend_per_share,
                    implied_next_dividend_per_share =
                        EXCLUDED.implied_next_dividend_per_share,
                    dividend_yield = EXCLUDED.dividend_yield,
                    source_updated_at =
                        EXCLUDED.source_updated_at,
                    fetched_at = NOW(),
                    source = 'ORATS'
                RETURNING
                    ticker,
                    stock_price,
                    annual_dividend_per_share,
                    dividend_yield,
                    fetched_at;
                """,
                (
                    data["ticker"],
                    data["stock_price"],
                    data["annual_dividend_per_share"],
                    data["annual_implied_dividend"],
                    data["next_dividend_per_share"],
                    data["implied_next_dividend_per_share"],
                    data["dividend_yield"],
                    data["source_updated_at"],
                ),
            )

            saved = cursor.fetchone()

        connection.commit()

        return {
            "ticker": saved[0],
            "stock_price": saved[1],
            "annual_dividend_per_share": saved[2],
            "dividend_yield": saved[3],
            "fetched_at": saved[4],
        }

    finally:
        connection.close()


def refresh_dividend(ticker: str) -> dict:
    data = fetch_orats_dividend(ticker)
    save_dividend_snapshot(data)
    return data


def get_dividend_data(
    ticker: str,
    maximum_age_hours: int = 24,
) -> dict:
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    connection = psycopg2.connect(database_url)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    ticker,
                    stock_price,
                    annual_dividend_per_share,
                    annual_implied_dividend,
                    next_dividend_per_share,
                    implied_next_dividend_per_share,
                    dividend_yield,
                    source_updated_at,
                    fetched_at
                FROM dividend_snapshots
                WHERE ticker = %s
                  AND fetched_at >=
                      NOW() - (%s * INTERVAL '1 hour');
                """,
                (ticker.upper(), maximum_age_hours),
            )

            row = cursor.fetchone()
    finally:
        connection.close()

    if row:
        return {
            "ticker": row[0],
            "stock_price": row[1],
            "annual_dividend_per_share": row[2],
            "annual_implied_dividend": row[3],
            "next_dividend_per_share": row[4],
            "implied_next_dividend_per_share": row[5],
            "dividend_yield": row[6],
            "source_updated_at": row[7],
            "fetched_at": row[8],
            "source": "Market Implied Dividend Rate",
        }

    return refresh_dividend(ticker)