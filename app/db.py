import os
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL environment variable")

    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS snaptrade_users (
                    parity_user_id TEXT PRIMARY KEY,
                    snaptrade_user_id TEXT NOT NULL,
                    encrypted_user_secret TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS brokerage_accounts (
                    id TEXT PRIMARY KEY,
                    parity_user_id TEXT NOT NULL,
                    institution_name TEXT,
                    account_name TEXT,
                    account_number_mask TEXT,
                    total_value NUMERIC,
                    raw_json JSONB,
                    last_synced_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS holdings (
                    id SERIAL PRIMARY KEY,
                    parity_user_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    symbol TEXT,
                    quantity NUMERIC,
                    price NUMERIC,
                    market_value NUMERIC,
                    asset_type TEXT,
                    raw_json JSONB,
                    synced_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS portfolio_recommendations (
                    id SERIAL PRIMARY KEY,
                    parity_user_id TEXT NOT NULL,
                    account_id TEXT,
                    recommended_etf TEXT,
                    reason TEXT,
                    raw_json JSONB,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            conn.commit()