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


import math
from datetime import datetime, timezone
from typing import Any


def get_protection_gap(product: dict[str, Any]) -> float:
    """
    Convert downside_before_buffer into a positive percentage-point gap.

    Example:
        -6.23 becomes 6.23

    A smaller value is better because the buffer begins sooner.
    """
    value = product.get("downside_before_buffer")

    if value is None:
        return math.inf

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return math.inf

    if not math.isfinite(numeric_value):
        return math.inf

    return abs(numeric_value)


def choose_defined_outcome_match(
    *,
    reference_asset: str,
    target_buffer: float,
    target_days_remaining: int = 365,
    maximum_buffer_difference: float = 10.0,
    maximum_days_difference: int = 120,
    maximum_protection_gap: float | None = None,
    minimum_days_remaining: int = 30,
) -> dict[str, Any] | None:
    normalized_asset = reference_asset.strip().upper()

    if normalized_asset not in SUPPORTED_REFERENCE_ASSETS:
        raise ValueError(
            "reference_asset must be SPY, QQQ, EFA, or EEM"
        )

    if not math.isfinite(target_buffer):
        raise ValueError(
            "target_buffer must be a finite number"
        )

    if not 0 <= target_buffer <= 100:
        raise ValueError(
            "target_buffer must be between 0 and 100"
        )

    if target_days_remaining < 1:
        raise ValueError(
            "target_days_remaining must be positive"
        )

    if maximum_buffer_difference < 0:
        raise ValueError(
            "maximum_buffer_difference cannot be negative"
        )

    if maximum_days_difference < 0:
        raise ValueError(
            "maximum_days_difference cannot be negative"
        )

    if (
        maximum_protection_gap is not None
        and maximum_protection_gap < 0
    ):
        raise ValueError(
            "maximum_protection_gap cannot be negative"
        )

    products = get_all_defined_outcomes()

    approved_candidates: list[dict[str, Any]] = []

    for product in products:
        if product.get("reference_asset") != normalized_asset:
            continue

        if not is_approved_buffer_product(
            product,
            minimum_days_remaining=minimum_days_remaining,
        ):
            continue

        remaining_buffer = product.get("remaining_buffer")
        days_remaining = product.get("days_remaining")
        remaining_cap = product.get("remaining_cap")

        if (
            remaining_buffer is None
            or days_remaining is None
            or remaining_cap is None
        ):
            continue

        protection_gap = get_protection_gap(product)

        if not math.isfinite(protection_gap):
            continue

        buffer_difference = (
            float(remaining_buffer) - target_buffer
        )

        days_difference = (
            int(days_remaining) - target_days_remaining
        )

        # Hard filters:
        # Do not improve the protection gap by returning a product
        # that materially misses the requested buffer or duration.
        if (
            abs(buffer_difference)
            > maximum_buffer_difference
        ):
            continue

        if (
            abs(days_difference)
            > maximum_days_difference
        ):
            continue

        if (
            maximum_protection_gap is not None
            and protection_gap > maximum_protection_gap
        ):
            continue

        candidate = {
            **product,
            "_protection_gap": protection_gap,
            "_buffer_difference": buffer_difference,
            "_days_difference": days_difference,
        }

        approved_candidates.append(candidate)

    if not approved_candidates:
        return None

    # Ranking order:
    #
    # 1. Smallest decline before protection begins
    # 2. Closest remaining buffer to the user's request
    # 3. Closest duration to the user's request
    # 4. Highest remaining upside cap
    approved_candidates.sort(
        key=lambda product: (
            product["_protection_gap"],
            abs(product["_buffer_difference"]),
            abs(product["_days_difference"]),
            -float(product["remaining_cap"]),
        )
    )

    selected = approved_candidates[0]

    protection_gap = selected.pop("_protection_gap")
    buffer_difference = selected.pop("_buffer_difference")
    days_difference = selected.pop("_days_difference")

    return {
        "requested": {
            "reference_asset": normalized_asset,
            "target_buffer": target_buffer,
            "target_days_remaining": target_days_remaining,
        },
        "match": selected,
        "protection_gap": round(
            protection_gap,
            2,
        ),
        "buffer_difference": round(
            buffer_difference,
            2,
        ),
        "absolute_buffer_difference": round(
            abs(buffer_difference),
            2,
        ),
        "days_difference": days_difference,
        "absolute_days_difference": abs(
            days_difference
        ),
        "retrieved_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": (
            "Innovator public defined outcome table"
        ),
    }



