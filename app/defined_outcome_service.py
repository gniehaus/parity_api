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