import os
from typing import Any

import requests


ORATS_TOKEN = os.getenv("ORATS_TOKEN")

ORATS_SUMMARIES_URL = (
    "https://api.orats.io/datav2/live/one-minute/summaries"
)


def fetch_all_orats_summaries() -> list[dict[str, Any]]:
    if not ORATS_TOKEN:
        raise RuntimeError("Missing ORATS_TOKEN environment variable")

    response = requests.get(
        ORATS_SUMMARIES_URL,
        params={"token": ORATS_TOKEN},
        timeout=120,
    )

    response.raise_for_status()

    content_type = response.headers.get("content-type", "")

    if "application/json" in content_type:
        payload = response.json()

        if isinstance(payload, dict):
            rows = payload.get("data", [])
        elif isinstance(payload, list):
            rows = payload
        else:
            raise RuntimeError(
                "Unexpected ORATS summaries response format"
            )

        return rows

    # The one-minute endpoint may return CSV.
    import pandas as pd
    from io import StringIO

    frame = pd.read_csv(StringIO(response.text))

    return frame.where(frame.notna(), None).to_dict(
        orient="records"
    )