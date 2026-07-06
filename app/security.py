import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("DATABASE_URL", "./parity_snaptrade.sqlite").replace("sqlite:///", ""))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snaptrade_users (
                app_user_id TEXT PRIMARY KEY,
                snaptrade_user_id TEXT NOT NULL UNIQUE,
                encrypted_user_secret TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_user_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
