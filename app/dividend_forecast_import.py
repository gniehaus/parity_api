from __future__ import annotations

import os
import tempfile
from pathlib import Path

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


if __name__ == "__main__":
    downloaded_path = download_forecast_file()

    print(f"Downloaded: {downloaded_path}")
    print(f"Bytes: {downloaded_path.stat().st_size}")