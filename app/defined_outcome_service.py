from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd
import requests


URL = "https://www.innovatoretfs.com/define/etfs/"


def get_page() -> str:
    response = requests.get(
        URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def clean_column_name(value: Any) -> str:
    text = str(value).strip().lower()
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text)


def flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        flattened = []

        for parts in frame.columns:
            values = [
                clean_column_name(part)
                for part in parts
                if str(part).lower() != "nan"
                and not str(part).startswith("Unnamed")
            ]

            flattened.append(" ".join(dict.fromkeys(values)))

        frame.columns = flattened
    else:
        frame.columns = [
            clean_column_name(column)
            for column in frame.columns
        ]

    return frame


def get_product_table() -> pd.DataFrame:
    html = get_page()
    tables = pd.read_html(io.StringIO(html))

    for table in tables:
        table = flatten_columns(table.copy())
        columns = list(table.columns)

        has_ticker = any("ticker" in column for column in columns)
        has_cap = any(
            "remaining" in column and "cap" in column
            for column in columns
        )
        has_buffer = any(
            "remaining" in column and "buffer" in column
            for column in columns
        )

        if has_ticker and has_cap and has_buffer:
            return table

    raise RuntimeError("Defined outcome table not found")


def inspect_table() -> dict:
    table = get_product_table()

    return {
        "rows": len(table),
        "columns": list(table.columns),
        "sample": table.head(3).fillna("").to_dict(orient="records"),
    }

from datetime import datetime
from typing import Any


def parse_number(value: Any) -> float | None:
    if value is None:
        return None

    text = str(value).strip()

    if text in {"", "-", "--", "—", "N/A", "nan"}:
        return None

    cleaned = (
        text.replace("$", "")
        .replace("%", "")
        .replace(",", "")
        .replace("days", "")
        .strip()
    )

    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)

    if not match:
        return None

    return float(match.group())


def parse_date(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    parsed = datetime.strptime(text, "%m/%d/%Y")

    return parsed.date().isoformat()


def get_defined_outcome(ticker: str) -> dict:
    table = get_product_table()

    normalized_ticker = ticker.strip().lower()

    matches = table[
        table["ticker"].astype(str).str.strip().str.lower()
        == normalized_ticker
    ]

    if matches.empty:
        raise ValueError(f"{ticker.upper()} was not found")

    row = matches.iloc[0]

    return {
        "ticker": str(row["ticker"]).upper(),
        "name": str(row["name"]),
        "series": str(row["series"]),
        "reference_asset": str(row["reference asset"]),
        "fund_price": parse_number(row["fund price"]),
        "fund_return": parse_number(row["fund return"]),
        "reference_asset_return": parse_number(
            row["ref. asset return"]
        ),
        "remaining_cap": parse_number(row["remaining cap"]),
        "remaining_buffer": parse_number(
            row["remaining buffer"]
        ),
        "downside_before_buffer": parse_number(
            row["downside before buffer"]
        ),
        "days_remaining": int(
            parse_number(row["remaining outcome period"])
        ),
        "starting_cap": parse_number(row["starting cap"]),
        "outcome_start": parse_date(
            row["outcome period start date"]
        ),
        "outcome_end": parse_date(
            row["outcome period end date"]
        ),
        "starting_reference_asset_price": parse_number(
            row["starting ref asset price"]
        ),
        "starting_etf_share_price": parse_number(
            row["starting etf share price"]
        ),
        "reference_asset_price": parse_number(
            row["index price"]
        ),
        "max_nav": parse_number(row["max nav"]),
        "source": "Innovator public defined outcome table",
    }