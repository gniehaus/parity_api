import os
import json
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
                CREATE TABLE IF NOT EXISTS parity_users (
                    id TEXT PRIMARY KEY,
                    email TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    last_login_at TIMESTAMP DEFAULT NOW(),
                    raw_json JSONB
                );


                CREATE TABLE IF NOT EXISTS investor_profiles (
                    parity_user_id TEXT PRIMARY KEY
                        REFERENCES parity_users(id)
                        ON DELETE CASCADE,
            
                    recommendation_use TEXT,
                    primary_goal TEXT,
                    max_acceptable_loss NUMERIC,
                    time_horizon TEXT,
                    liquidity_need TEXT,
                    tradeoff_preference TEXT,
                    investment_experience TEXT,
                    scope TEXT,
                    new_investment_amount NUMERIC,
            
                    contradiction_acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
                    completed BOOLEAN NOT NULL DEFAULT FALSE,
                    completed_at TIMESTAMPTZ,
            
                    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

    CREATE INDEX IF NOT EXISTS idx_investor_profiles_completed
    ON investor_profiles(completed);

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

                CREATE TABLE IF NOT EXISTS normalized_holdings (
                    id SERIAL PRIMARY KEY,

                    parity_user_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,

                    symbol TEXT,
                    raw_symbol TEXT,
                    display_name TEXT,
                    description TEXT,
                    cusip TEXT,
                    isin TEXT,
                    figi TEXT,

                    asset_class TEXT NOT NULL DEFAULT 'unknown',
                    security_type TEXT NOT NULL DEFAULT 'unknown',
                    asset_subtype TEXT,
                    currency TEXT DEFAULT 'USD',

                    quantity NUMERIC,
                    price NUMERIC,
                    market_value NUMERIC,
                    cost_basis NUMERIC,
                    unrealized_gain_loss NUMERIC,
                    unrealized_gain_loss_pct NUMERIC,

                    position_direction TEXT DEFAULT 'long',
                    exposure_value NUMERIC,
                    is_cash BOOLEAN DEFAULT false,
                    is_margin BOOLEAN DEFAULT false,
                    is_short BOOLEAN DEFAULT false,

                    is_option BOOLEAN DEFAULT false,
                    underlying_symbol TEXT,
                    option_type TEXT,
                    expiration_date DATE,
                    strike_price NUMERIC,
                    multiplier NUMERIC,
                    contract_count NUMERIC,

                    maturity_date DATE,
                    coupon_rate NUMERIC,
                    face_value NUMERIC,
                    yield_rate NUMERIC,

                    expense_ratio NUMERIC,
                    fund_family TEXT,

                    source TEXT DEFAULT 'snaptrade',
                    raw_json JSONB,
                    synced_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS plaid_items (
                    id SERIAL PRIMARY KEY,
                    parity_user_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    encrypted_access_token TEXT NOT NULL,
                    institution_name TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    last_synced_at TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS bank_accounts (
                    id TEXT PRIMARY KEY,
                    parity_user_id TEXT NOT NULL,
                    plaid_item_id TEXT,
                    name TEXT,
                    official_name TEXT,
                    subtype TEXT,
                    type TEXT,
                    mask TEXT,
                    current_balance NUMERIC,
                    available_balance NUMERIC,
                    iso_currency_code TEXT,
                    raw_json JSONB,
                    last_synced_at TIMESTAMP DEFAULT NOW()
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

                CREATE INDEX IF NOT EXISTS idx_brokerage_accounts_user
                ON brokerage_accounts(parity_user_id);

                CREATE INDEX IF NOT EXISTS idx_holdings_user
                ON holdings(parity_user_id);

                CREATE INDEX IF NOT EXISTS idx_holdings_user_account
                ON holdings(parity_user_id, account_id);

                CREATE INDEX IF NOT EXISTS idx_holdings_symbol
                ON holdings(symbol);

                CREATE INDEX IF NOT EXISTS idx_normalized_holdings_user
                ON normalized_holdings(parity_user_id);

                CREATE INDEX IF NOT EXISTS idx_normalized_holdings_user_account
                ON normalized_holdings(parity_user_id, account_id);

                CREATE INDEX IF NOT EXISTS idx_normalized_holdings_symbol
                ON normalized_holdings(symbol);

                CREATE INDEX IF NOT EXISTS idx_normalized_holdings_asset_class
                ON normalized_holdings(asset_class);

                CREATE INDEX IF NOT EXISTS idx_normalized_holdings_security_type
                ON normalized_holdings(security_type);

                CREATE INDEX IF NOT EXISTS idx_normalized_holdings_is_option
                ON normalized_holdings(is_option);

                CREATE INDEX IF NOT EXISTS idx_normalized_holdings_is_cash
                ON normalized_holdings(is_cash);
            """)
            conn.commit()
from typing import Any


def get_investor_profile(parity_user_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    parity_user_id,
                    recommendation_use,
                    primary_goal,
                    max_acceptable_loss,
                    time_horizon,
                    liquidity_need,
                    tradeoff_preference,
                    investment_experience,
                    scope,
                    new_investment_amount,
                    contradiction_acknowledged,
                    completed,
                    completed_at,
                    raw_json,
                    created_at,
                    updated_at
                FROM investor_profiles
                WHERE parity_user_id = %s
                """,
                (parity_user_id,),
            )

            row = cur.fetchone()
            return row if row else None


def upsert_investor_profile(
    parity_user_id: str,
    recommendation_use: str | None = None,
    primary_goal: str | None = None,
    max_acceptable_loss: float | None = None,
    time_horizon: str | None = None,
    liquidity_need: str | None = None,
    tradeoff_preference: str | None = None,
    investment_experience: str | None = None,
    scope: str | None = None,
    new_investment_amount: float | None = None,
    contradiction_acknowledged: bool = False,
    completed: bool = False,
    raw: dict | None = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Ensure a parent user exists before inserting the profile.
            cur.execute(
                """
                INSERT INTO parity_users (id, created_at, last_login_at)
                VALUES (%s, NOW(), NOW())
                ON CONFLICT (id)
                DO UPDATE SET last_login_at = NOW()
                """,
                (parity_user_id,),
            )

            cur.execute(
                """
                INSERT INTO investor_profiles (
                    parity_user_id,
                    recommendation_use,
                    primary_goal,
                    max_acceptable_loss,
                    time_horizon,
                    liquidity_need,
                    tradeoff_preference,
                    investment_experience,
                    scope,
                    new_investment_amount,
                    contradiction_acknowledged,
                    completed,
                    completed_at,
                    raw_json,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    CASE WHEN %s = TRUE THEN NOW() ELSE NULL END,
                    %s::jsonb,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (parity_user_id)
                DO UPDATE SET
                    recommendation_use = EXCLUDED.recommendation_use,
                    primary_goal = EXCLUDED.primary_goal,
                    max_acceptable_loss = EXCLUDED.max_acceptable_loss,
                    time_horizon = EXCLUDED.time_horizon,
                    liquidity_need = EXCLUDED.liquidity_need,
                    tradeoff_preference = EXCLUDED.tradeoff_preference,
                    investment_experience = EXCLUDED.investment_experience,
                    scope = EXCLUDED.scope,
                    new_investment_amount = EXCLUDED.new_investment_amount,
                    contradiction_acknowledged =
                        EXCLUDED.contradiction_acknowledged,
                    completed = EXCLUDED.completed,
                    completed_at = CASE
                        WHEN EXCLUDED.completed = TRUE
                        THEN COALESCE(
                            investor_profiles.completed_at,
                            NOW()
                        )
                        ELSE NULL
                    END,
                    raw_json = EXCLUDED.raw_json,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    parity_user_id,
                    recommendation_use,
                    primary_goal,
                    max_acceptable_loss,
                    time_horizon,
                    liquidity_need,
                    tradeoff_preference,
                    investment_experience,
                    scope,
                    new_investment_amount,
                    contradiction_acknowledged,
                    completed,
                    completed,
                    json.dumps(raw or {}),
                ),
            )

            profile = cur.fetchone()
            conn.commit()

            if not profile:
                raise RuntimeError("Investor profile was not saved")

            return profile

def upsert_parity_user(
    user_id: str,
    email: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    raw: dict | None = None,
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO parity_users (
                    id,
                    email,
                    first_name,
                    last_name,
                    raw_json,
                    created_at,
                    last_login_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, NOW(), NOW())
                ON CONFLICT (id)
                DO UPDATE SET
                    email = COALESCE(EXCLUDED.email, parity_users.email),
                    first_name = COALESCE(EXCLUDED.first_name, parity_users.first_name),
                    last_name = COALESCE(EXCLUDED.last_name, parity_users.last_name),
                    raw_json = COALESCE(EXCLUDED.raw_json, parity_users.raw_json),
                    last_login_at = NOW()
                """,
                (
                    user_id,
                    email,
                    first_name,
                    last_name,
                    json.dumps(raw or {}),
                ),
            )
            conn.commit()