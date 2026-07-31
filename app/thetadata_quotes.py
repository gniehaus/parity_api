from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Any

from thetadata import ThetaClient


class ThetaDataQuoteError(ValueError):
    pass


@lru_cache(maxsize=1)
def _theta_client() -> ThetaClient:
    return ThetaClient()


def _rows(frame: Any) -> list[dict[str, Any]]:
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()

    if hasattr(frame, "to_dict"):
        return frame.to_dict(orient="records")

    raise ThetaDataQuoteError(
        "ThetaData returned an unsupported quote response"
    )


def get_thetadata_option_quote(
    *,
    ticker: str,
    expiration: str,
    option_type: str,
    strike: str | float | Decimal,
) -> dict[str, Any]:
    """
    Return the latest real-time OPRA NBBO for one option contract.
    """

    normalized_type = str(option_type).strip().upper()

    if normalized_type not in {"CALL", "PUT"}:
        raise ThetaDataQuoteError(
            "option_type must be CALL or PUT"
        )

    expiration_date = date.fromisoformat(
        str(expiration)[:10]
    )

    requested_strike = float(
        Decimal(str(strike))
    )

    frame = _theta_client().option_snapshot_quote(
        symbol=str(ticker).strip().upper(),
        expiration=expiration_date,
        strike=format(
            Decimal(str(strike)).normalize(),
            "f",
        ),
        right=normalized_type.lower(),
    )

    rows = _rows(frame)

    matching_row = next(
        (
            row
            for row in rows
            if (
                str(row.get("symbol") or "").upper()
                == str(ticker).strip().upper()
                and str(row.get("right") or "").upper()
                == normalized_type
                and abs(
                    float(row.get("strike")) - requested_strike
                ) < 0.0001
            )
        ),
        None,
    )

    if not matching_row:
        raise ThetaDataQuoteError(
            "ThetaData did not return the requested option quote"
        )

    bid = float(matching_row.get("bid") or 0)
    ask = float(matching_row.get("ask") or 0)

    if bid < 0 or ask <= 0 or ask < bid:
        raise ThetaDataQuoteError(
            "ThetaData returned an invalid option market"
        )

    quote_time = matching_row.get("timestamp")

    if not isinstance(quote_time, datetime):
        raise ThetaDataQuoteError(
            "ThetaData quote is missing its timestamp"
        )

    if quote_time.tzinfo is None:
        raise ThetaDataQuoteError(
            "ThetaData quote timestamp is missing its timezone"
        )

    quote_timestamp = quote_time.astimezone(
        timezone.utc
    ).isoformat()

    return {
        "ticker": str(ticker).strip().upper(),
        "expiration": expiration_date.isoformat(),
        "option_type": normalized_type,
        "strike": requested_strike,
        "bid_per_share": round(bid, 4),
        "ask_per_share": round(ask, 4),
        "mid_per_share": round((bid + ask) / 2, 4),
        "spread_per_share": round(ask - bid, 4),
        "bid_size": int(matching_row.get("bid_size") or 0),
        "ask_size": int(matching_row.get("ask_size") or 0),
        "quote_timestamp": quote_timestamp,
        "source_updated_at": quote_timestamp,
        "source": "THETADATA_OPRA",
    }