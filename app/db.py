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

                CREATE EXTENSION IF NOT EXISTS pgcrypto;

                CREATE TABLE IF NOT EXISTS recommendation_runs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                
                    parity_user_id TEXT NOT NULL
                        REFERENCES parity_users(id)
                        ON DELETE CASCADE,
                
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    engine_version TEXT NOT NULL,
                
                    profile_version TEXT,
                    profile_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                
                    portfolio_signature TEXT NOT NULL,
                    portfolio_payload JSONB,
                
                    accounts_count INTEGER NOT NULL DEFAULT 0,
                    total_assets NUMERIC(16, 2),
                    cash_pct NUMERIC(8, 6),
                    portfolio_iv NUMERIC(8, 6),
                
                    analysis_only BOOLEAN NOT NULL DEFAULT FALSE,
                    recommendation_count INTEGER NOT NULL DEFAULT 0,
                    aggregate_benefit NUMERIC(16, 2),
                
                    hero_title TEXT,
                    hero_ticker TEXT,
                
                    market_data_timestamp TIMESTAMPTZ,
                
                    superseded_by UUID
                        REFERENCES recommendation_runs(id)
                        ON DELETE SET NULL,
                
                    is_current BOOLEAN NOT NULL DEFAULT TRUE
                );
                
                CREATE TABLE IF NOT EXISTS recommendations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                
                    run_id UUID NOT NULL
                        REFERENCES recommendation_runs(id)
                        ON DELETE CASCADE,
                
                    parity_user_id TEXT NOT NULL
                        REFERENCES parity_users(id)
                        ON DELETE CASCADE,
                
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                
                    type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                
                    evidence TEXT,
                    diagnosis TEXT,
                    recommended_action TEXT,
                
                    account_id TEXT,
                    account_name TEXT,
                    account_type TEXT,
                
                    suggested_exposure TEXT,
                    product_type TEXT,
                    ticker TEXT,
                
                    severity_score NUMERIC(6, 2),
                    impact_score NUMERIC(6, 2),
                    confidence_score NUMERIC(6, 2),
                    recommendation_score NUMERIC(6, 2) NOT NULL,
                    rank INTEGER,
                
                    dollar_benefit NUMERIC(16, 2),
                    benefit_label TEXT,
                
                    deploy_amount NUMERIC(16, 2),
                    sgov_amount NUMERIC(16, 2),
                    remaining_cash NUMERIC(16, 2),
                
                    actionable BOOLEAN NOT NULL DEFAULT FALSE,
                    eligible BOOLEAN NOT NULL DEFAULT FALSE,
                
                    eligibility_reasons TEXT[],
                    product_match JSONB,
                    implementation JSONB,
                    assumptions JSONB,
                
                    household_fit TEXT,
                    supporting_diagnostics TEXT[],
                
                    based_on JSONB NOT NULL DEFAULT '{}'::jsonb,
                
                    status TEXT NOT NULL DEFAULT 'generated',
                    viewed_at TIMESTAMPTZ,
                    dismissed_at TIMESTAMPTZ,
                    actioned_at TIMESTAMPTZ,
                    action_reference TEXT,
                
                    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb
                );
                
                CREATE TABLE IF NOT EXISTS recommendation_findings (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                
                    run_id UUID NOT NULL
                        REFERENCES recommendation_runs(id)
                        ON DELETE CASCADE,
                
                    parity_user_id TEXT NOT NULL
                        REFERENCES parity_users(id)
                        ON DELETE CASCADE,
                
                    detector_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                
                    confidence NUMERIC(6, 4),
                    dollar_benefit NUMERIC(16, 2),
                    benefit_type TEXT,
                
                    suggested_exposure TEXT,
                    suggested_products TEXT[],
                    priority NUMERIC(8, 6),
                
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                
                    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb
                );
                
                CREATE INDEX IF NOT EXISTS idx_recommendation_runs_user_current
                ON recommendation_runs (
                    parity_user_id,
                    is_current,
                    generated_at DESC
                );
                
                CREATE INDEX IF NOT EXISTS idx_recommendation_runs_signature
                ON recommendation_runs (
                    parity_user_id,
                    portfolio_signature
                );
                
                CREATE INDEX IF NOT EXISTS idx_recommendations_run
                ON recommendations(run_id);
                
                CREATE INDEX IF NOT EXISTS idx_recommendations_user_status
                ON recommendations (
                    parity_user_id,
                    status,
                    generated_at DESC
                );
                
                CREATE INDEX IF NOT EXISTS idx_recommendations_type
                ON recommendations (
                    type,
                    generated_at DESC
                );
                
                CREATE INDEX IF NOT EXISTS idx_recommendation_findings_run
                ON recommendation_findings(run_id);
                
                CREATE INDEX IF NOT EXISTS idx_recommendation_findings_user
                ON recommendation_findings (
                    parity_user_id,
                    generated_at DESC
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

                CREATE INDEX IF NOT EXISTS idx_portfolio_recommendations_user
                ON portfolio_recommendations(parity_user_id);
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

from typing import Any
import json


PROFILE_FIELDS = (
    "recommendation_use",
    "primary_goal",
    "max_acceptable_loss",
    "time_horizon",
    "liquidity_need",
    "tradeoff_preference",
    "investment_experience",
    "scope",
    "new_investment_amount",
    "contradiction_acknowledged",
    "completed",
)

from typing import Any
import json


def persist_recommendation_run(
    parity_user_id: str,
    engine_version: str,
    profile_version: str | None,
    profile_payload: dict[str, Any],
    portfolio_signature: str,
    portfolio_payload: dict[str, Any] | None,
    accounts_count: int,
    total_assets: float | None,
    cash_pct: float | None,
    portfolio_iv: float | None,
    analysis_only: bool,
    aggregate_benefit: float | None,
    hero_title: str | None,
    hero_ticker: str | None,
    market_data_timestamp: str | None,
    recommendations: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Persist one complete frontend recommendation-engine execution.

    The previous current run is superseded, and all new recommendation
    and finding rows are written in the same database transaction.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Ensure the parent user exists.
            cur.execute(
                """
                INSERT INTO parity_users (
                    id,
                    created_at,
                    last_login_at
                )
                VALUES (%s, NOW(), NOW())
                ON CONFLICT (id)
                DO UPDATE SET
                    last_login_at = NOW()
                """,
                (parity_user_id,),
            )

            # Insert the new run first so that it has a UUID.
            cur.execute(
                """
                INSERT INTO recommendation_runs (
                    parity_user_id,
                    engine_version,
                    profile_version,
                    profile_payload,
                    portfolio_signature,
                    portfolio_payload,
                    accounts_count,
                    total_assets,
                    cash_pct,
                    portfolio_iv,
                    analysis_only,
                    recommendation_count,
                    aggregate_benefit,
                    hero_title,
                    hero_ticker,
                    market_data_timestamp,
                    is_current
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s,
                    %s::jsonb,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    TRUE
                )
                RETURNING *
                """,
                (
                    parity_user_id,
                    engine_version,
                    profile_version,
                    json.dumps(profile_payload or {}),
                    portfolio_signature,
                    json.dumps(portfolio_payload)
                    if portfolio_payload is not None
                    else None,
                    accounts_count,
                    total_assets,
                    cash_pct,
                    portfolio_iv,
                    analysis_only,
                    len(recommendations),
                    aggregate_benefit,
                    hero_title,
                    hero_ticker,
                    market_data_timestamp,
                ),
            )

            new_run = cur.fetchone()

            if not new_run:
                raise RuntimeError(
                    "Recommendation run could not be created"
                )

            new_run_id = new_run["id"]

            # Supersede every older current run for this user.
            cur.execute(
                """
                UPDATE recommendation_runs
                SET
                    is_current = FALSE,
                    superseded_by = %s
                WHERE parity_user_id = %s
                  AND is_current = TRUE
                  AND id <> %s
                """,
                (
                    new_run_id,
                    parity_user_id,
                    new_run_id,
                ),
            )

            # Mark recommendations from older runs as superseded.
            cur.execute(
                """
                UPDATE recommendations
                SET status = 'superseded'
                WHERE parity_user_id = %s
                  AND run_id <> %s
                  AND status = 'generated'
                """,
                (
                    parity_user_id,
                    new_run_id,
                ),
            )

            saved_recommendations: list[dict[str, Any]] = []

            for index, recommendation in enumerate(
                recommendations,
                start=1,
            ):
                implementation = (
                    recommendation.get("implementation") or {}
                )

                product_match = (
                    recommendation.get("productMatch") or {}
                )

                ticker = (
                    implementation.get("ticker")
                    or product_match.get("ticker")
                    or recommendation.get("ticker")
                )

                cur.execute(
                    """
                    INSERT INTO recommendations (
                        run_id,
                        parity_user_id,
                        type,
                        category,
                        title,
                        evidence,
                        diagnosis,
                        recommended_action,
                        account_id,
                        account_name,
                        account_type,
                        suggested_exposure,
                        product_type,
                        ticker,
                        severity_score,
                        impact_score,
                        confidence_score,
                        recommendation_score,
                        rank,
                        dollar_benefit,
                        benefit_label,
                        deploy_amount,
                        sgov_amount,
                        remaining_cash,
                        actionable,
                        eligible,
                        eligibility_reasons,
                        product_match,
                        implementation,
                        assumptions,
                        household_fit,
                        supporting_diagnostics,
                        based_on,
                        status,
                        raw_json
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        %s::jsonb,
                        %s::jsonb,
                        %s,
                        %s,
                        %s::jsonb,
                        %s,
                        %s::jsonb
                    )
                    RETURNING *
                    """,
                    (
                        new_run_id,
                        parity_user_id,
                        recommendation.get("type"),
                        recommendation.get("category"),
                        recommendation.get("title"),
                        recommendation.get("evidence"),
                        recommendation.get("diagnosis"),
                        recommendation.get(
                            "recommendedAction"
                        ),
                        recommendation.get("accountId")
                        or implementation.get("accountId"),
                        recommendation.get("accountName")
                        or implementation.get("account"),
                        recommendation.get("accountType")
                        or implementation.get("accountType"),
                        recommendation.get(
                            "suggestedExposure"
                        ),
                        implementation.get("productType")
                        or recommendation.get("productType"),
                        ticker,
                        recommendation.get("severityScore"),
                        recommendation.get("impactScore"),
                        recommendation.get(
                            "confidenceScore"
                        ),
                        recommendation.get(
                            "recommendationScore",
                            0,
                        ),
                        recommendation.get("rank", index),
                        recommendation.get("dollarBenefit"),
                        recommendation.get("benefitLabel"),
                        implementation.get("deployAmount"),
                        implementation.get("sgovAmount"),
                        implementation.get("remainingCash"),
                        bool(
                            recommendation.get(
                                "actionable",
                                False,
                            )
                        ),
                        bool(
                            product_match.get(
                                "eligible",
                                recommendation.get(
                                    "eligible",
                                    False,
                                ),
                            )
                        ),
                        recommendation.get(
                            "eligibilityReasons"
                        )
                        or product_match.get("reasons")
                        or [],
                        json.dumps(product_match),
                        json.dumps(implementation),
                        json.dumps(
                            recommendation.get(
                                "assumptions"
                            )
                            or {}
                        ),
                        recommendation.get("householdFit"),
                        recommendation.get(
                            "supportingDiagnostics"
                        )
                        or [],
                        json.dumps(
                            recommendation.get("basedOn")
                            or {}
                        ),
                        recommendation.get(
                            "status",
                            "generated",
                        ),
                        json.dumps(recommendation),
                    ),
                )

                saved = cur.fetchone()

                if saved:
                    saved_recommendations.append(saved)

            saved_findings: list[dict[str, Any]] = []

            for finding in findings:
                cur.execute(
                    """
                    INSERT INTO recommendation_findings (
                        run_id,
                        parity_user_id,
                        detector_id,
                        category,
                        evidence,
                        confidence,
                        dollar_benefit,
                        benefit_type,
                        suggested_exposure,
                        suggested_products,
                        priority,
                        raw_json
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb
                    )
                    RETURNING *
                    """,
                    (
                        new_run_id,
                        parity_user_id,
                        finding.get("detectorId")
                        or finding.get("detector_id"),
                        finding.get("category"),
                        finding.get("evidence"),
                        finding.get("confidence"),
                        finding.get("dollarBenefit")
                        or finding.get("dollar_benefit"),
                        finding.get("benefitType")
                        or finding.get("benefit_type"),
                        finding.get("suggestedExposure")
                        or finding.get(
                            "suggested_exposure"
                        ),
                        finding.get("suggestedProducts")
                        or finding.get(
                            "suggested_products"
                        )
                        or [],
                        finding.get("priority"),
                        json.dumps(finding),
                    ),
                )

                saved = cur.fetchone()

                if saved:
                    saved_findings.append(saved)

            conn.commit()

            return {
                "run": new_run,
                "recommendations": saved_recommendations,
                "findings": saved_findings,
            }


def get_current_recommendation_run(
    parity_user_id: str,
) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM recommendation_runs
                WHERE parity_user_id = %s
                  AND is_current = TRUE
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                (parity_user_id,),
            )

            run = cur.fetchone()

            if not run:
                return None

            cur.execute(
                """
                SELECT *
                FROM recommendations
                WHERE run_id = %s
                ORDER BY
                    rank ASC NULLS LAST,
                    recommendation_score DESC,
                    dollar_benefit DESC NULLS LAST
                """,
                (run["id"],),
            )

            recommendations = cur.fetchall()

            cur.execute(
                """
                SELECT *
                FROM recommendation_findings
                WHERE run_id = %s
                ORDER BY
                    priority DESC NULLS LAST,
                    generated_at ASC
                """,
                (run["id"],),
            )

            findings = cur.fetchall()

            return {
                "run": run,
                "recommendations": recommendations,
                "findings": findings,
            }
def save_investor_profile_and_invalidate_recommendations(
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
    """
    Saves the user's current investor profile.

    If any recommendation-relevant profile field changed, all existing
    portfolio recommendations for that user are deleted in the same
    transaction.

    The frontend can then regenerate recommendations.
    """

    new_profile_values = {
        "recommendation_use": recommendation_use,
        "primary_goal": primary_goal,
        "max_acceptable_loss": max_acceptable_loss,
        "time_horizon": time_horizon,
        "liquidity_need": liquidity_need,
        "tradeoff_preference": tradeoff_preference,
        "investment_experience": investment_experience,
        "scope": scope,
        "new_investment_amount": new_investment_amount,
        "contradiction_acknowledged": contradiction_acknowledged,
        "completed": completed,
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Ensure the parent parity_users row exists.
            cur.execute(
                """
                INSERT INTO parity_users (
                    id,
                    created_at,
                    last_login_at
                )
                VALUES (%s, NOW(), NOW())
                ON CONFLICT (id)
                DO UPDATE SET
                    last_login_at = NOW()
                """,
                (parity_user_id,),
            )

            # Lock the existing profile row while this transaction runs.
            cur.execute(
                """
                SELECT
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
                    completed
                FROM investor_profiles
                WHERE parity_user_id = %s
                FOR UPDATE
                """,
                (parity_user_id,),
            )

            existing_profile = cur.fetchone()

            if existing_profile is None:
                profile_changed = True
            else:
                profile_changed = any(
                    existing_profile.get(field) != new_profile_values[field]
                    for field in PROFILE_FIELDS
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
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    CASE
                        WHEN %s = TRUE THEN NOW()
                        ELSE NULL
                    END,
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

            saved_profile = cur.fetchone()

            invalidated_count = 0

            if profile_changed:
                cur.execute(
                    """
                    DELETE FROM portfolio_recommendations
                    WHERE parity_user_id = %s
                    """,
                    (parity_user_id,),
                )

                invalidated_count = cur.rowcount

            conn.commit()

            return {
                "profile": saved_profile,
                "profile_changed": profile_changed,
                "recommendations_invalidated": profile_changed,
                "invalidated_recommendation_count": invalidated_count,
            }



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