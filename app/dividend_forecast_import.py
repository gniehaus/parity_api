from __future__ import annotations

import os
import tempfile
from pathlib import Path
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from .db import get_conn, init_db

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


def create_import_record(
    *,
    source_filename: str,
) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dividend_forecast_imports (
                    source,
                    source_filename,
                    status
                )
                VALUES (
                    'ORATS',
                    %s,
                    'RUNNING'
                )
                RETURNING id
                """,
                (source_filename,),
            )

            row = cur.fetchone()
            conn.commit()

    return str(row["id"])


def mark_import_failed(
    *,
    import_id: str,
    error_message: str,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dividend_forecast_imports
                SET
                    status = 'FAILED',
                    completed_at = NOW(),
                    error_message = %s
                WHERE id = %s
                """,
                (
                    error_message[:4000],
                    import_id,
                ),
            )
            conn.commit()


def replace_dividend_forecasts(
    *,
    rows: list[DividendForecast],
    source_filename: str,
    import_id: str,
) -> int:
    if len(rows) < 10000:
        raise ValueError(
            f"Refusing to replace dividend forecasts with only "
            f"{len(rows)} rows"
        )

    imported_at = datetime.now(timezone.utc)

    values = [
        (
            row.ticker,
            row.ex_date,
            row.amount_per_share,
            row.frequency,
            "ORATS",
            source_filename,
            imported_at,
        )
        for row in rows
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TEMP TABLE dividend_forecasts_stage
                (
                    LIKE dividend_forecasts
                    INCLUDING DEFAULTS
                    INCLUDING CONSTRAINTS
                )
                ON COMMIT DROP
                """
            )

            cur.executemany(
                """
                INSERT INTO dividend_forecasts_stage (
                    ticker,
                    ex_date,
                    amount_per_share,
                    frequency,
                    source,
                    source_filename,
                    imported_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                values,
            )

            cur.execute(
                """
                SELECT COUNT(*) AS row_count
                FROM dividend_forecasts_stage
                """
            )
            staged_count = cur.fetchone()["row_count"]

            if staged_count != len(rows):
                raise ValueError(
                    f"Staged row count {staged_count} does not match "
                    f"parsed row count {len(rows)}"
                )

            cur.execute("TRUNCATE TABLE dividend_forecasts")

            cur.execute(
                """
                INSERT INTO dividend_forecasts (
                    ticker,
                    ex_date,
                    amount_per_share,
                    frequency,
                    source,
                    source_filename,
                    imported_at
                )
                SELECT
                    ticker,
                    ex_date,
                    amount_per_share,
                    frequency,
                    source,
                    source_filename,
                    imported_at
                FROM dividend_forecasts_stage
                """
            )

            cur.execute(
                """
                UPDATE dividend_forecast_imports
                SET
                    status = 'COMPLETE',
                    completed_at = NOW(),
                    row_count = %s,
                    error_message = NULL
                WHERE id = %s
                """,
                (
                    staged_count,
                    import_id,
                ),
            )

            conn.commit()

    return staged_count


def import_dividend_forecasts() -> dict:
    init_db()

    source_filename = "ORATSStockDivForecasts.txt"

    import_id = create_import_record(
        source_filename=source_filename,
    )

    try:
        downloaded_path = download_forecast_file()
        rows = parse_forecast_file(downloaded_path)

        imported_count = replace_dividend_forecasts(
            rows=rows,
            source_filename=source_filename,
            import_id=import_id,
        )

        return {
            "status": "COMPLETE",
            "import_id": import_id,
            "row_count": imported_count,
            "source_filename": source_filename,
        }

    except Exception as exc:
        mark_import_failed(
            import_id=import_id,
            error_message=str(exc),
        )
        raise



if __name__ == "__main__":
    result = import_dividend_forecasts()
    print(result)