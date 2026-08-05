from __future__ import annotations

import os
import tempfile
from pathlib import Path
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import paramiko


HOST = os.getenv("ORATS_SFTP_HOST", "ftp.hostedftp.com")
PORT = int(os.getenv("ORATS_SFTP_PORT", "22"))
USERNAME = os.getenv("ORATS_SFTP_USERNAME")
PASSWORD = os.getenv("ORATS_SFTP_PASSWORD")
REMOTE_PATH = os.getenv(
    "ORATS_SFTP_REMOTE_PATH",
    "/dividends/ORATSStockDivForecasts.txt",
)


def download_forecast_file() -> Path:
    if not USERNAME:
        raise RuntimeError("Missing ORATS_SFTP_USERNAME")

    if not PASSWORD:
        raise RuntimeError("Missing ORATS_SFTP_PASSWORD")

    temp_dir = Path(tempfile.mkdtemp(prefix="orats-dividends-"))
    local_path = temp_dir / "ORATSStockDivForecasts.txt"

    transport = paramiko.Transport((HOST, PORT))

    try:
        transport.connect(
            username=USERNAME,
            password=PASSWORD,
        )

        sftp = paramiko.SFTPClient.from_transport(transport)

        try:
            sftp.get(
                REMOTE_PATH,
                str(local_path),
            )
        finally:
            sftp.close()

    finally:
        transport.close()

    if not local_path.exists():
        raise RuntimeError("ORATS dividend forecast file was not downloaded")

    if local_path.stat().st_size == 0:
        raise RuntimeError("Downloaded ORATS dividend forecast file is empty")

    return local_path


@dataclass(frozen=True)
class DividendForecast:
    ticker: str
    ex_date: date
    amount_per_share: Decimal
    frequency: int


def parse_forecast_line(
    line: str,
    *,
    line_number: int,
) -> DividendForecast | None:
    stripped = line.strip()

    if not stripped:
        return None

    parts = stripped.split()

    if len(parts) != 4:
        raise ValueError(
            f"Line {line_number} has {len(parts)} fields; "
            f"expected 4: {line!r}"
        )

    ticker_raw, ex_date_raw, amount_raw, frequency_raw = parts

    ticker = ticker_raw.strip().upper()

    if not ticker:
        raise ValueError(
            f"Line {line_number} has an empty ticker"
        )

    try:
        parsed_ex_date = date.fromisoformat(ex_date_raw)
    except ValueError as exc:
        raise ValueError(
            f"Line {line_number} has an invalid ex-date: "
            f"{ex_date_raw!r}"
        ) from exc

    try:
        amount_per_share = Decimal(amount_raw)
    except InvalidOperation as exc:
        raise ValueError(
            f"Line {line_number} has an invalid dividend amount: "
            f"{amount_raw!r}"
        ) from exc

    try:
        frequency = int(frequency_raw)
    except ValueError as exc:
        raise ValueError(
            f"Line {line_number} has an invalid frequency: "
            f"{frequency_raw!r}"
        ) from exc

    if amount_per_share < 0:
        raise ValueError(
            f"Line {line_number} has a negative dividend amount"
        )

    if frequency < 0:
        raise ValueError(
            f"Line {line_number} has a negative frequency"
        )

    return DividendForecast(
        ticker=ticker,
        ex_date=parsed_ex_date,
        amount_per_share=amount_per_share,
        frequency=frequency,
    )


def parse_forecast_file(
    path: Path,
) -> list[DividendForecast]:
    rows: list[DividendForecast] = []

    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            parsed = parse_forecast_line(
                line,
                line_number=line_number,
            )

            if parsed is not None:
                rows.append(parsed)

    if not rows:
        raise ValueError(
            "ORATS dividend forecast file contained no rows"
        )

    return rows




if __name__ == "__main__":
    downloaded_path = download_forecast_file()

    print(f"Downloaded: {downloaded_path}")
    print(f"Bytes: {downloaded_path.stat().st_size}")