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


def get_defined_outcome(ticker: str) -> dict[str, Any]:
    normalized_ticker = ticker.strip().upper()

    products = get_all_defined_outcomes()

    match = next(
        (
            product
            for product in products
            if product["ticker"] == normalized_ticker
        ),
        None,
    )

    if match is None:
        raise ValueError(
            f"{normalized_ticker} was not found"
        )

    return {
        **match,
        "retrieved_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "Innovator public defined outcome table",
    }


import math
from datetime import datetime, timezone
from typing import Any


SUPPORTED_REFERENCE_ASSETS = {
    "SPY",
    "QQQ",
    "EFA",
    "EEM",
}

# A product must actually be a buffer strategy.
REQUIRED_STRATEGY_TERMS = {
    "buffer",
}

# These are explicitly prohibited, even if Innovator adds names
# that also contain the word "buffer."
BLOCKED_STRATEGY_TERMS = {
    "accelerated",
    "dual directional",
    "ultra buffer",
    "floor",
    "premium income",
    "defined protection",
    "managed floor",
}


def safe_string(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text or text.lower() == "nan":
        return None

    return text


def normalize_product_row(row: Any) -> dict[str, Any]:
    days_remaining_value = parse_number(
        row.get("remaining outcome period")
    )

    return {
        "ticker": safe_string(row.get("ticker")).upper(),
        "name": safe_string(row.get("name")),
        "series": safe_string(row.get("series")),
        "reference_asset": safe_string(
            row.get("reference asset")
        ).upper(),
        "fund_price": parse_number(row.get("fund price")),
        "fund_return": parse_number(row.get("fund return")),
        "reference_asset_return": parse_number(
            row.get("ref. asset return")
        ),
        "remaining_cap": parse_number(
            row.get("remaining cap")
        ),
        "remaining_buffer": parse_number(
            row.get("remaining buffer")
        ),
        "downside_before_buffer": parse_number(
            row.get("downside before buffer")
        ),
        "days_remaining": (
            int(days_remaining_value)
            if days_remaining_value is not None
            else None
        ),
        "starting_cap": parse_number(
            row.get("starting cap")
        ),
        "outcome_start": parse_date(
            row.get("outcome period start date")
        ),
        "outcome_end": parse_date(
            row.get("outcome period end date")
        ),
        "starting_reference_asset_price": parse_number(
            row.get("starting ref asset price")
        ),
        "starting_etf_share_price": parse_number(
            row.get("starting etf share price")
        ),
        "reference_asset_price": parse_number(
            row.get("index price")
        ),
        "max_nav": parse_number(row.get("max nav")),
    }


def get_all_defined_outcomes() -> list[dict[str, Any]]:
    table = get_product_table()

    products: list[dict[str, Any]] = []

    for _, row in table.iterrows():
        try:
            product = normalize_product_row(row)
        except (AttributeError, TypeError, ValueError):
            continue

        if not product.get("ticker"):
            continue

        products.append(product)

    return products


def is_approved_buffer_product(
    product: dict[str, Any],
    *,
    minimum_days_remaining: int = 90,
) -> bool:
    name = str(product.get("name") or "").lower()
    reference_asset = str(
        product.get("reference_asset") or ""
    ).upper()

    remaining_buffer = product.get("remaining_buffer")
    remaining_cap = product.get("remaining_cap")
    days_remaining = product.get("days_remaining")

    has_required_strategy = all(
        term in name
        for term in REQUIRED_STRATEGY_TERMS
    )

    has_blocked_strategy = any(
        term in name
        for term in BLOCKED_STRATEGY_TERMS
    )

    return (
        reference_asset in SUPPORTED_REFERENCE_ASSETS
        and has_required_strategy
        and not has_blocked_strategy
        and remaining_buffer is not None
        and remaining_cap is not None
        and remaining_cap > 0
        and days_remaining is not None
        and days_remaining >= minimum_days_remaining
    )


def choose_defined_outcome_match(
    *,
    reference_asset: str,
    target_buffer: float,
    maximum_buffer_difference: float = 5.0,
    minimum_days_remaining: int = 90,
) -> dict[str, Any] | None:
    normalized_asset = reference_asset.strip().upper()

    if normalized_asset not in SUPPORTED_REFERENCE_ASSETS:
        raise ValueError(
            "reference_asset must be SPY, QQQ, EFA, or EEM"
        )

    if not math.isfinite(target_buffer):
        raise ValueError("target_buffer must be a finite number")

    if target_buffer < 0 or target_buffer > 100:
        raise ValueError(
            "target_buffer must be between 0 and 100"
        )

    if maximum_buffer_difference < 0:
        raise ValueError(
            "maximum_buffer_difference cannot be negative"
        )

    products = get_all_defined_outcomes()

    candidates = [
        product
        for product in products
        if (
            product["reference_asset"] == normalized_asset
            and is_approved_buffer_product(
                product,
                minimum_days_remaining=minimum_days_remaining,
            )
        )
    ]

    if not candidates:
        return None

    # Ranking order:
    # 1. Remaining buffer closest to the user's input
    # 2. Higher remaining cap
    # 3. More time remaining
    candidates.sort(
        key=lambda product: (
            abs(
                product["remaining_buffer"]
                - target_buffer
            ),
            -product["remaining_cap"],
            -product["days_remaining"],
        )
    )

    match = candidates[0]

    absolute_difference = abs(
        match["remaining_buffer"] - target_buffer
    )

    # Do not pretend a poor match is suitable.
    if absolute_difference > maximum_buffer_difference:
        return None

    return {
        "requested": {
            "reference_asset": normalized_asset,
            "target_buffer": target_buffer,
        },
        "match": match,
        "buffer_difference": round(
            match["remaining_buffer"] - target_buffer,
            2,
        ),
        "absolute_buffer_difference": round(
            absolute_difference,
            2,
        ),
        "retrieved_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "Innovator public defined outcome table",
    }