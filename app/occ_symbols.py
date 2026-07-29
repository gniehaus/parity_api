from datetime import date, datetime
from decimal import Decimal, InvalidOperation


def format_occ_option_symbol(
    ticker: str,
    expiration: str | date | datetime,
    option_type: str,
    strike: str | float | Decimal,
) -> str:
    """
    Return a 21-character OCC option symbol accepted by SnapTrade.

    Example:
    SPY, 2027-06-30, PUT, 741
    -> "SPY   270630P00741000"
    """

    root = ticker.strip().upper()

    if not root or len(root) > 6:
        raise ValueError(
            "OCC option roots must contain between 1 and 6 characters"
        )

    if isinstance(expiration, datetime):
        expiration_date = expiration.date()
    elif isinstance(expiration, date):
        expiration_date = expiration
    elif isinstance(expiration, str):
        try:
            expiration_date = datetime.strptime(
                expiration,
                "%Y-%m-%d",
            ).date()
        except ValueError as exc:
            raise ValueError(
                "expiration must use YYYY-MM-DD format"
            ) from exc
    else:
        raise ValueError("expiration must be a date or YYYY-MM-DD string")

    normalized_type = option_type.strip().upper()

    option_side = {
        "CALL": "C",
        "C": "C",
        "PUT": "P",
        "P": "P",
    }.get(normalized_type)

    if option_side is None:
        raise ValueError("option_type must be CALL, C, PUT, or P")

    try:
        strike_millis = Decimal(str(strike)) * Decimal("1000")
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("strike must be a valid number") from exc

    if strike_millis != strike_millis.to_integral_value():
        raise ValueError(
            "strike must be representable to three decimal places"
        )

    strike_code = int(strike_millis)

    if strike_code < 0 or strike_code > 99_999_999:
        raise ValueError("strike is outside the OCC symbol range")

    return (
        f"{root.ljust(6)}"
        f"{expiration_date:%y%m%d}"
        f"{option_side}"
        f"{strike_code:08d}"
    )