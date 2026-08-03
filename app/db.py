import os
import json
import psycopg
from psycopg.rows import dict_row
from datetime import date, datetime
from .orats_summary_service import fetch_all_orats_summaries
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


                CREATE TABLE IF NOT EXISTS dividend_snapshots (
                ticker VARCHAR(12) PRIMARY KEY,
                stock_price DOUBLE PRECISION,
                annual_dividend_per_share DOUBLE PRECISION NOT NULL DEFAULT 0,
                annual_implied_dividend DOUBLE PRECISION,
                next_dividend_per_share DOUBLE PRECISION,
                implied_next_dividend_per_share DOUBLE PRECISION,
                dividend_yield DOUBLE PRECISION,
                source_updated_at TIMESTAMPTZ,
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                source VARCHAR(30) NOT NULL DEFAULT 'ORATS'
                        );
            
                CREATE INDEX IF NOT EXISTS idx_dividend_snapshots_fetched_at
                ON dividend_snapshots (fetched_at);





                CREATE TABLE IF NOT EXISTS orats_summary_snapshots (
                    ticker VARCHAR(20) PRIMARY KEY,
                
                    -- Market information
                    trade_date DATE,
                    stock_price DOUBLE PRECISION,
                
                    -- Dividend information
                    annual_actual_dividend DOUBLE PRECISION,
                    annual_implied_dividend DOUBLE PRECISION,
                    next_dividend DOUBLE PRECISION,
                    implied_next_dividend DOUBLE PRECISION,
                
                    -- Borrow and rates
                    borrow_30d DOUBLE PRECISION,
                    borrow_2y DOUBLE PRECISION,
                    risk_free_30d DOUBLE PRECISION,
                    risk_free_2y DOUBLE PRECISION,
                
                    -- Summary confidence
                    confidence DOUBLE PRECISION,
                    total_error_confidence DOUBLE PRECISION,
                
                    -- Standard implied volatility term structure
                    iv_10d DOUBLE PRECISION,
                    iv_20d DOUBLE PRECISION,
                    iv_30d DOUBLE PRECISION,
                    iv_60d DOUBLE PRECISION,
                    iv_90d DOUBLE PRECISION,
                    iv_6m DOUBLE PRECISION,
                    iv_1y DOUBLE PRECISION,
                
                    -- Earnings-adjusted implied volatility
                    ex_earnings_iv_10d DOUBLE PRECISION,
                    ex_earnings_iv_20d DOUBLE PRECISION,
                    ex_earnings_iv_30d DOUBLE PRECISION,
                    ex_earnings_iv_60d DOUBLE PRECISION,
                    ex_earnings_iv_90d DOUBLE PRECISION,
                    ex_earnings_iv_6m DOUBLE PRECISION,
                    ex_earnings_iv_1y DOUBLE PRECISION,
                
                    -- Earnings information
                    implied_earnings_effect DOUBLE PRECISION,
                    implied_move DOUBLE PRECISION,
                    implied_earnings_move DOUBLE PRECISION,
                
                    -- Term structure and volatility fit
                    mw_adjusted_30d DOUBLE PRECISION,
                    mw_adjusted_2y DOUBLE PRECISION,
                    residual_driver_30d DOUBLE PRECISION,
                    residual_driver_2y DOUBLE PRECISION,
                    residual_slope_30d DOUBLE PRECISION,
                    residual_slope_2y DOUBLE PRECISION,
                    residual_volatility_30d DOUBLE PRECISION,
                    residual_volatility_2y DOUBLE PRECISION,
                    relative_implied_price DOUBLE PRECISION,
                    skewing DOUBLE PRECISION,
                    contango DOUBLE PRECISION,
                
                    -- ORATS timestamps and identifiers
                    quote_date TIMESTAMPTZ,
                    source_updated_at TIMESTAMPTZ,
                    snapshot_est_time TEXT,
                    snapshot_date TIMESTAMPTZ,
                    ticker_id BIGINT,
                
                    -- Preserve every vendor field, including new ones
                    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                
                    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    source VARCHAR(40) NOT NULL DEFAULT 'ORATS'
                );
                
                CREATE INDEX IF NOT EXISTS idx_orats_summary_fetched_at
                ON orats_summary_snapshots (fetched_at DESC);
                
                CREATE INDEX IF NOT EXISTS idx_orats_summary_trade_date
                ON orats_summary_snapshots (trade_date DESC);
                
                CREATE INDEX IF NOT EXISTS idx_orats_summary_iv30
                ON orats_summary_snapshots (iv_30d);
                
                CREATE INDEX IF NOT EXISTS idx_orats_summary_ticker_id
                ON orats_summary_snapshots (ticker_id);
                                
                    
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
            CREATE TABLE IF NOT EXISTS advisory_clients (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            
                parity_user_id TEXT NOT NULL UNIQUE
                    REFERENCES parity_users(id)
                    ON DELETE RESTRICT,
            
                status TEXT NOT NULL DEFAULT 'onboarding'
                    CHECK (
                        status IN (
                            'onboarding',
                            'documents_complete',
                            'schwab_authorization_pending',
                            'active',
                            'restricted',
                            'terminated'
                        )
                    ),
            
                legal_first_name TEXT,
                legal_middle_name TEXT,
                legal_last_name TEXT,
                preferred_name TEXT,
            
                email TEXT NOT NULL,
                phone TEXT,
            
                onboarding_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                agreement_completed_at TIMESTAMPTZ,
                activated_at TIMESTAMPTZ,
                restricted_at TIMESTAMPTZ,
                terminated_at TIMESTAMPTZ,
            
                onboarding_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            
                CHECK (
                    status <> 'active'
                    OR activated_at IS NOT NULL
                ),
            
                CHECK (
                    status <> 'terminated'
                    OR terminated_at IS NOT NULL
                )
            );
            CREATE TABLE IF NOT EXISTS parity_subscriptions (
                parity_user_id TEXT PRIMARY KEY
                    REFERENCES parity_users(id)
                    ON DELETE CASCADE,
            
                subscription_tier TEXT NOT NULL DEFAULT 'free'
                    CHECK (
                        subscription_tier IN (
                            'free',
                            'connected',
                            'complete'
                        )
                    ),
            
                subscription_status TEXT NOT NULL DEFAULT 'none'
                    CHECK (
                        subscription_status IN (
                            'none',
                            'incomplete',
                            'trialing',
                            'active',
                            'past_due',
                            'canceled',
                            'unpaid',
                            'paused'
                        )
                    ),
            
                stripe_customer_id TEXT UNIQUE,
                stripe_subscription_id TEXT UNIQUE,
                stripe_price_id TEXT,
            
                current_period_start TIMESTAMPTZ,
                current_period_end TIMESTAMPTZ,
            
                cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
                canceled_at TIMESTAMPTZ,
            
                pending_tier TEXT
                    CHECK (
                        pending_tier IS NULL
                        OR pending_tier IN (
                            'connected',
                            'complete'
                        )
                    ),
                pending_change_at TIMESTAMPTZ,
            
                access_grace_until TIMESTAMPTZ,
            
                complimentary_snapshot_started_at TIMESTAMPTZ,
                complimentary_snapshot_expires_at TIMESTAMPTZ,
            
                last_event_key TEXT,
                last_event_type TEXT,
                last_event_at TIMESTAMPTZ,
            
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            
            CREATE TABLE IF NOT EXISTS parity_subscription_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            
                event_key TEXT NOT NULL UNIQUE,
            
                parity_user_id TEXT NOT NULL
                    REFERENCES parity_users(id)
                    ON DELETE CASCADE,
            
                event_type TEXT NOT NULL,
            
                previous_tier TEXT,
                new_tier TEXT,
                previous_status TEXT,
                new_status TEXT,
            
                event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                effective_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            
            CREATE INDEX IF NOT EXISTS idx_parity_subscriptions_status
            ON parity_subscriptions(subscription_status);
            
            CREATE INDEX IF NOT EXISTS idx_parity_subscription_events_user
            ON parity_subscription_events(
                parity_user_id,
                effective_at DESC
            );

            CREATE TABLE IF NOT EXISTS advisory_documents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            
                document_type TEXT NOT NULL
                    CHECK (
                        document_type IN (
                            'investment_advisory_agreement',
                            'form_adv_part_2a',
                            'form_adv_part_2b',
                            'privacy_notice',
                            'business_continuity_plan',
                            'electronic_delivery_consent',
                            'other'
                        )
                    ),
            
                version TEXT NOT NULL,
                title TEXT NOT NULL,
            
                storage_location TEXT NOT NULL,
                document_hash TEXT NOT NULL UNIQUE,
            
                effective_at TIMESTAMPTZ NOT NULL,
                retired_at TIMESTAMPTZ,
            
                required_for_activation BOOLEAN NOT NULL DEFAULT TRUE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
            
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            
                UNIQUE (document_type, version),
            
                CHECK (
                    retired_at IS NULL
                    OR retired_at >= effective_at
                )
            );

            
            CREATE TABLE IF NOT EXISTS client_consents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            
                client_id UUID NOT NULL
                    REFERENCES advisory_clients(id)
                    ON DELETE RESTRICT,
            
                document_id UUID NOT NULL
                    REFERENCES advisory_documents(id)
                    ON DELETE RESTRICT,
            
                consent_type TEXT NOT NULL
                    CHECK (
                        consent_type IN (
                            'accepted',
                            'declined',
                            'withdrawn'
                        )
                    ),
            
                signed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            
                ip_address INET,
                user_agent TEXT,
            
                signature_method TEXT NOT NULL DEFAULT 'electronic'
                    CHECK (
                        signature_method IN (
                            'electronic',
                            'wet_signature',
                            'advisor_recorded'
                        )
                    ),
            
                signature_reference TEXT,
            
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()

            );

                        
            CREATE INDEX IF NOT EXISTS idx_client_consents_client_document
            ON client_consents(client_id, document_id);

            CREATE TABLE IF NOT EXISTS client_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            
                client_id UUID NOT NULL
                    REFERENCES advisory_clients(id)
                    ON DELETE RESTRICT,
            
                event_type TEXT NOT NULL,
            
                actor_type TEXT NOT NULL DEFAULT 'system'
                    CHECK (
                        actor_type IN (
                            'client',
                            'advisor',
                            'system',
                            'schwab',
                            'email_provider'
                        )
                    ),
            
                actor_id TEXT,
                event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            
                correlation_id UUID,
                request_id TEXT
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

                CREATE INDEX IF NOT EXISTS idx_client_consents_client
                ON client_consents(client_id);
                
                CREATE INDEX IF NOT EXISTS idx_client_consents_document
                ON client_consents(document_id);
                
                CREATE INDEX IF NOT EXISTS idx_client_consents_signed
                ON client_consents(signed_at DESC);

                
                CREATE INDEX IF NOT EXISTS idx_recommendation_findings_run
                ON recommendation_findings(run_id);
                
                CREATE INDEX IF NOT EXISTS idx_recommendation_findings_user
                ON recommendation_findings (
                    parity_user_id,
                    generated_at DESC
                );

                CREATE INDEX IF NOT EXISTS idx_advisory_documents_type_active
                ON advisory_documents(document_type, is_active);
                
                CREATE INDEX IF NOT EXISTS idx_advisory_documents_required
                ON advisory_documents(required_for_activation, is_active);
                
                CREATE INDEX IF NOT EXISTS idx_advisory_documents_effective
                ON advisory_documents(effective_at DESC);
                
                CREATE INDEX IF NOT EXISTS idx_advisory_clients_status
                ON advisory_clients(status, created_at DESC);
                
                CREATE INDEX IF NOT EXISTS idx_advisory_clients_user
                ON advisory_clients(parity_user_id);
                
                CREATE INDEX IF NOT EXISTS idx_client_events_client_time
                ON client_events(client_id, occurred_at DESC);
                
                CREATE INDEX IF NOT EXISTS idx_client_events_type
                ON client_events(event_type, occurred_at DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS uq_advisory_documents_one_active_type
                ON advisory_documents(document_type)
                WHERE is_active = TRUE;

                CREATE OR REPLACE FUNCTION set_updated_at()
                RETURNS TRIGGER AS $$
                BEGIN
                    NEW.updated_at = NOW();
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                
                DROP TRIGGER IF EXISTS trg_advisory_clients_updated_at
                ON advisory_clients;
                
                CREATE TRIGGER trg_advisory_clients_updated_at
                BEFORE UPDATE ON advisory_clients
                FOR EACH ROW
                EXECUTE FUNCTION set_updated_at();

                
                DROP TRIGGER IF EXISTS trg_advisory_documents_updated_at
                ON advisory_documents;
                
                CREATE TRIGGER trg_advisory_documents_updated_at
                BEFORE UPDATE ON advisory_documents
                FOR EACH ROW
                EXECUTE FUNCTION set_updated_at();

                CREATE TABLE IF NOT EXISTS investor_profiles  (
                    parity_user_id TEXT PRIMARY KEY
                        REFERENCES parity_users(id)
                        ON DELETE CASCADE,
                
                    -- Personal information
                    first_name TEXT,
                    last_name TEXT,
                    phone TEXT,
                    date_of_birth DATE,
                
                    -- Address
                    address_line1 TEXT,
                    city TEXT,
                    state CHAR(2),
                    zip TEXT,
                
                    -- Suitability
                    investment_objective TEXT,
                    risk_tolerance TEXT,
                    time_horizon TEXT,
                
                    annual_income TEXT,
                    net_worth TEXT,
                    investable_assets TEXT,
                
                    options_experience TEXT,
                    liquidity_needs TEXT,
                
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


                CREATE TABLE IF NOT EXISTS public_scorecard_cache (
                cache_key TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                market_data_timestamp TIMESTAMPTZ NOT NULL,
                generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

                CREATE TABLE IF NOT EXISTS prepared_option_orders (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                parity_user_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                brokerage_slug TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
            
                status TEXT NOT NULL DEFAULT 'PREPARED'
                    CHECK (
                        status IN (
                            'PREPARED',
                            'SUBMITTED',
                            'EXPIRED',
                            'CANCELED',
                            'SUBMISSION_FAILED'
                        )
                    ),
            
                order_payload JSONB NOT NULL,
                quote_snapshot JSONB,
                client_order_id UUID NOT NULL DEFAULT gen_random_uuid(),
            
                prepared_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                submitted_at TIMESTAMPTZ,
            
                snaptrade_brokerage_order_id TEXT,
                submission_response JSONB
            );
            
            CREATE INDEX IF NOT EXISTS idx_prepared_option_orders_user_status
            ON prepared_option_orders (
                parity_user_id,
                status,
                prepared_at DESC
            );


            CREATE TABLE IF NOT EXISTS execution_workflows (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            
                parity_user_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                brokerage_slug TEXT NOT NULL,
            
                strategy_type TEXT NOT NULL
                    CHECK (
                        strategy_type IN (
                            'covered_call',
                            'married_put',
                            'collar',
                            'buffer'
                        )
                    ),
            
                underlying_source TEXT NOT NULL
                    CHECK (
                        underlying_source IN (
                            'existing',
                            'new'
                        )
                    ),
            
                underlying_symbol TEXT NOT NULL,
                underlying_shares INTEGER NOT NULL
                    CHECK (
                        underlying_shares > 0
                        AND underlying_shares % 100 = 0
                    ),
                option_contracts INTEGER NOT NULL
                    CHECK (option_contracts > 0),
            
                status TEXT NOT NULL DEFAULT 'DRAFT'
                    CHECK (
                        status IN (
                            'DRAFT',
                            'UNDERLYING_ORDER_PREPARED',
                            'UNDERLYING_SUBMITTED',
                            'AWAITING_UNDERLYING_FILL',
                            'OPTIONS_ORDER_PREPARED',
                            'OPTIONS_SUBMITTED',
                            'COMPLETE',
                            'FAILED',
                            'CANCELED'
                        )
                    ),
            
                underlying_prepared_order_id UUID
                    REFERENCES prepared_option_orders(id)
                    ON DELETE SET NULL,
            
                options_prepared_order_id UUID
                    REFERENCES prepared_option_orders(id)
                    ON DELETE SET NULL,
            
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            
            CREATE INDEX IF NOT EXISTS idx_execution_workflows_user_status
            ON execution_workflows (
                parity_user_id,
                status,
                created_at DESC
            );

            CREATE TABLE IF NOT EXISTS execution_workflow_lots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        
            workflow_id UUID NOT NULL
                REFERENCES execution_workflows(id)
                ON DELETE CASCADE,
        
            lot_number INTEGER NOT NULL
                CHECK (lot_number > 0),
        
            share_quantity INTEGER NOT NULL DEFAULT 100
                CHECK (share_quantity = 100),
        
            reserved_share_quantity INTEGER NOT NULL DEFAULT 0
                CHECK (
                    reserved_share_quantity >= 0
                    AND reserved_share_quantity <= 100
                ),
        
            status TEXT NOT NULL DEFAULT 'UNSTARTED'
                CHECK (
                    status IN (
                        'UNSTARTED',
                        'ORDER_WORKING',
                        'PARTIALLY_FILLED',
                        'WAITING_FOR_NEXT_STEP',
                        'NEXT_STEP_WORKING',
                        'REQUOTE_REQUIRED',
                        'ACTION_REQUIRED',
                        'EXCEPTION_RECONCILIATION',
                        'COMPLETE',
                        'CANCELED'
                    )
                ),
        
            committed_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
        
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        
            UNIQUE (workflow_id, lot_number)
        );
        
        CREATE INDEX IF NOT EXISTS idx_execution_workflow_lots_workflow_status
        ON execution_workflow_lots (
            workflow_id,
            status,
            lot_number
        );



            
            """)
            cur.execute("""
                ALTER TABLE execution_workflows
                DROP CONSTRAINT IF EXISTS
                    execution_workflows_strategy_type_check;

                ALTER TABLE execution_workflows
                ADD CONSTRAINT
                    execution_workflows_strategy_type_check
                CHECK (
                    strategy_type IN (
                        'covered_call',
                        'married_put',
                        'collar',
                        'buffer'
                    )
                );
            """)
            cur.execute("""
                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS execution_plan JSONB;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS execution_preference TEXT;
            """)




            cur.execute("""
                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    approved_option_contracts JSONB;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    approved_option_limit_price NUMERIC;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    approved_option_price_effect TEXT;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    approved_option_time_in_force TEXT;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    approved_option_quote_snapshot JSONB;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    approved_at TIMESTAMPTZ;

                ALTER TABLE execution_workflows
                DROP CONSTRAINT IF EXISTS
                    execution_workflows_status_check;

                ALTER TABLE execution_workflows
                ADD CONSTRAINT
                    execution_workflows_status_check
                CHECK (
                    status IN (
                        'DRAFT',
                        'UNDERLYING_ORDER_PREPARED',
                        'UNDERLYING_SUBMITTED',
                        'AWAITING_UNDERLYING_FILL',
                        'OPTIONS_ORDER_PREPARED',
                        'OPTIONS_SUBMITTED',
                        'APPROVED_PENDING_UNDERLYING_FILL',
                        'PENDING_OPTIONS_SUBMISSION',
                        'OPTIONS_SUBMITTING',
                        'ACTION_REQUIRED',
                        'COMPLETE',
                        'FAILED',
                        'CANCELED'
                    )
                
                );

                ALTER TABLE execution_workflows
                DROP CONSTRAINT IF EXISTS
                    execution_workflows_approved_option_price_effect_check;

                ALTER TABLE execution_workflows
                ADD CONSTRAINT
                    execution_workflows_approved_option_price_effect_check
                CHECK (
                    approved_option_price_effect IS NULL
                    OR approved_option_price_effect IN (
                        'DEBIT',
                        'CREDIT',
                        'EVEN'
                    )
                );
                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    attention_resolved_at TIMESTAMPTZ;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    attention_resolution_code TEXT;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    attention_resolution_note TEXT;

                ALTER TABLE execution_workflows
                DROP CONSTRAINT IF EXISTS
                    execution_workflows_attention_resolution_code_check;

                ALTER TABLE execution_workflows
                ADD CONSTRAINT
                    execution_workflows_attention_resolution_code_check
                CHECK (
                    attention_resolution_code IS NULL
                    OR attention_resolution_code IN (
                        'NO_ACTIVE_BROKER_ORDER',
                        'POSITION_NO_LONGER_HELD',
                        'POSITION_REVIEWED_UNPROTECTED',
                        'DUPLICATE_OR_TEST_WORKFLOW'
                    )
                );
            """)



            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS execution_orders (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

                    workflow_id UUID NOT NULL
                        REFERENCES execution_workflows(id)
                        ON DELETE CASCADE,

                    lot_id UUID NOT NULL
                        REFERENCES execution_workflow_lots(id)
                        ON DELETE CASCADE,

                    parity_user_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    brokerage_slug TEXT NOT NULL,

                    sequence INTEGER NOT NULL
                        CHECK (sequence > 0),

                    order_role TEXT NOT NULL
                        CHECK (
                            order_role IN (
                                'BUY_UNDERLYING',
                                'BUY_PROTECTIVE_PUT',
                                'BUY_HIGHER_STRIKE_PUT',
                                'SELL_LOWER_STRIKE_PUT',
                                'SELL_COVERED_CALL',
                                'COLLAR_OPTIONS_PACKAGE',
                                'BUFFER_OPTIONS_PACKAGE',
                                'BUY_PUT_SPREAD_PACKAGE',
                                'CLOSE_OPTIONS_OVERLAY',
                                'SELL_UNDERLYING'
                                
                                
                            )
                        ),

                    order_scope TEXT NOT NULL
                        CHECK (
                            order_scope IN (
                                'EQUITY',
                                'OPTIONS',
                                'OPTIONS_PACKAGE'
                            )
                        ),

                    execution_phase TEXT NOT NULL DEFAULT 'INITIAL'
                        CHECK (
                            execution_phase IN (
                                'INITIAL',
                                'CONTINUATION',
                                'RECONCILIATION',
                                'REQUOTE',
                                'CLOSE_OPTIONS'
                            )
                        ),

                    status TEXT NOT NULL DEFAULT 'DRAFT'
                        CHECK (
                            status IN (
                                'DRAFT',
                                'PREPARED',
                                'SUBMITTING',
                                'SUBMITTED',
                                'WORKING',
                                'PARTIALLY_FILLED',
                                'FILLED',
                                'CANCELED',
                                'EXPIRED',
                                'REJECTED',
                                'FAILED',
                                'REQUOTE_REQUIRED',
                                'ACTION_REQUIRED'
                            )
                        ),

                    requested_quantity NUMERIC NOT NULL
                        CHECK (requested_quantity > 0),

                    filled_quantity NUMERIC NOT NULL DEFAULT 0
                        CHECK (filled_quantity >= 0),

                    limit_price NUMERIC,
                    price_effect TEXT
                        CHECK (
                            price_effect IN (
                                'DEBIT',
                                'CREDIT',
                                'EVEN'
                            )
                        ),

                    average_fill_price NUMERIC,

                    broker_order_id TEXT,
                    client_order_id UUID NOT NULL
                        DEFAULT gen_random_uuid(),

                    order_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    quote_snapshot JSONB,
                    broker_response JSONB,
                    rejection_reason TEXT,

                    submitted_at TIMESTAMPTZ,
                    last_checked_at TIMESTAMPTZ,
                    filled_at TIMESTAMPTZ,
                    canceled_at TIMESTAMPTZ,

                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                    UNIQUE (workflow_id, lot_id, sequence, order_role,
                            execution_phase, client_order_id)
                );

                CREATE INDEX IF NOT EXISTS
                    idx_execution_orders_workflow_lot_status
                ON execution_orders (
                    workflow_id,
                    lot_id,
                    status,
                    sequence
                );

                CREATE INDEX IF NOT EXISTS
                    idx_execution_orders_broker_order
                ON execution_orders (
                    brokerage_slug,
                    broker_order_id
                )
                WHERE broker_order_id IS NOT NULL;
            """)
            cur.execute("""
                ALTER TABLE execution_orders
                DROP CONSTRAINT IF EXISTS
                    execution_orders_status_check;

                ALTER TABLE execution_orders
                ADD CONSTRAINT execution_orders_status_check
                CHECK (
                    status IN (
                        'DRAFT',
                        'PREPARED',
                        'SUBMITTING',
                        'SUBMITTED',
                        'WORKING',
                        'PARTIALLY_FILLED',
                        'CANCELING',
                        'FILLED',
                        'CANCELED',
                        'EXPIRED',
                        'REJECTED',
                        'FAILED',
                        'REQUOTE_REQUIRED',
                        'ACTION_REQUIRED'
                    )
                );
                ALTER TABLE execution_orders
                DROP CONSTRAINT IF EXISTS
                    execution_orders_order_role_check;

                ALTER TABLE execution_orders
                ADD CONSTRAINT
                    execution_orders_order_role_check
                CHECK (
                    order_role IN (
                        'BUY_UNDERLYING',
                        'BUY_PROTECTIVE_PUT',
                        'BUY_HIGHER_STRIKE_PUT',
                        'SELL_LOWER_STRIKE_PUT',
                        'SELL_COVERED_CALL',
                        'COLLAR_OPTIONS_PACKAGE',
                        'BUFFER_OPTIONS_PACKAGE',
                        'BUY_PUT_SPREAD_PACKAGE',
                        'CLOSE_OPTIONS_OVERLAY',
                        'SELL_UNDERLYING'
                    )
                );

                ALTER TABLE execution_orders
                DROP CONSTRAINT IF EXISTS
                    execution_orders_execution_phase_check;

                ALTER TABLE execution_orders
                ADD CONSTRAINT
                    execution_orders_execution_phase_check
                CHECK (
                    execution_phase IN (
                        'INITIAL',
                        'CONTINUATION',
                        'RECONCILIATION',
                        'REQUOTE',
                        'CLOSE_OPTIONS'
                    )
                );
            """)
            cur.execute("""
                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    unwind_requested_at TIMESTAMPTZ;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    unwind_status TEXT;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    unwind_final_share_quantity INTEGER;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    unwind_sell_order_id UUID
                    REFERENCES execution_orders(id)
                    ON DELETE SET NULL;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    unwind_error TEXT;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    unwind_completed_at TIMESTAMPTZ;

                ALTER TABLE execution_workflows
                ADD COLUMN IF NOT EXISTS
                    unwind_broker_snapshot JSONB
                    NOT NULL DEFAULT '{}'::jsonb;

                ALTER TABLE execution_workflows
                DROP CONSTRAINT IF EXISTS
                    execution_workflows_unwind_status_check;

                ALTER TABLE execution_workflows
                ADD CONSTRAINT
                    execution_workflows_unwind_status_check
                CHECK (
                    unwind_status IS NULL
                    OR unwind_status IN (
                        'REQUESTED',
                        'CANCELING_ORDERS',
                        'READY_TO_SELL',
                        'SELL_SUBMITTED',
                        'COMPLETE',
                        'ACTION_REQUIRED'
                    )
                );

                ALTER TABLE execution_workflows
                DROP CONSTRAINT IF EXISTS
                    execution_workflows_unwind_share_quantity_check;

                ALTER TABLE execution_workflows
                ADD CONSTRAINT
                    execution_workflows_unwind_share_quantity_check
                CHECK (
                    unwind_final_share_quantity IS NULL
                    OR unwind_final_share_quantity >= 0
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS protected_position_lots (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

                    parity_user_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    brokerage_slug TEXT NOT NULL,

                    opening_workflow_id UUID NOT NULL
                        REFERENCES execution_workflows(id)
                        ON DELETE RESTRICT,

                    opening_workflow_lot_id UUID NOT NULL UNIQUE
                        REFERENCES execution_workflow_lots(id)
                        ON DELETE RESTRICT,

                    underlying_symbol TEXT NOT NULL,

                    share_quantity INTEGER NOT NULL DEFAULT 100
                        CHECK (share_quantity = 100),

                    share_source TEXT NOT NULL
                        CHECK (
                            share_source IN (
                                'EXISTING_HOLDING',
                                'PARITY_NEW_POSITION'
                            )
                        ),

                    share_entry_fill_price NUMERIC,
                    share_entry_filled_at TIMESTAMPTZ,

                    strategy_type TEXT NOT NULL
                        CHECK (
                            strategy_type IN (
                                'covered_call',
                                'married_put',
                                'collar',
                                'buffer'
                            )
                        ),

                    option_contracts JSONB NOT NULL,

                    options_open_order_id UUID
                        REFERENCES execution_orders(id)
                        ON DELETE SET NULL,

                    protection_opened_at TIMESTAMPTZ NOT NULL,

                    status TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (
                            status IN (
                                'ACTIVE',
                                'RECONCILIATION_REQUIRED',
                                'CLOSED'
                            )
                        ),

                    latest_reconciled_at TIMESTAMPTZ,
                    closed_at TIMESTAMPTZ,

                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS
                    idx_protected_position_lots_user_account_status
                ON protected_position_lots (
                    parity_user_id,
                    account_id,
                    status,
                    created_at DESC
                );
            """)
            cur.execute("""
                ALTER TABLE protected_position_lots
                ADD COLUMN IF NOT EXISTS
                    underlying_reference_price NUMERIC;

                ALTER TABLE protected_position_lots
                ADD COLUMN IF NOT EXISTS
                    underlying_reference_at TIMESTAMPTZ;

                ALTER TABLE protected_position_lots
                ADD COLUMN IF NOT EXISTS
                    option_entry_net_price NUMERIC;

                ALTER TABLE protected_position_lots
                ADD COLUMN IF NOT EXISTS
                    option_entry_price_effect TEXT;

                ALTER TABLE protected_position_lots
                ADD COLUMN IF NOT EXISTS
                    entry_strategy_value NUMERIC;

                ALTER TABLE protected_position_lots
                ADD COLUMN IF NOT EXISTS
                    entry_outcome_snapshot JSONB
                    NOT NULL DEFAULT '{}'::jsonb;

                ALTER TABLE protected_position_lots
                DROP CONSTRAINT IF EXISTS
                    protected_position_lots_entry_price_effect_check;

                ALTER TABLE protected_position_lots
                ADD CONSTRAINT
                    protected_position_lots_entry_price_effect_check
                CHECK (
                    option_entry_price_effect IS NULL
                    OR option_entry_price_effect IN (
                        'DEBIT',
                        'CREDIT',
                        'EVEN'
                    )
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS protected_position_marks (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

                    protected_lot_id UUID NOT NULL
                        REFERENCES protected_position_lots(id)
                        ON DELETE CASCADE,

                    parity_user_id TEXT NOT NULL,

                    marked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                    underlying_price NUMERIC NOT NULL,
                    underlying_market_value NUMERIC NOT NULL,

                    option_market_value NUMERIC NOT NULL,
                    strategy_market_value NUMERIC NOT NULL,

                    pnl_dollars NUMERIC NOT NULL,
                    pnl_percent NUMERIC NOT NULL,

                    quote_source TEXT NOT NULL,

                    quote_snapshot JSONB
                        NOT NULL DEFAULT '{}'::jsonb,

                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS
                    idx_protected_position_marks_lot_time
                ON protected_position_marks (
                    protected_lot_id,
                    marked_at DESC
                );

                CREATE INDEX IF NOT EXISTS
                    idx_protected_position_marks_user_time
                ON protected_position_marks (
                    parity_user_id,
                    marked_at DESC
                );
            """)
            cur.execute("""
                ALTER TABLE execution_orders
                DROP CONSTRAINT IF EXISTS
                    execution_orders_order_role_check;

                ALTER TABLE execution_orders
                ADD CONSTRAINT
                    execution_orders_order_role_check
                CHECK (
                    order_role IN (
                        'BUY_UNDERLYING',
                        'SELL_UNDERLYING',
                        'BUY_PROTECTIVE_PUT',
                        'BUY_HIGHER_STRIKE_PUT',
                        'SELL_LOWER_STRIKE_PUT',
                        'SELL_COVERED_CALL',
                        'COLLAR_OPTIONS_PACKAGE',
                        'BUFFER_OPTIONS_PACKAGE',
                        'BUY_PUT_SPREAD_PACKAGE',
                        'CLOSE_OPTIONS_OVERLAY'
                    )
                );

                ALTER TABLE execution_orders
                DROP CONSTRAINT IF EXISTS
                    execution_orders_execution_phase_check;

                ALTER TABLE execution_orders
                ADD CONSTRAINT
                    execution_orders_execution_phase_check
                CHECK (
                    execution_phase IN (
                        'INITIAL',
                        'CONTINUATION',
                        'RECONCILIATION',
                        'REQUOTE',
                        'CLOSE_OPTIONS',
                        'CLOSE_EQUITY'
                    )
                );

                ALTER TABLE protected_position_lots
                DROP CONSTRAINT IF EXISTS
                    protected_position_lots_status_check;

                ALTER TABLE protected_position_lots
                ADD CONSTRAINT
                    protected_position_lots_status_check
                CHECK (
                    status IN (
                        'ACTIVE',
                        'EXITING',
                        'RECONCILIATION_REQUIRED',
                        'CLOSED'
                    )
                );

                CREATE TABLE IF NOT EXISTS protected_position_exits (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

                    protected_lot_id UUID NOT NULL
                        REFERENCES protected_position_lots(id)
                        ON DELETE RESTRICT,

                    parity_user_id TEXT NOT NULL,

                    exit_mode TEXT NOT NULL
                        CHECK (
                            exit_mode IN (
                                'REMOVE_PROTECTION',
                                'SELL_PROTECTED_POSITION'
                            )
                        ),

                    status TEXT NOT NULL DEFAULT 'APPROVED'
                        CHECK (
                            status IN (
                                'APPROVED',
                                'OPTIONS_CLOSE_SUBMITTED',
                                'AWAITING_OPTIONS_FILL',
                                'EQUITY_SALE_SUBMITTED',
                                'COMPLETE',
                                'ACTION_REQUIRED',
                                'CANCELED'
                            )
                        ),

                    approved_share_quantity INTEGER NOT NULL DEFAULT 100
                        CHECK (approved_share_quantity = 100),

                    options_close_order_id UUID
                        REFERENCES execution_orders(id)
                        ON DELETE SET NULL,

                    equity_sale_order_id UUID
                        REFERENCES execution_orders(id)
                        ON DELETE SET NULL,

                    approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_protected_position_exits_one_active_exit
                ON protected_position_exits (protected_lot_id)
                WHERE status NOT IN ('COMPLETE', 'CANCELED');

                CREATE INDEX IF NOT EXISTS
                    idx_protected_position_exits_user_status
                ON protected_position_exits (
                    parity_user_id,
                    status,
                    created_at DESC
                );
            """)
            cur.execute("""
                ALTER TABLE execution_workflow_lots
                DROP CONSTRAINT IF EXISTS
                    execution_workflow_lots_share_quantity_check;

                ALTER TABLE execution_workflow_lots
                ADD CONSTRAINT
                    execution_workflow_lots_share_quantity_check
                CHECK (
                    share_quantity > 0
                    AND share_quantity % 100 = 0
                );

                ALTER TABLE execution_workflow_lots
                DROP CONSTRAINT IF EXISTS
                    execution_workflow_lots_reserved_share_quantity_check;

                ALTER TABLE execution_workflow_lots
                ADD CONSTRAINT
                    execution_workflow_lots_reserved_share_quantity_check
                CHECK (
                    reserved_share_quantity >= 0
                    AND reserved_share_quantity <= share_quantity
                );

                ALTER TABLE protected_position_lots
                DROP CONSTRAINT IF EXISTS
                    protected_position_lots_share_quantity_check;

                ALTER TABLE protected_position_lots
                ADD CONSTRAINT
                    protected_position_lots_share_quantity_check
                CHECK (
                    share_quantity > 0
                    AND share_quantity % 100 = 0
                );

                ALTER TABLE protected_position_exits
                DROP CONSTRAINT IF EXISTS
                    protected_position_exits_approved_share_quantity_check;

                ALTER TABLE protected_position_exits
                ADD CONSTRAINT
                    protected_position_exits_approved_share_quantity_check
                CHECK (
                    approved_share_quantity > 0
                    AND approved_share_quantity % 100 = 0
                );
            """)




            cur.execute("""
                ALTER TABLE protected_position_marks
                ADD COLUMN IF NOT EXISTS
                    mark_type TEXT NOT NULL DEFAULT 'MANUAL';
            
                ALTER TABLE protected_position_marks
                ADD COLUMN IF NOT EXISTS
                    market_date DATE;
            
                ALTER TABLE protected_position_marks
                DROP CONSTRAINT IF EXISTS
                    protected_position_marks_mark_type_check;
            
                ALTER TABLE protected_position_marks
                ADD CONSTRAINT
                    protected_position_marks_mark_type_check
                CHECK (
                    mark_type IN (
                        'MANUAL',
                        'DAILY_CLOSE'
                    )
                );
            
                ALTER TABLE protected_position_marks
                DROP CONSTRAINT IF EXISTS
                    protected_position_marks_daily_date_check;
            
                ALTER TABLE protected_position_marks
                ADD CONSTRAINT
                    protected_position_marks_daily_date_check
                CHECK (
                    mark_type = 'MANUAL'
                    OR market_date IS NOT NULL
                );
            
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_protected_position_marks_one_daily_close
                ON protected_position_marks (
                    protected_lot_id,
                    market_date
                )
                WHERE mark_type = 'DAILY_CLOSE';
            """)



            
            cur.execute("""
                ALTER TABLE execution_orders
                ADD COLUMN IF NOT EXISTS
                broker_status_checked_at TIMESTAMPTZ;
                """)
            cur.execute("""
                ALTER TABLE execution_orders
                ADD COLUMN IF NOT EXISTS
                    replaces_order_id UUID
                    REFERENCES execution_orders(id)
                    ON DELETE RESTRICT;

                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_execution_orders_one_replacement
                ON execution_orders (replaces_order_id)
                WHERE replaces_order_id IS NOT NULL;

                CREATE INDEX IF NOT EXISTS
                    idx_execution_orders_replacement_lookup
                ON execution_orders (
                    parity_user_id,
                    replaces_order_id,
                    status
                )
                WHERE replaces_order_id IS NOT NULL;
            """)
            conn.commit()

from typing import Any


def save_public_scorecard_snapshot(
    cache_key: str,
    payload: dict[str, Any],
    market_data_timestamp: datetime,
) -> dict[str, Any]:
    """
    Insert or replace one complete public homepage scorecard snapshot.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public_scorecard_cache (
                    cache_key,
                    payload,
                    market_data_timestamp,
                    generated_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s::jsonb,
                    %s,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (cache_key)
                DO UPDATE SET
                    payload = EXCLUDED.payload,
                    market_data_timestamp = EXCLUDED.market_data_timestamp,
                    generated_at = NOW(),
                    updated_at = NOW()
                RETURNING
                    cache_key,
                    payload,
                    market_data_timestamp,
                    generated_at,
                    updated_at

                """,
                (
                    cache_key,
                    json.dumps(payload),
                    market_data_timestamp,
                ),
            )

            saved_snapshot = cur.fetchone()
            conn.commit()

    return dict(saved_snapshot)

def get_public_scorecard_snapshot(
    cache_key: str = "homepage_v1",
) -> dict[str, Any] | None:
    """
    Return the current cached homepage scorecard snapshot without
    calling ORATS or recalculating outcomes.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cache_key,
                    payload,
                    market_data_timestamp,
                    generated_at,
                    updated_at
                FROM public_scorecard_cache
                WHERE cache_key = %s
                """,
                (cache_key,),
            )

            snapshot = cur.fetchone()

    if snapshot is None:
        return None

    return dict(snapshot)


def create_advisory_client(
    parity_user_id: str,
    email: str,
    legal_first_name: str | None = None,
    legal_middle_name: str | None = None,
    legal_last_name: str | None = None,
    preferred_name: str | None = None,
    phone: str | None = None,
    onboarding_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Creates an advisory client and records CLIENT_CREATED.

    This function is idempotent: if the client already exists,
    it returns the existing record without creating a duplicate.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Confirm the authenticated Parity user exists.
            cur.execute(
                """
                SELECT id
                FROM parity_users
                WHERE id = %s
                """,
                (parity_user_id,),
            )

            if not cur.fetchone():
                raise ValueError(
                    "Parity user must exist before advisory onboarding"
                )

            # Return the existing client if onboarding already started.
            cur.execute(
                """
                SELECT *
                FROM advisory_clients
                WHERE parity_user_id = %s
                FOR UPDATE
                """,
                (parity_user_id,),
            )

            existing_client = cur.fetchone()

            if existing_client:
                return existing_client

            cur.execute(
                """
                INSERT INTO advisory_clients (
                    parity_user_id,
                    status,
                    legal_first_name,
                    legal_middle_name,
                    legal_last_name,
                    preferred_name,
                    email,
                    phone,
                    onboarding_payload,
                    onboarding_started_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    'onboarding',
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    NOW(),
                    NOW(),
                    NOW()
                )
                RETURNING *
                """,
                (
                    parity_user_id,
                    legal_first_name,
                    legal_middle_name,
                    legal_last_name,
                    preferred_name,
                    email,
                    phone,
                    json.dumps(onboarding_payload or {}),
                ),
            )

            client = cur.fetchone()

            if not client:
                raise RuntimeError(
                    "Advisory client could not be created"
                )

            cur.execute(
                """
                INSERT INTO client_events (
                    client_id,
                    event_type,
                    actor_type,
                    actor_id,
                    event_data,
                    occurred_at
                )
                VALUES (
                    %s,
                    'CLIENT_CREATED',
                    'client',
                    %s,
                    %s::jsonb,
                    NOW()
                )
                """,
                (
                    client["id"],
                    parity_user_id,
                    json.dumps(
                        {
                            "initial_status": "onboarding",
                            "source": "parity_app",
                        }
                    ),
                ),
            )

            conn.commit()

            return client
            
from typing import Any

def get_investor_profile(
    parity_user_id: str,
) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    parity_user_id,
                    first_name,
                    last_name,
                    phone,
                    date_of_birth,
                    address_line1,
                    city,
                    state,
                    zip,
                    investment_objective,
                    risk_tolerance,
                    time_horizon,
                    annual_income,
                    net_worth,
                    investable_assets,
                    options_experience,
                    liquidity_needs,
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

            profile = cur.fetchone()

            return profile if profile else None


import json
from datetime import date
from typing import Any


def upsert_investor_profile(
    parity_user_id: str,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
    date_of_birth: date | str | None = None,
    address_line1: str | None = None,
    city: str | None = None,
    state: str | None = None,
    zip: str | None = None,
    investment_objective: str | None = None,
    risk_tolerance: str | None = None,
    time_horizon: str | None = None,
    annual_income: str | None = None,
    net_worth: str | None = None,
    investable_assets: str | None = None,
    options_experience: str | None = None,
    liquidity_needs: str | None = None,
    completed: bool = False,
    raw: dict | None = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Ensure the parent user exists before inserting the profile.
            cur.execute(
                """
                INSERT INTO parity_users (
                    id,
                    created_at,
                    last_login_at
                )
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
                    first_name,
                    last_name,
                    phone,
                    date_of_birth,
                    address_line1,
                    city,
                    state,
                    zip,
                    investment_objective,
                    risk_tolerance,
                    time_horizon,
                    annual_income,
                    net_worth,
                    investable_assets,
                    options_experience,
                    liquidity_needs,
                    completed,
                    completed_at,
                    raw_json,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
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
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    phone = EXCLUDED.phone,
                    date_of_birth = EXCLUDED.date_of_birth,
                    address_line1 = EXCLUDED.address_line1,
                    city = EXCLUDED.city,
                    state = EXCLUDED.state,
                    zip = EXCLUDED.zip,
                    investment_objective =
                        EXCLUDED.investment_objective,
                    risk_tolerance = EXCLUDED.risk_tolerance,
                    time_horizon = EXCLUDED.time_horizon,
                    annual_income = EXCLUDED.annual_income,
                    net_worth = EXCLUDED.net_worth,
                    investable_assets =
                        EXCLUDED.investable_assets,
                    options_experience =
                        EXCLUDED.options_experience,
                    liquidity_needs = EXCLUDED.liquidity_needs,
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
                    first_name,
                    last_name,
                    phone,
                    date_of_birth,
                    address_line1,
                    city,
                    state,
                    zip,
                    investment_objective,
                    risk_tolerance,
                    time_horizon,
                    annual_income,
                    net_worth,
                    investable_assets,
                    options_experience,
                    liquidity_needs,
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
import json
from typing import Any


PROFILE_FIELDS = (
    "first_name",
    "last_name",
    "phone",
    "date_of_birth",
    "address_line1",
    "city",
    "state",
    "zip",
    "investment_objective",
    "risk_tolerance",
    "time_horizon",
    "annual_income",
    "net_worth",
    "investable_assets",
    "options_experience",
    "liquidity_needs",
    "completed",
)


def save_investor_profile_and_invalidate_recommendations(
    parity_user_id: str,
    first_name: str,
    last_name: str,
    phone: str,
    date_of_birth,
    address_line1: str,
    city: str,
    state: str,
    zip: str,
    investment_objective: str,
    risk_tolerance: str,
    time_horizon: str,
    annual_income: str,
    net_worth: str,
    investable_assets: str,
    options_experience: str,
    liquidity_needs: str,
    completed: bool = True,
    raw: dict | None = None,
) -> dict[str, Any]:
    """
    Save the user's current investor profile.

    If any recommendation-relevant profile field changes, existing
    portfolio recommendations for the user are deleted in the same
    transaction so the frontend can regenerate them.
    """

    new_profile_values = {
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "date_of_birth": date_of_birth,
        "address_line1": address_line1,
        "city": city,
        "state": state,
        "zip": zip,
        "investment_objective": investment_objective,
        "risk_tolerance": risk_tolerance,
        "time_horizon": time_horizon,
        "annual_income": annual_income,
        "net_worth": net_worth,
        "investable_assets": investable_assets,
        "options_experience": options_experience,
        "liquidity_needs": liquidity_needs,
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

            # Lock the existing profile row for this transaction.
            cur.execute(
                """
                SELECT
                    first_name,
                    last_name,
                    phone,
                    date_of_birth,
                    address_line1,
                    city,
                    state,
                    zip,
                    investment_objective,
                    risk_tolerance,
                    time_horizon,
                    annual_income,
                    net_worth,
                    investable_assets,
                    options_experience,
                    liquidity_needs,
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
                    first_name,
                    last_name,
                    phone,
                    date_of_birth,
                    address_line1,
                    city,
                    state,
                    zip,
                    investment_objective,
                    risk_tolerance,
                    time_horizon,
                    annual_income,
                    net_worth,
                    investable_assets,
                    options_experience,
                    liquidity_needs,
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
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    phone = EXCLUDED.phone,
                    date_of_birth = EXCLUDED.date_of_birth,
                    address_line1 = EXCLUDED.address_line1,
                    city = EXCLUDED.city,
                    state = EXCLUDED.state,
                    zip = EXCLUDED.zip,
                    investment_objective =
                        EXCLUDED.investment_objective,
                    risk_tolerance = EXCLUDED.risk_tolerance,
                    time_horizon = EXCLUDED.time_horizon,
                    annual_income = EXCLUDED.annual_income,
                    net_worth = EXCLUDED.net_worth,
                    investable_assets = EXCLUDED.investable_assets,
                    options_experience =
                        EXCLUDED.options_experience,
                    liquidity_needs = EXCLUDED.liquidity_needs,
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
                    first_name,
                    last_name,
                    phone,
                    date_of_birth,
                    address_line1,
                    city,
                    state,
                    zip,
                    investment_objective,
                    risk_tolerance,
                    time_horizon,
                    annual_income,
                    net_worth,
                    investable_assets,
                    options_experience,
                    liquidity_needs,
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
from typing import Any
import json

def get_advisory_status(parity_user_id: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    status,
                    agreement_completed_at
                FROM advisory_clients
                WHERE parity_user_id = %s
                """,
                (parity_user_id,),
            )

            client = cur.fetchone()

            if not client:
                return {
                    "exists": False,
                    "status": "not_started",
                    "documents_complete": False,
                    "next_step": "create_client",
                }

            cur.execute(
                """
                SELECT COUNT(*)
                FROM advisory_documents d
                WHERE d.required_for_activation = TRUE
                  AND d.is_active = TRUE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM client_consents c
                      WHERE c.client_id = %s
                        AND c.document_id = d.id
                        AND c.consent_type = 'accepted'
                  )
                """,
                (client["id"],),
            )

            remaining = cur.fetchone()["count"]

            documents_complete = remaining == 0

            if not documents_complete:
                next_step = "documents"
            elif client["agreement_completed_at"] is None:
                next_step = "advisory_agreement"
            else:
                next_step = None

            return {
                "exists": True,
                "status": client["status"],
                "documents_complete": documents_complete,
                "next_step": next_step,
            }



def get_active_advisory_documents():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    document_type,
                    title,
                    version,
                    storage_location,
                    required_for_activation,
                    effective_at
                FROM advisory_documents
                WHERE is_active = TRUE
                ORDER BY required_for_activation DESC, title;
                """
            )

            return cur.fetchall()

            
def record_client_consent(
    parity_user_id: str,
    document_id: str,
    consent_type: str = "accepted",
    ip_address: str | None = None,
    user_agent: str | None = None,
    signature_method: str = "electronic",
    signature_reference: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Records a document consent for an advisory client.

    If every required active document has been accepted,
    the client is promoted to documents_complete.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            # Lock advisory client
            cur.execute(
                """
                SELECT *
                FROM advisory_clients
                WHERE parity_user_id = %s
                FOR UPDATE
                """,
                (parity_user_id,),
            )

            client = cur.fetchone()

            if not client:
                raise ValueError("Advisory client not found")

            # Validate document
            cur.execute(
                """
                SELECT *
                FROM advisory_documents
                WHERE id = %s
                  AND is_active = TRUE
                """,
                (document_id,),
            )

            document = cur.fetchone()

            if not document:
                raise ValueError("Active advisory document not found")

            # Insert consent
            cur.execute(
                """
                INSERT INTO client_consents (
                    client_id,
                    document_id,
                    consent_type,
                    signed_at,
                    ip_address,
                    user_agent,
                    signature_method,
                    signature_reference,
                    metadata
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    NOW(),
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb
                )
                RETURNING *
                """,
                (
                    client["id"],
                    document["id"],
                    consent_type,
                    ip_address,
                    user_agent,
                    signature_method,
                    signature_reference,
                    json.dumps(metadata or {}),
                ),
            )

            consent = cur.fetchone()

            # Audit event
            cur.execute(
                """
                INSERT INTO client_events (
                    client_id,
                    event_type,
                    actor_type,
                    actor_id,
                    event_data,
                    occurred_at
                )
                VALUES (
                    %s,
                    %s,
                    'client',
                    %s,
                    %s::jsonb,
                    NOW()
                )
                """,
                (
                    client["id"],
                    f"DOCUMENT_{consent_type.upper()}",
                    parity_user_id,
                    json.dumps(
                        {
                            "document_id": str(document["id"]),
                            "document_type": document["document_type"],
                            "version": document["version"],
                        }
                    ),
                ),
            )

            # Check whether every required active document has
            # a latest consent of "accepted"
            cur.execute(
                """
                SELECT COUNT(*)
                FROM advisory_documents d
                WHERE d.required_for_activation = TRUE
                  AND d.is_active = TRUE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM (
                          SELECT DISTINCT ON (document_id)
                              document_id,
                              consent_type
                          FROM client_consents
                          WHERE client_id = %s
                          ORDER BY document_id, signed_at DESC
                      ) latest
                      WHERE latest.document_id = d.id
                        AND latest.consent_type = 'accepted'
                  )
                """,
                (client["id"],),
            )

            remaining = cur.fetchone()["count"]

            documents_complete = remaining == 0

            if documents_complete and client["status"] == "onboarding":
                cur.execute(
                    """
                    UPDATE advisory_clients
                    SET
                        status = 'documents_complete'
                    WHERE id = %s
                    RETURNING *
                    """,
                    (client["id"],),
                )
                client = cur.fetchone()

            if (
                document["document_type"] == "investment_advisory_agreement"
                and consent_type == "accepted"
            ):
                cur.execute(
                    """
                UPDATE advisory_clients
                SET
                    agreement_completed_at = NOW(),
                    status = 'active',
                    activated_at = NOW()
                WHERE id = %s
                RETURNING *
                    """,
                    (client["id"],),
                )
            
                client = cur.fetchone()

                cur.execute(
                    """
                    INSERT INTO client_events (
                        client_id,
                        event_type,
                        actor_type,
                        event_data
                    )
                    VALUES (
                        %s,
                        'AGREEMENT_COMPLETED',
                        'system',
                        '{}'::jsonb
                    )
                    """,
                    (client["id"],),
                )

            conn.commit()

            return {
                "client": client,
                "consent": consent,
                "documents_complete": documents_complete,
            }


def sync_parity_subscription(
    parity_user_id: str,
    event_key: str,
    event_type: str,
    subscription_tier: str,
    subscription_status: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    stripe_price_id: str | None = None,
    current_period_start: str | None = None,
    current_period_end: str | None = None,
    cancel_at_period_end: bool = False,
    canceled_at: str | None = None,
    pending_tier: str | None = None,
    pending_change_at: str | None = None,
    access_grace_until: str | None = None,
    event_data: dict | None = None,
) -> dict:
    """
    Pushes a signup, tier change, cancellation, or payment-status
    change into Postgres.

    event_key must be unique so repeated events are not processed twice.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM parity_users
                WHERE id = %s
                """,
                (parity_user_id,),
            )

            if not cur.fetchone():
                raise ValueError("Parity user does not exist")

            cur.execute(
                """
                INSERT INTO parity_subscriptions (
                    parity_user_id
                )
                VALUES (%s)
                ON CONFLICT (parity_user_id) DO NOTHING
                """,
                (parity_user_id,),
            )

            cur.execute(
                """
                SELECT *
                FROM parity_subscriptions
                WHERE parity_user_id = %s
                FOR UPDATE
                """,
                (parity_user_id,),
            )

            previous = cur.fetchone()

            cur.execute(
                """
                INSERT INTO parity_subscription_events (
                    event_key,
                    parity_user_id,
                    event_type,
                    previous_tier,
                    new_tier,
                    previous_status,
                    new_status,
                    event_data,
                    effective_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb, NOW()
                )
                ON CONFLICT (event_key) DO NOTHING
                RETURNING id
                """,
                (
                    event_key,
                    parity_user_id,
                    event_type,
                    previous["subscription_tier"],
                    subscription_tier,
                    previous["subscription_status"],
                    subscription_status,
                    json.dumps(event_data or {}),
                ),
            )

            event_inserted = cur.fetchone()

            if not event_inserted:
                return previous

            cur.execute(
                """
                UPDATE parity_subscriptions
                SET
                    subscription_tier = %s,
                    subscription_status = %s,
                    stripe_customer_id = COALESCE(
                        %s,
                        stripe_customer_id
                    ),
                    stripe_subscription_id = COALESCE(
                        %s,
                        stripe_subscription_id
                    ),
                    stripe_price_id = COALESCE(
                        %s,
                        stripe_price_id
                    ),
                    current_period_start = %s,
                    current_period_end = %s,
                    cancel_at_period_end = %s,
                    canceled_at = %s,
                    pending_tier = %s,
                    pending_change_at = %s,
                    access_grace_until = %s,
                    last_event_key = %s,
                    last_event_type = %s,
                    last_event_at = NOW(),
                    updated_at = NOW()
                WHERE parity_user_id = %s
                RETURNING *
                """,
                (
                    subscription_tier,
                    subscription_status,
                    stripe_customer_id,
                    stripe_subscription_id,
                    stripe_price_id,
                    current_period_start,
                    current_period_end,
                    cancel_at_period_end,
                    canceled_at,
                    pending_tier,
                    pending_change_at,
                    access_grace_until,
                    event_key,
                    event_type,
                    parity_user_id,
                ),
            )

            subscription = cur.fetchone()
            conn.commit()

            return subscription


def get_subscription_access(
    parity_user_id: str,
) -> dict | None:
    """
    Returns the frontend permissions for one authenticated Parity user.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id AS parity_user_id,

                    COALESCE(
                        s.subscription_tier,
                        'free'
                    ) AS subscription_tier,

                    COALESCE(
                        s.subscription_status,
                        'none'
                    ) AS subscription_status,

                    COALESCE(
                        s.cancel_at_period_end,
                        FALSE
                    ) AS cancel_at_period_end,

                    s.current_period_end,
                    s.pending_tier,
                    s.pending_change_at,
                    s.complimentary_snapshot_started_at,
                    s.complimentary_snapshot_expires_at,

                    CASE
                        WHEN
                            s.subscription_status IN (
                                'active',
                                'trialing'
                            )
                            AND (
                                s.current_period_end IS NULL
                                OR s.current_period_end > NOW()
                            )
                        THEN TRUE

                        WHEN
                            s.subscription_status = 'past_due'
                            AND s.access_grace_until > NOW()
                        THEN TRUE

                        ELSE FALSE
                    END AS has_paid_access

                FROM parity_users u

                LEFT JOIN parity_subscriptions s
                    ON s.parity_user_id = u.id

                WHERE u.id = %s
                """,
                (parity_user_id,),
            )

            row = cur.fetchone()

    if not row:
        return None

    has_paid_access = row["has_paid_access"]

    effective_tier = (
        row["subscription_tier"]
        if has_paid_access
        else "free"
    )

    snapshot_started = (
        row["complimentary_snapshot_started_at"]
    )
    snapshot_expires = (
        row["complimentary_snapshot_expires_at"]
    )

    if snapshot_started is None:
        snapshot_status = "available"
    elif snapshot_expires and snapshot_expires > datetime.now(
        snapshot_expires.tzinfo
    ):
        snapshot_status = "active"
    else:
        snapshot_status = "used"

    return {
        "parity_user_id": parity_user_id,
        "effective_tier": effective_tier,
        "subscription_tier": row["subscription_tier"],
        "subscription_status": row["subscription_status"],
        "cancel_at_period_end": row["cancel_at_period_end"],
        "current_period_end": row["current_period_end"],
        "pending_tier": row["pending_tier"],
        "pending_change_at": row["pending_change_at"],
        "snapshot_status": snapshot_status,
        "snapshot_expires_at": snapshot_expires,

        # Free
        "can_browse_marketplace": True,
        "can_compare_outcomes": True,
        "can_use_advanced_options_analytics": True,
        "can_use_portfolio_stress_test": True,
        "can_use_portfolio_overview": True,
        "can_start_complimentary_snapshot": (
            snapshot_status == "available"
        ),
        "can_use_complimentary_snapshot": (
            snapshot_status == "active"
        ),

        # Connected and Complete
        "can_use_live_brokerage_connections": (
            has_paid_access
            and effective_tier in (
                "connected",
                "complete",
            )
        ),
        "can_export_csv_pdf": (
            has_paid_access
            and effective_tier in (
                "connected",
                "complete",
            )
        ),

        # Complete
        "can_create_or_renew_outcomes": (
            has_paid_access
            and effective_tier == "complete"
        ),
        "can_execute_new_orders": (
            has_paid_access
            and effective_tier == "complete"
        ),
        "can_use_active_outcome_monitoring": (
            has_paid_access
            and effective_tier == "complete"
        ),
        "can_use_position_expiration_alerts": (
            has_paid_access
            and effective_tier == "complete"
        ),

        # Safety features are never removed.
        "can_view_existing_outcomes": True,
        "can_close_existing_outcomes": True,
        "receives_basic_expiration_reminders": True,
    }


def start_complimentary_snapshot(
    parity_user_id: str,
) -> dict:
    """
    Starts the user's one complimentary 24-hour snapshot.
    Calling this again does not reset the expiration.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM parity_users
                WHERE id = %s
                """,
                (parity_user_id,),
            )

            if not cur.fetchone():
                raise ValueError("Parity user does not exist")

            cur.execute(
                """
                INSERT INTO parity_subscriptions (
                    parity_user_id
                )
                VALUES (%s)
                ON CONFLICT (parity_user_id) DO NOTHING
                """,
                (parity_user_id,),
            )

            cur.execute(
                """
                UPDATE parity_subscriptions
                SET
                    complimentary_snapshot_started_at = NOW(),
                    complimentary_snapshot_expires_at =
                        NOW() + INTERVAL '24 hours',
                    updated_at = NOW()
                WHERE parity_user_id = %s
                  AND complimentary_snapshot_started_at IS NULL
                RETURNING *
                """,
                (parity_user_id,),
            )

            snapshot = cur.fetchone()

            if not snapshot:
                cur.execute(
                    """
                    SELECT *
                    FROM parity_subscriptions
                    WHERE parity_user_id = %s
                    """,
                    (parity_user_id,),
                )
                snapshot = cur.fetchone()

            conn.commit()

            return snapshot

import json
from typing import Any


def upsert_orats_summary_snapshots(
    rows: list[dict[str, Any]],
) -> int:
    if not rows:
        return 0

    query = """
        INSERT INTO orats_summary_snapshots (
            ticker,
            trade_date,
            stock_price,

            annual_actual_dividend,
            annual_implied_dividend,
            next_dividend,
            implied_next_dividend,

            borrow_30d,
            borrow_2y,
            risk_free_30d,
            risk_free_2y,

            confidence,
            total_error_confidence,

            iv_10d,
            iv_20d,
            iv_30d,
            iv_60d,
            iv_90d,
            iv_6m,
            iv_1y,

            ex_earnings_iv_10d,
            ex_earnings_iv_20d,
            ex_earnings_iv_30d,
            ex_earnings_iv_60d,
            ex_earnings_iv_90d,
            ex_earnings_iv_6m,
            ex_earnings_iv_1y,

            implied_earnings_effect,
            implied_move,
            implied_earnings_move,

            mw_adjusted_30d,
            mw_adjusted_2y,
            residual_driver_30d,
            residual_driver_2y,
            residual_slope_30d,
            residual_slope_2y,
            residual_volatility_30d,
            residual_volatility_2y,
            relative_implied_price,
            skewing,
            contango,

            quote_date,
            source_updated_at,
            snapshot_est_time,
            snapshot_date,
            ticker_id,

            raw_json,
            fetched_at,
            source
        )
        VALUES (
            %(ticker)s,
            %(trade_date)s,
            %(stock_price)s,

            %(annual_actual_dividend)s,
            %(annual_implied_dividend)s,
            %(next_dividend)s,
            %(implied_next_dividend)s,

            %(borrow_30d)s,
            %(borrow_2y)s,
            %(risk_free_30d)s,
            %(risk_free_2y)s,

            %(confidence)s,
            %(total_error_confidence)s,

            %(iv_10d)s,
            %(iv_20d)s,
            %(iv_30d)s,
            %(iv_60d)s,
            %(iv_90d)s,
            %(iv_6m)s,
            %(iv_1y)s,

            %(ex_earnings_iv_10d)s,
            %(ex_earnings_iv_20d)s,
            %(ex_earnings_iv_30d)s,
            %(ex_earnings_iv_60d)s,
            %(ex_earnings_iv_90d)s,
            %(ex_earnings_iv_6m)s,
            %(ex_earnings_iv_1y)s,

            %(implied_earnings_effect)s,
            %(implied_move)s,
            %(implied_earnings_move)s,

            %(mw_adjusted_30d)s,
            %(mw_adjusted_2y)s,
            %(residual_driver_30d)s,
            %(residual_driver_2y)s,
            %(residual_slope_30d)s,
            %(residual_slope_2y)s,
            %(residual_volatility_30d)s,
            %(residual_volatility_2y)s,
            %(relative_implied_price)s,
            %(skewing)s,
            %(contango)s,

            %(quote_date)s,
            %(source_updated_at)s,
            %(snapshot_est_time)s,
            %(snapshot_date)s,
            %(ticker_id)s,

            %(raw_json)s::jsonb,
            NOW(),
            'ORATS'
        )
        ON CONFLICT (ticker)
        DO UPDATE SET
            trade_date = EXCLUDED.trade_date,
            stock_price = EXCLUDED.stock_price,

            annual_actual_dividend =
                EXCLUDED.annual_actual_dividend,
            annual_implied_dividend =
                EXCLUDED.annual_implied_dividend,
            next_dividend = EXCLUDED.next_dividend,
            implied_next_dividend =
                EXCLUDED.implied_next_dividend,

            borrow_30d = EXCLUDED.borrow_30d,
            borrow_2y = EXCLUDED.borrow_2y,
            risk_free_30d = EXCLUDED.risk_free_30d,
            risk_free_2y = EXCLUDED.risk_free_2y,

            confidence = EXCLUDED.confidence,
            total_error_confidence =
                EXCLUDED.total_error_confidence,

            iv_10d = EXCLUDED.iv_10d,
            iv_20d = EXCLUDED.iv_20d,
            iv_30d = EXCLUDED.iv_30d,
            iv_60d = EXCLUDED.iv_60d,
            iv_90d = EXCLUDED.iv_90d,
            iv_6m = EXCLUDED.iv_6m,
            iv_1y = EXCLUDED.iv_1y,

            ex_earnings_iv_10d =
                EXCLUDED.ex_earnings_iv_10d,
            ex_earnings_iv_20d =
                EXCLUDED.ex_earnings_iv_20d,
            ex_earnings_iv_30d =
                EXCLUDED.ex_earnings_iv_30d,
            ex_earnings_iv_60d =
                EXCLUDED.ex_earnings_iv_60d,
            ex_earnings_iv_90d =
                EXCLUDED.ex_earnings_iv_90d,
            ex_earnings_iv_6m =
                EXCLUDED.ex_earnings_iv_6m,
            ex_earnings_iv_1y =
                EXCLUDED.ex_earnings_iv_1y,

            implied_earnings_effect =
                EXCLUDED.implied_earnings_effect,
            implied_move = EXCLUDED.implied_move,
            implied_earnings_move =
                EXCLUDED.implied_earnings_move,

            mw_adjusted_30d =
                EXCLUDED.mw_adjusted_30d,
            mw_adjusted_2y =
                EXCLUDED.mw_adjusted_2y,
            residual_driver_30d =
                EXCLUDED.residual_driver_30d,
            residual_driver_2y =
                EXCLUDED.residual_driver_2y,
            residual_slope_30d =
                EXCLUDED.residual_slope_30d,
            residual_slope_2y =
                EXCLUDED.residual_slope_2y,
            residual_volatility_30d =
                EXCLUDED.residual_volatility_30d,
            residual_volatility_2y =
                EXCLUDED.residual_volatility_2y,
            relative_implied_price =
                EXCLUDED.relative_implied_price,
            skewing = EXCLUDED.skewing,
            contango = EXCLUDED.contango,

            quote_date = EXCLUDED.quote_date,
            source_updated_at =
                EXCLUDED.source_updated_at,
            snapshot_est_time =
                EXCLUDED.snapshot_est_time,
            snapshot_date = EXCLUDED.snapshot_date,
            ticker_id = EXCLUDED.ticker_id,

            raw_json = EXCLUDED.raw_json,
            fetched_at = NOW(),
            source = EXCLUDED.source
    """

    normalized_rows = [
        normalize_orats_summary(row)
        for row in rows
        if row.get("ticker")
        and str(row.get("ticker")).lower() != "nan"
    ]

    with get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(query, normalized_rows)

        conn.commit()

    return len(normalized_rows)

import math


import math
import numbers

def clean_json_value(value):
    if isinstance(value, numbers.Real):
        if math.isnan(float(value)):
            return None
        return float(value)

    if isinstance(value, dict):
        return {
            k: clean_json_value(v)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [
            clean_json_value(v)
            for v in value
        ]

    return value


def normalize_orats_summary(
    row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ticker": str(row["ticker"]).upper(),
        "trade_date": row.get("tradeDate"),
        "stock_price": row.get("stockPrice"),

        "annual_actual_dividend": row.get("annActDiv"),
        "annual_implied_dividend": row.get("annIdiv"),
        "next_dividend": row.get("nextDiv"),
        "implied_next_dividend": row.get(
            "impliedNextDiv"
        ),

        "borrow_30d": row.get("borrow30"),
        "borrow_2y": row.get("borrow2y"),
        "risk_free_30d": row.get("riskFree30"),
        "risk_free_2y": row.get("riskFree2y"),

        "confidence": row.get("confidence"),
        "total_error_confidence": row.get(
            "totalErrorConf"
        ),

        "iv_10d": row.get("iv10d"),
        "iv_20d": row.get("iv20d"),
        "iv_30d": row.get("iv30d"),
        "iv_60d": row.get("iv60d"),
        "iv_90d": row.get("iv90d"),
        "iv_6m": row.get("iv6m"),
        "iv_1y": row.get("iv1y"),

        "ex_earnings_iv_10d": row.get("exErnIv10d"),
        "ex_earnings_iv_20d": row.get("exErnIv20d"),
        "ex_earnings_iv_30d": row.get("exErnIv30d"),
        "ex_earnings_iv_60d": row.get("exErnIv60d"),
        "ex_earnings_iv_90d": row.get("exErnIv90d"),
        "ex_earnings_iv_6m": row.get("exErnIv6m"),
        "ex_earnings_iv_1y": row.get("exErnIv1y"),

        "implied_earnings_effect": row.get(
            "ieeEarnEffect"
        ),
        "implied_move": row.get("impliedMove"),
        "implied_earnings_move": row.get(
            "impliedEarningsMove"
        ),

        "mw_adjusted_30d": row.get("mwAdj30"),
        "mw_adjusted_2y": row.get("mwAdj2y"),
        "residual_driver_30d": row.get("rDrv30"),
        "residual_driver_2y": row.get("rDrv2y"),
        "residual_slope_30d": row.get("rSlp30"),
        "residual_slope_2y": row.get("rSlp2y"),
        "residual_volatility_30d": row.get("rVol30"),
        "residual_volatility_2y": row.get("rVol2y"),
        "relative_implied_price": row.get("rip"),
        "skewing": row.get("skewing"),
        "contango": row.get("contango"),

        "quote_date": row.get("quoteDate"),
        "source_updated_at": row.get("updatedAt"),
        "snapshot_est_time": row.get(
            "snapShotEstTime"
        ),
        "snapshot_date": row.get("snapShotDate"),
        "ticker_id": row.get("tickerId"),

        "raw_json": json.dumps(
        clean_json_value(row),
        allow_nan=False,
    ),
    }

def get_orats_summary(ticker: str) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM orats_summary_snapshots
                WHERE ticker = %s
                """,
                (ticker.upper(),),
            )

            return cursor.fetchone()


def refresh_orats_summary_cache() -> dict:
    rows = fetch_all_orats_summaries()

    saved_count = upsert_orats_summary_snapshots(rows)

    return {
        "received": len(rows),
        "saved": saved_count,
        "refreshed_at": datetime.now().isoformat(),
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



def create_prepared_option_order(
    parity_user_id: str,
    account_id: str,
    brokerage_slug: str,
    strategy_type: str,
    order_payload: dict,
    quote_snapshot: dict | None,
    expires_at: str,
) -> dict:
    """
    Save an order that has been prepared for user review but not submitted.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO prepared_option_orders (
                    parity_user_id,
                    account_id,
                    brokerage_slug,
                    strategy_type,
                    order_payload,
                    quote_snapshot,
                    expires_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s::jsonb,
                    %s
                )
                RETURNING *
                """,
                (
                    parity_user_id,
                    account_id,
                    brokerage_slug,
                    strategy_type,
                    json.dumps(order_payload),
                    json.dumps(quote_snapshot or {}),
                    expires_at,
                ),
            )
            prepared_order = cur.fetchone()
            conn.commit()

    return prepared_order


def get_prepared_option_order(
    parity_user_id: str,
    prepared_order_id: str,
) -> dict | None:
    """
    Return one prepared order only when it belongs to the requesting user.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM prepared_option_orders
                WHERE id = %s
                  AND parity_user_id = %s
                """,
                (
                    prepared_order_id,
                    parity_user_id,
                ),
            )
            return cur.fetchone()

def create_execution_workflow(
    parity_user_id: str,
    account_id: str,
    brokerage_slug: str,
    strategy_type: str,
    underlying_source: str,
    underlying_symbol: str,
    underlying_shares: int,
) -> dict:
    """
    Create a parent workflow before any underlying or option order is prepared.
    """
    option_contracts = underlying_shares // 100

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO execution_workflows (
                    parity_user_id,
                    account_id,
                    brokerage_slug,
                    strategy_type,
                    underlying_source,
                    underlying_symbol,
                    underlying_shares,
                    option_contracts
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                RETURNING *
                """,
                (
                    parity_user_id,
                    account_id,
                    brokerage_slug,
                    strategy_type,
                    underlying_source,
                    underlying_symbol.upper(),
                    underlying_shares,
                    option_contracts,
                ),
            )
            workflow = cur.fetchone()
            conn.commit()

    return workflow



def create_execution_workflow_lots(
    parity_user_id: str,
    workflow_id: str,
    underlying_source: str,
    option_contracts: int,
) -> list[dict]:
    """
    Create execution lots for one workflow.

    Existing holdings retain one separate 100-share row per option
    contract. New positions use one aggregate row so Parity can submit
    one equity order and one scaled options package.
    """
    if underlying_source not in {"existing", "new"}:
        raise ValueError(
            "underlying_source must be 'existing' or 'new'"
        )

    if option_contracts <= 0:
        raise ValueError(
            "option_contracts must be greater than zero"
        )

    if underlying_source == "new":
        rows = [
            (
                workflow_id,
                1,
                option_contracts * 100,
                0,
            )
        ]
    else:
        rows = [
            (
                workflow_id,
                lot_number,
                100,
                100,
            )
            for lot_number in range(1, option_contracts + 1)
        ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM execution_workflows
                WHERE id = %s
                  AND parity_user_id = %s
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            if not cur.fetchone():
                raise ValueError(
                    "Execution workflow was not found"
                )

            cur.executemany(
                """
                INSERT INTO execution_workflow_lots (
                    workflow_id,
                    lot_number,
                    share_quantity,
                    reserved_share_quantity
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (workflow_id, lot_number)
                DO NOTHING
                """,
                rows,
            )

            cur.execute(
                """
                SELECT *
                FROM execution_workflow_lots
                WHERE workflow_id = %s
                ORDER BY lot_number
                """,
                (workflow_id,),
            )
            lots = cur.fetchall()

            conn.commit()

    return lots

    
def get_execution_workflow_lots(
    parity_user_id: str,
    workflow_id: str,
) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM execution_workflow_lots
                WHERE workflow_id = %s
                  AND workflow_id IN (
                      SELECT id
                      FROM execution_workflows
                      WHERE parity_user_id = %s
                  )
                ORDER BY lot_number
                """,
                (workflow_id, parity_user_id),
            )

            return cur.fetchall()



def get_execution_workflow(
    parity_user_id: str,
    workflow_id: str,
) -> dict | None:
    """
    Return a workflow only when it belongs to the requesting user.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM execution_workflows
                WHERE id = %s
                  AND parity_user_id = %s
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )
            return cur.fetchone()


def update_execution_workflow(
    parity_user_id: str,
    workflow_id: str,
    status: str,
    underlying_prepared_order_id: str | None = None,
    options_prepared_order_id: str | None = None,
) -> dict:
    """
    Advance a workflow without allowing a user to update another user's record.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_workflows
                SET
                    status = %s,
                    underlying_prepared_order_id = COALESCE(
                        %s,
                        underlying_prepared_order_id
                    ),
                    options_prepared_order_id = COALESCE(
                        %s,
                        options_prepared_order_id
                    ),
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                RETURNING *
                """,
                (
                    status,
                    underlying_prepared_order_id,
                    options_prepared_order_id,
                    workflow_id,
                    parity_user_id,
                ),
            )
            workflow = cur.fetchone()

            if not workflow:
                raise ValueError(
                    "Execution workflow was not found"
                )

            conn.commit()

    return workflow


def save_execution_workflow_plan(
    parity_user_id: str,
    workflow_id: str,
    execution_plan: list[dict],
    execution_preference: str | None = None,
) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_workflows
                SET execution_plan = %s::jsonb,
                    execution_preference = %s,
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                RETURNING *
                """,
                (
                    json.dumps(execution_plan),
                    execution_preference,
                    workflow_id,
                    parity_user_id,
                ),
            )

            workflow = cur.fetchone()
            conn.commit()

    return workflow










def record_new_position_workflow_approval(
    *,
    parity_user_id: str,
    workflow_id: str,
    option_contracts: list[dict],
    option_limit_price: float,
    option_price_effect: str,
    option_time_in_force: str,
    option_quote_snapshot: dict,
) -> dict:
    """
    Persist one explicit, immutable user approval for a new-position
    workflow. This does not create or submit any brokerage order.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_workflows
                SET
                    approved_option_contracts = %s::jsonb,
                    approved_option_limit_price = %s,
                    approved_option_price_effect = %s,
                    approved_option_time_in_force = %s,
                    approved_option_quote_snapshot = %s::jsonb,
                    approved_at = NOW(),
                    status = 'APPROVED_PENDING_UNDERLYING_FILL',
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND underlying_source = 'new'
                  AND status = 'DRAFT'
                RETURNING *
                """,
                (
                    json.dumps(option_contracts),
                    option_limit_price,
                    option_price_effect,
                    option_time_in_force,
                    json.dumps(option_quote_snapshot),
                    workflow_id,
                    parity_user_id,
                ),
            )
            workflow = cur.fetchone()
            conn.commit()

    if not workflow:
        raise ValueError(
            "This workflow is not available for approval"
        )

    return workflow


def claim_workflow_option_submission_after_underlying_fill(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> dict | None:
    """
    Atomically claim the stored overlay approval only after the
    workflow's initial equity order is broker-confirmed FILLED.

    A second refresh or browser retry returns None instead of creating
    a duplicate option submission.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_workflows w
                SET
                    status = 'OPTIONS_SUBMITTING',
                    updated_at = NOW()
                WHERE w.id = %s
                  AND w.parity_user_id = %s
                  AND w.status = (
                      'APPROVED_PENDING_UNDERLYING_FILL'
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM execution_orders o
                      WHERE o.workflow_id = w.id
                        AND o.sequence = 1
                        AND o.status = 'FILLED'
                  )
                RETURNING w.*
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )
            workflow = cur.fetchone()
            conn.commit()

    return workflow


def mark_workflow_options_submitted(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_workflows
                SET
                    status = 'OPTIONS_SUBMITTED',
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status = 'OPTIONS_SUBMITTING'
                RETURNING *
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )
            workflow = cur.fetchone()
            conn.commit()

    if not workflow:
        raise ValueError(
            "Workflow option submission state could not be recorded"
        )

    return workflow


def mark_workflow_action_required(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_workflows
                SET
                    status = 'ACTION_REQUIRED',
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status IN (
                      'APPROVED_PENDING_UNDERLYING_FILL',
                      'PENDING_OPTIONS_SUBMISSION',
                      'OPTIONS_SUBMITTING'
                  )
                RETURNING *
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )
            workflow = cur.fetchone()
            conn.commit()

    if not workflow:
        raise ValueError(
            "Workflow is not available for action-required handling"
        )

    return workflow












def create_execution_order(
    parity_user_id: str,
    workflow_id: str,
    lot_id: str,
    sequence: int,
    order_role: str,
    order_scope: str,
    requested_quantity: float,
    order_payload: dict,
    execution_phase: str = "INITIAL",
    replaces_order_id: str | None = None,
    limit_price: float | None = None,
    price_effect: str | None = None,
    quote_snapshot: dict | None = None,
) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM execution_workflows
                WHERE id = %s
                  AND parity_user_id = %s
                FOR UPDATE
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            if not cur.fetchone():
                raise ValueError(
                    "Execution workflow was not found"
                )

            cur.execute(
                """
                SELECT *
                FROM execution_orders
                WHERE workflow_id = %s
                  AND lot_id = %s
                  AND sequence = %s
                  AND execution_phase = %s
                  AND status IN (
                      'DRAFT',
                      'PREPARED',
                      'SUBMITTING',
                      'SUBMITTED',
                      'WORKING',
                      'PARTIALLY_FILLED',
                      'ACTION_REQUIRED'
                  )
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    workflow_id,
                    lot_id,
                    sequence,
                    execution_phase,
                ),
            )

            existing_execution_order = cur.fetchone()

            if existing_execution_order:
                return existing_execution_order
            cur.execute(
                """
                INSERT INTO execution_orders (
                    workflow_id,
                    lot_id,
                    parity_user_id,
                    account_id,
                    brokerage_slug,
                    sequence,
                    order_role,
                    order_scope,
                    execution_phase,
                    replaces_order_id,
                    requested_quantity,
                    limit_price,
                    price_effect,
                    order_payload,
                    quote_snapshot
                )
                SELECT
                    w.id,
                    l.id,
                    w.parity_user_id,
                    w.account_id,
                    w.brokerage_slug,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s::jsonb
                FROM execution_workflows w
                JOIN execution_workflow_lots l
                  ON l.workflow_id = w.id
                WHERE w.id = %s
                  AND w.parity_user_id = %s
                  AND l.id = %s
                RETURNING *
                """,
                (
                    sequence,
                    order_role,
                    order_scope,
                    execution_phase,
                    replaces_order_id,
                    requested_quantity,
                    limit_price,
                    price_effect,
                    json.dumps(order_payload),
                    json.dumps(quote_snapshot or {}),
                    workflow_id,
                    parity_user_id,
                    lot_id,
                ),
            )

            execution_order = cur.fetchone()

            if not execution_order:
                raise ValueError(
                    "Execution workflow or lot was not found"
                )

            conn.commit()

    return execution_order



def refresh_execution_order_draft(
    *,
    parity_user_id: str,
    order_id: str,
    order_payload: dict,
    limit_price: float,
    price_effect: str,
    quote_snapshot: dict,
) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_orders
                SET
                    order_payload = %s::jsonb,
                    limit_price = %s,
                    price_effect = %s,
                    quote_snapshot = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status = 'DRAFT'
                RETURNING *
                """,
                (
                    json.dumps(order_payload),
                    limit_price,
                    price_effect,
                    json.dumps(quote_snapshot),
                    order_id,
                    parity_user_id,
                ),
            )
            order = cur.fetchone()

            if not order:
                raise ValueError(
                    "Only DRAFT execution orders can refresh quotes"
                )

            conn.commit()

    return order



    
def get_execution_workflow_orders(
    parity_user_id: str,
    workflow_id: str,
) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT eo.*
                FROM execution_orders eo
                JOIN execution_workflows w
                  ON w.id = eo.workflow_id
                WHERE eo.workflow_id = %s
                  AND w.parity_user_id = %s
                ORDER BY eo.sequence, eo.created_at
                """,
                (workflow_id, parity_user_id),
            )

            return cur.fetchall()





def create_protected_position_lot(
    *,
    parity_user_id: str,
    account_id: str,
    brokerage_slug: str,
    opening_workflow_id: str,
    opening_workflow_lot_id: str,
    underlying_symbol: str,
    share_quantity: int,
    share_source: str,
    strategy_type: str,
    option_contracts: list[dict[str, Any]],
    options_open_order_id: str,
    protection_opened_at: datetime,
    share_entry_fill_price: float | None = None,
    share_entry_filled_at: datetime | None = None,
    underlying_reference_price: float | None = None,
    underlying_reference_at: datetime | None = None,
    option_entry_net_price: float | None = None,
    option_entry_price_effect: str | None = None,
    entry_strategy_value: float | None = None,
    entry_outcome_snapshot: dict | None = None,
) -> dict:
    """
     Persist one active Parity protected position after its
    complete opening options package is broker-confirmed FILLED.

    This is idempotent: repeated fill polling returns the same lot.
    """

    if share_source not in {
        "EXISTING_HOLDING",
        "PARITY_NEW_POSITION",
    }:
        raise ValueError("Invalid protected-lot share source")

    if strategy_type not in {
        "covered_call",
        "married_put",
        "collar",
        "buffer",
    }:
        raise ValueError("Invalid protected-lot strategy type")
        
    if option_entry_price_effect not in {
        None,
        "DEBIT",
        "CREDIT",
        "EVEN",
    }:
        raise ValueError(
            "Invalid option entry price effect"
        )
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO protected_position_lots (
                    parity_user_id,
                    account_id,
                    brokerage_slug,
                    opening_workflow_id,
                    opening_workflow_lot_id,
                    underlying_symbol,
                    share_quantity,
                    share_source,
                    share_entry_fill_price,
                    share_entry_filled_at,
                    underlying_reference_price,
                    underlying_reference_at,
                    strategy_type,
                    option_contracts,
                    option_entry_net_price,
                    option_entry_price_effect,
                    entry_strategy_value,
                    entry_outcome_snapshot,
                    options_open_order_id,
                    protection_opened_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s::jsonb, %s,
                    %s, %s, %s::jsonb, %s, %s
                )
                ON CONFLICT (opening_workflow_lot_id)
                DO NOTHING
                RETURNING *
                """,
                (
                    parity_user_id,
                    account_id,
                    brokerage_slug,
                    opening_workflow_id,
                    opening_workflow_lot_id,
                    underlying_symbol.strip().upper(),
                    share_quantity,
                    share_source,
                    share_entry_fill_price,
                    share_entry_filled_at,
                    underlying_reference_price,
                    underlying_reference_at,
                    strategy_type,
                    json.dumps(option_contracts),
                    option_entry_net_price,
                    option_entry_price_effect,
                    entry_strategy_value,
                    json.dumps(entry_outcome_snapshot or {}),
                    options_open_order_id,
                    protection_opened_at,
                ),
            )

            protected_lot = cur.fetchone()

            if protected_lot is None:
                cur.execute(
                    """
                    SELECT *
                    FROM protected_position_lots
                    WHERE opening_workflow_lot_id = %s
                      AND parity_user_id = %s
                    """,
                    (
                        opening_workflow_lot_id,
                        parity_user_id,
                    ),
                )
                protected_lot = cur.fetchone()

            if protected_lot is None:
                raise ValueError(
                    "Protected position lot could not be created"
                )

            conn.commit()

    return protected_lot


def list_active_protected_position_lots(
    *,
    parity_user_id: str,
) -> list[dict]:
    """
    Return active Parity-tracked protection lots for one user.

    This does not call the brokerage or infer outside option positions.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM protected_position_lots
                WHERE parity_user_id = %s
                  AND status = 'ACTIVE'
                ORDER BY protection_opened_at DESC
                """,
                (parity_user_id,),
            )

            return cur.fetchall()



def list_managed_protected_position_lots(
    *,
    parity_user_id: str,
    account_id: str,
    underlying_symbol: str,
) -> list[dict]:
    """
    Return non-closed Parity-managed protected lots for one account
    and underlying.

    This is database-only and does not call the brokerage.
    """

    normalized_symbol = (
        underlying_symbol.strip().upper()
    )

    if not normalized_symbol:
        raise ValueError(
            "underlying_symbol is required"
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM protected_position_lots
                WHERE parity_user_id = %s
                  AND account_id = %s
                  AND underlying_symbol = %s
                  AND status IN (
                      'ACTIVE',
                      'RECONCILIATION_REQUIRED'
                  )
                ORDER BY protection_opened_at DESC
                """,
                (
                    parity_user_id,
                    account_id,
                    normalized_symbol,
                ),
            )

            return cur.fetchall()


def list_active_option_execution_orders(
    *,
    parity_user_id: str,
    account_id: str,
    underlying_symbol: str,
) -> list[dict]:
    """
    Return nonterminal Parity option orders for one account and
    underlying.

    This is database-only and does not call the brokerage.
    """

    normalized_symbol = (
        underlying_symbol.strip().upper()
    )

    if not normalized_symbol:
        raise ValueError(
            "underlying_symbol is required"
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT execution_order.*
                FROM execution_orders execution_order
                JOIN execution_workflows workflow
                  ON workflow.id =
                     execution_order.workflow_id
                WHERE execution_order.parity_user_id = %s
                  AND execution_order.account_id = %s
                  AND workflow.underlying_symbol = %s
                  AND execution_order.order_scope IN (
                      'OPTIONS',
                      'OPTIONS_PACKAGE'
                  )
                AND execution_order.broker_order_id IS NOT NULL
                AND execution_order.status IN (
                        'SUBMITTED',
                        'WORKING',
                        'PARTIALLY_FILLED',
                        'CANCELING',
                        'ACTION_REQUIRED'
                    )
                ORDER BY execution_order.created_at DESC
                """,
                (
                    parity_user_id,
                    account_id,
                    normalized_symbol,
                ),
            )

            return cur.fetchall()





def list_protected_positions_with_latest_mark(
    *,
    parity_user_id: str,
) -> list[dict]:
    """
    Return all protected positions for one user together with each
    position's most recent analytics mark.

    This is database-only and does not refresh brokerage or market data.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    position.*,

                    latest_mark.id
                        AS latest_mark_id,
                    latest_mark.marked_at
                        AS latest_marked_at,
                    latest_mark.underlying_price
                        AS latest_underlying_price,
                    latest_mark.underlying_market_value
                        AS latest_underlying_market_value,
                    latest_mark.option_market_value
                        AS latest_option_market_value,
                    latest_mark.strategy_market_value
                        AS latest_strategy_market_value,
                    latest_mark.pnl_dollars
                        AS latest_pnl_dollars,
                    latest_mark.pnl_percent
                        AS latest_pnl_percent,
                    latest_mark.quote_source
                        AS latest_quote_source

                FROM protected_position_lots position

                LEFT JOIN LATERAL (
                    SELECT
                        mark.id,
                        mark.marked_at,
                        mark.underlying_price,
                        mark.underlying_market_value,
                        mark.option_market_value,
                        mark.strategy_market_value,
                        mark.pnl_dollars,
                        mark.pnl_percent,
                        mark.quote_source
                    FROM protected_position_marks mark
                    WHERE mark.protected_lot_id = position.id
                    ORDER BY
                        mark.marked_at DESC,
                        mark.created_at DESC
                    LIMIT 1
                ) latest_mark
                    ON TRUE

                WHERE position.parity_user_id = %s

                ORDER BY
                    position.protection_opened_at DESC,
                    position.created_at DESC
                """,
                (parity_user_id,),
            )

            return cur.fetchall()




def list_execution_activity(
    *,
    parity_user_id: str,
    limit: int = 100,
) -> list[dict]:
    """
    Return broker execution activity for one user.

    Unsubmitted drafts and merely prepared orders are excluded.
    This is database-only and does not contact the broker.
    """

    if limit <= 0 or limit > 500:
        raise ValueError("limit must be between 1 and 500")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    execution_order.id AS order_id,
                    execution_order.workflow_id,
                    execution_order.lot_id,
                    execution_order.replaces_order_id,

                    workflow.underlying_symbol,
                    workflow.underlying_source,
                    workflow.underlying_shares,
                    workflow.strategy_type,
                    workflow.status AS workflow_status,
                    workflow.attention_resolved_at,
                    workflow.attention_resolution_code,
                    workflow.attention_resolution_note,
                    workflow_lot.lot_number,
                    workflow_lot.share_quantity,

                    execution_order.account_id,
                    execution_order.brokerage_slug,
                    execution_order.sequence,
                    execution_order.order_role,
                    execution_order.order_scope,
                    execution_order.execution_phase,
                    execution_order.status AS order_status,

                    execution_order.requested_quantity,
                    execution_order.filled_quantity,
                    execution_order.limit_price,
                    execution_order.price_effect,
                    execution_order.average_fill_price,

                    execution_order.order_payload,
                    execution_order.quote_snapshot,
                    execution_order.rejection_reason,

                    execution_order.submitted_at,
                    execution_order.filled_at,
                    execution_order.canceled_at,
                    execution_order.created_at,
                    execution_order.updated_at,

                    COALESCE(
                        execution_order.filled_at,
                        execution_order.canceled_at,
                        execution_order.submitted_at,
                        execution_order.created_at
                    ) AS activity_at

                FROM execution_orders execution_order

                JOIN execution_workflows workflow
                  ON workflow.id = execution_order.workflow_id

                JOIN execution_workflow_lots workflow_lot
                  ON workflow_lot.id = execution_order.lot_id

                WHERE execution_order.parity_user_id = %s
                  AND execution_order.status NOT IN (
                      'DRAFT',
                      'PREPARED'
                  )

                ORDER BY
                    activity_at DESC,
                    execution_order.created_at DESC

                LIMIT %s
                """,
                (
                    parity_user_id,
                    limit,
                ),
            )

            return cur.fetchall()



def list_protected_position_marks(
    *,
    parity_user_id: str,
    protected_lot_id: str,
    limit: int = 500,
) -> list[dict]:
    """
    Return the most recent stored valuation marks for one owned
    protected position, ordered oldest to newest for charting.

    This is database-only and does not refresh market data.
    """

    if limit <= 0 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM (
                    SELECT
                        mark.*
                    FROM protected_position_marks mark
                    JOIN protected_position_lots position
                      ON position.id = mark.protected_lot_id
                    WHERE mark.protected_lot_id = %s
                      AND position.parity_user_id = %s
                    ORDER BY
                        mark.marked_at DESC,
                        mark.created_at DESC
                    LIMIT %s
                ) recent_marks
                ORDER BY
                    marked_at ASC,
                    created_at ASC
                """,
                (
                    protected_lot_id,
                    parity_user_id,
                    limit,
                ),
            )

            return cur.fetchall()



def create_protected_position_mark(
    *,
    parity_user_id: str,
    protected_lot_id: str,
    underlying_price: float,
    underlying_market_value: float,
    option_market_value: float,
    strategy_market_value: float,
    pnl_dollars: float,
    pnl_percent: float,
    quote_source: str,
    quote_snapshot: dict,
    marked_at: datetime | None = None,
    mark_type: str = "MANUAL",
    market_date: date | None = None,
) -> dict:
    """
    Persist one point-in-time valuation for an active protected
    position.

    Daily closing marks are idempotent by position and market date.
    """

    if not quote_source.strip():
        raise ValueError(
            "Protected-position mark requires a quote source"
        )

    normalized_mark_type = mark_type.strip().upper()

    if normalized_mark_type not in {
        "MANUAL",
        "DAILY_CLOSE",
    }:
        raise ValueError(
            "mark_type must be MANUAL or DAILY_CLOSE"
        )

    if (
        normalized_mark_type == "DAILY_CLOSE"
        and market_date is None
    ):
        raise ValueError(
            "Daily closing marks require a market date"
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO protected_position_marks (
                    protected_lot_id,
                    parity_user_id,
                    marked_at,
                    mark_type,
                    market_date,
                    underlying_price,
                    underlying_market_value,
                    option_market_value,
                    strategy_market_value,
                    pnl_dollars,
                    pnl_percent,
                    quote_source,
                    quote_snapshot
                )
                SELECT
                    position.id,
                    position.parity_user_id,
                    COALESCE(%s, NOW()),
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
                FROM protected_position_lots position
                WHERE position.id = %s
                  AND position.parity_user_id = %s
                  AND position.status = 'ACTIVE'

                ON CONFLICT (
                    protected_lot_id,
                    market_date
                )
                WHERE mark_type = 'DAILY_CLOSE'
                DO NOTHING

                RETURNING *
                """,
                (
                    marked_at,
                    normalized_mark_type,
                    market_date,
                    underlying_price,
                    underlying_market_value,
                    option_market_value,
                    strategy_market_value,
                    pnl_dollars,
                    pnl_percent,
                    quote_source.strip().upper(),
                    json.dumps(quote_snapshot),
                    protected_lot_id,
                    parity_user_id,
                ),
            )

            mark = cur.fetchone()

            if (
                mark is None
                and normalized_mark_type == "DAILY_CLOSE"
            ):
                cur.execute(
                    """
                    SELECT mark.*
                    FROM protected_position_marks mark
                    JOIN protected_position_lots position
                      ON position.id = mark.protected_lot_id
                    WHERE mark.protected_lot_id = %s
                      AND position.parity_user_id = %s
                      AND mark.mark_type = 'DAILY_CLOSE'
                      AND mark.market_date = %s
                    LIMIT 1
                    """,
                    (
                        protected_lot_id,
                        parity_user_id,
                        market_date,
                    ),
                )

                mark = cur.fetchone()

            if not mark:
                raise ValueError(
                    "Active protected position was not found"
                )

            conn.commit()

    return mark

def list_active_protected_positions_for_reconciliation(
    *,
    limit: int = 5000,
) -> list[dict]:
    """
    Return every active protected position needed for broker
    reconciliation.

    This is database-only and does not contact the brokerage.
    """

    if limit <= 0 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    parity_user_id,
                    account_id,
                    underlying_symbol,
                    share_quantity,
                    strategy_type,
                    option_contracts,
                    protection_opened_at
                FROM protected_position_lots
                WHERE status = 'ACTIVE'
                ORDER BY
                    parity_user_id,
                    account_id,
                    underlying_symbol,
                    protection_opened_at,
                    id
                LIMIT %s
                """,
                (limit,),
            )

            return cur.fetchall()


def list_active_positions_for_daily_mark(
    *,
    market_date: date,
    limit: int = 500,
) -> list[dict]:
    """
    Return active protected positions that do not yet have an official
    daily closing mark for the requested market date.

    This is database-only and does not contact the broker.
    """

    if limit <= 0 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    position.id,
                    position.parity_user_id,
                    position.account_id,
                    position.underlying_symbol,
                    position.strategy_type,
                    position.share_quantity,
                    position.protection_opened_at

                FROM protected_position_lots position

                WHERE position.status = 'ACTIVE'
                  AND position.entry_strategy_value IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM protected_position_marks mark
                      WHERE mark.protected_lot_id = position.id
                        AND mark.mark_type = 'DAILY_CLOSE'
                        AND mark.market_date = %s
                  )

                ORDER BY
                    position.protection_opened_at ASC,
                    position.id ASC

                LIMIT %s
                """,
                (
                    market_date,
                    limit,
                ),
            )

            return cur.fetchall()


def mark_protected_position_lot_closed(
    *,
    parity_user_id: str,
    opening_workflow_lot_id: str,
) -> dict | None:
    """
    Mark one Parity-tracked protection lot closed after its complete
    options-close order is broker-confirmed FILLED.

    This is idempotent.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE protected_position_lots
                SET
                    status = 'CLOSED',
                    closed_at = COALESCE(closed_at, NOW()),
                    updated_at = NOW()
                WHERE opening_workflow_lot_id = %s
                  AND parity_user_id = %s
                  AND status <> 'CLOSED'
                RETURNING *
                """,
                (
                    opening_workflow_lot_id,
                    parity_user_id,
                ),
            )

            protected_lot = cur.fetchone()

            if protected_lot is None:
                cur.execute(
                    """
                    SELECT *
                    FROM protected_position_lots
                    WHERE opening_workflow_lot_id = %s
                      AND parity_user_id = %s
                    """,
                    (
                        opening_workflow_lot_id,
                        parity_user_id,
                    ),
                )
                protected_lot = cur.fetchone()

            conn.commit()

    return protected_lot




def get_protected_position_lot(
    *,
    parity_user_id: str,
    protected_lot_id: str,
) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM protected_position_lots
                WHERE id = %s
                  AND parity_user_id = %s
                """,
                (
                    protected_lot_id,
                    parity_user_id,
                ),
            )

            return cur.fetchone()


def create_protected_position_exit(
    *,
    parity_user_id: str,
    protected_lot_id: str,
    exit_mode: str,
) -> dict:
    """
    Create one approved exit intent for a protected lot.

    This is idempotent while an exit remains active.
    """

    if exit_mode not in {
        "REMOVE_PROTECTION",
        "SELL_PROTECTED_POSITION",
    }:
        raise ValueError("Invalid protected-position exit mode")

    active_statuses = {
        "APPROVED",
        "OPTIONS_CLOSE_SUBMITTED",
        "AWAITING_OPTIONS_FILL",
        "EQUITY_SALE_SUBMITTED",
        "ACTION_REQUIRED",
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM protected_position_lots
                WHERE id = %s
                  AND parity_user_id = %s
                FOR UPDATE
                """,
                (
                    protected_lot_id,
                    parity_user_id,
                ),
            )
            protected_lot = cur.fetchone()

            if not protected_lot:
                raise ValueError("Protected position lot was not found")

            if protected_lot["status"] == "CLOSED":
                raise ValueError(
                    "This protected position lot is already closed"
                )

            cur.execute(
                """
                SELECT *
                FROM protected_position_exits
                WHERE protected_lot_id = %s
                  AND parity_user_id = %s
                ORDER BY created_at DESC
                FOR UPDATE
                """,
                (
                    protected_lot_id,
                    parity_user_id,
                ),
            )
            exits = cur.fetchall()

            existing_exit = next(
                (
                    exit_record
                    for exit_record in exits
                    if exit_record["status"] in active_statuses
                ),
                None,
            )

            if existing_exit:
                if existing_exit["exit_mode"] != exit_mode:
                    raise ValueError(
                        "A different exit is already active for this "
                        "protected lot"
                    )

                conn.commit()
                return existing_exit

            if exit_mode == "SELL_PROTECTED_POSITION":
                cur.execute(
                    """
                    UPDATE protected_position_lots
                    SET
                        status = 'EXITING',
                        updated_at = NOW()
                    WHERE id = %s
                      AND parity_user_id = %s
                    """,
                    (
                        protected_lot_id,
                        parity_user_id,
                    ),
                )

            cur.execute(
                """
                INSERT INTO protected_position_exits (
                    protected_lot_id,
                    parity_user_id,
                    exit_mode
                )
                VALUES (%s, %s, %s)
                RETURNING *
                """,
                (
                    protected_lot_id,
                    parity_user_id,
                    exit_mode,
                ),
            )
            exit_record = cur.fetchone()
            conn.commit()

    return exit_record


def get_protected_position_exit(
    *,
    parity_user_id: str,
    exit_id: str,
) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM protected_position_exits
                WHERE id = %s
                  AND parity_user_id = %s
                """,
                (
                    exit_id,
                    parity_user_id,
                ),
            )

            return cur.fetchone()


def get_protected_position_exit_for_options_close_order(
    *,
    parity_user_id: str,
    options_close_order_id: str,
) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM protected_position_exits
                WHERE options_close_order_id = %s
                  AND parity_user_id = %s
                """,
                (
                    options_close_order_id,
                    parity_user_id,
                ),
            )

            return cur.fetchone()


def get_protected_position_exit_for_equity_sale_order(
    *,
    parity_user_id: str,
    equity_sale_order_id: str,
) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM protected_position_exits
                WHERE equity_sale_order_id = %s
                  AND parity_user_id = %s
                """,
                (
                    equity_sale_order_id,
                    parity_user_id,
                ),
            )

            return cur.fetchone()


def attach_options_close_order_to_protected_position_exit(
    *,
    parity_user_id: str,
    exit_id: str,
    options_close_order_id: str,
) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE protected_position_exits
                SET
                    options_close_order_id = %s,
                    status = 'OPTIONS_CLOSE_SUBMITTED',
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status IN (
                      'APPROVED',
                      'OPTIONS_CLOSE_SUBMITTED'
                  )
                RETURNING *
                """,
                (
                    options_close_order_id,
                    exit_id,
                    parity_user_id,
                ),
            )
            exit_record = cur.fetchone()

            if not exit_record:
                raise ValueError(
                    "Protected-position exit is not available for "
                    "an options-close order"
                )

            conn.commit()

    return exit_record


def attach_equity_sale_order_to_protected_position_exit(
    *,
    parity_user_id: str,
    exit_id: str,
    equity_sale_order_id: str,
) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE protected_position_exits
                SET
                    equity_sale_order_id = %s,
                    status = 'EQUITY_SALE_SUBMITTED',
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status IN (
                      'AWAITING_OPTIONS_FILL',
                      'EQUITY_SALE_SUBMITTED'
                  )
                RETURNING *
                """,
                (
                    equity_sale_order_id,
                    exit_id,
                    parity_user_id,
                ),
            )
            exit_record = cur.fetchone()

            if not exit_record:
                raise ValueError(
                    "Protected-position exit is not available for "
                    "an equity-sale order"
                )

            conn.commit()

    return exit_record


def update_protected_position_exit_status(
    *,
    parity_user_id: str,
    exit_id: str,
    status: str,
) -> dict:
    allowed_statuses = {
        "APPROVED",
        "OPTIONS_CLOSE_SUBMITTED",
        "AWAITING_OPTIONS_FILL",
        "EQUITY_SALE_SUBMITTED",
        "COMPLETE",
        "ACTION_REQUIRED",
        "CANCELED",
    }

    if status not in allowed_statuses:
        raise ValueError("Invalid protected-position exit status")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE protected_position_exits
                SET
                    status = %s,
                    completed_at = CASE
                        WHEN %s = 'COMPLETE'
                        THEN COALESCE(completed_at, NOW())
                        ELSE completed_at
                    END,
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                RETURNING *
                """,
                (
                    status,
                    status,
                    exit_id,
                    parity_user_id,
                ),
            )
            exit_record = cur.fetchone()

            if not exit_record:
                raise ValueError("Protected-position exit was not found")

            conn.commit()

    return exit_record


def update_protected_position_lot_status(
    *,
    parity_user_id: str,
    protected_lot_id: str,
    status: str,
) -> dict:
    allowed_statuses = {
        "ACTIVE",
        "EXITING",
        "RECONCILIATION_REQUIRED",
        "CLOSED",
    }

    if status not in allowed_statuses:
        raise ValueError("Invalid protected-position lot status")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE protected_position_lots
                SET
                    status = %s,
                    closed_at = CASE
                        WHEN %s = 'CLOSED'
                        THEN COALESCE(closed_at, NOW())
                        ELSE closed_at
                    END,
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                RETURNING *
                """,
                (
                    status,
                    status,
                    protected_lot_id,
                    parity_user_id,
                ),
            )
            protected_lot = cur.fetchone()

            if not protected_lot:
                raise ValueError("Protected position lot was not found")

            conn.commit()

    return protected_lot



def list_reconcilable_execution_orders(
    *,
    limit: int = 100,
) -> list[dict]:
    """
    Return broker-acknowledged orders whose local state may still
    require reconciliation.

    This does not claim, submit, cancel, or modify any order.
    """

    if limit <= 0 or limit > 500:
        raise ValueError("limit must be between 1 and 500")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    parity_user_id,
                    workflow_id,
                    lot_id,
                    order_scope,
                    execution_phase,
                    status,
                    broker_order_id,
                    updated_at
                FROM execution_orders
                WHERE broker_order_id IS NOT NULL
                  AND status IN (
                      'SUBMITTED',
                      'WORKING',
                      'PARTIALLY_FILLED',
                      'CANCELING'
                  )
                ORDER BY
                broker_status_checked_at ASC NULLS FIRST,
                created_at ASC
                LIMIT %s
                """,
                (limit,),
            )

            return cur.fetchall()


def mark_execution_order_status_checked(
    *,
    order_id: str,
) -> None:
    """
    Record when the reconciler last checked an order at the broker.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_orders
                SET broker_status_checked_at = NOW()
                WHERE id = %s
                """,
                (order_id,),
            )
            conn.commit()

            
def get_execution_order(
    parity_user_id: str,
    order_id: str,
) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM execution_orders
                WHERE id = %s
                  AND parity_user_id = %s
                """,
                (
                    order_id,
                    parity_user_id,
                ),
            )

            return cur.fetchone()







def get_execution_order_replacement(
    parity_user_id: str,
    original_order_id: str,
) -> dict | None:
    """
    Return the replacement created for an original execution order.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM execution_orders
                WHERE parity_user_id = %s
                  AND replaces_order_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    parity_user_id,
                    original_order_id,
                ),
            )

            return cur.fetchone()



            

def mark_execution_order_prepared(
    parity_user_id: str,
    order_id: str,
) -> dict:
    """
    Atomically promote one validated DRAFT to PREPARED.

    This does not call a broker or submit an order.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_orders
                SET status = 'PREPARED',
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status = 'DRAFT'
                RETURNING *
                """,
                (
                    order_id,
                    parity_user_id,
                ),
            )

            prepared_order = cur.fetchone()

            if prepared_order:
                conn.commit()
                return prepared_order

            cur.execute(
                """
                SELECT status
                FROM execution_orders
                WHERE id = %s
                  AND parity_user_id = %s
                """,
                (
                    order_id,
                    parity_user_id,
                ),
            )

            existing_order = cur.fetchone()

            if not existing_order:
                raise ValueError("Execution order was not found")

            raise ValueError(
                "Only DRAFT execution orders can be prepared; "
                f"current status is {existing_order['status']}"
            )



def claim_execution_order_submission(
    parity_user_id: str,
    order_id: str,
) -> dict:
    """
    Atomically claim a PREPARED order for broker submission.

    Once claimed, duplicate requests cannot submit the same order again.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_orders eo
                SET
                    status = 'SUBMITTING',
                    updated_at = NOW()
                FROM execution_workflows ew
                WHERE eo.id = %s
                  AND eo.parity_user_id = %s
                  AND eo.status = 'PREPARED'
                  AND ew.id = eo.workflow_id
                  AND ew.status <> 'CANCELED'
                RETURNING eo.*
                """,
                (
                    order_id,
                    parity_user_id,
                ),
            )

            submitting_order = cur.fetchone()

            if submitting_order:
                conn.commit()
                return submitting_order

            cur.execute(
                """
                SELECT status
                FROM execution_orders
                WHERE id = %s
                  AND parity_user_id = %s
                """,
                (
                    order_id,
                    parity_user_id,
                ),
            )

            existing_order = cur.fetchone()

            if not existing_order:
                raise ValueError(
                    "Execution order was not found"
                )

            if existing_order["status"] == "SUBMITTING":
                raise ValueError(
                    "Execution order is already being submitted"
                )

            raise ValueError(
                "Only PREPARED orders can be submitted; "
                f"current status is {existing_order['status']}"
            )

def claim_execution_order_cancellation(
    parity_user_id: str,
    order_id: str,
) -> dict:
    """
    Atomically claim one working order for explicit cancellation.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_orders
                SET
                    status = 'CANCELING',
                    last_checked_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status IN (
                      'SUBMITTED',
                      'WORKING',
                      'PARTIALLY_FILLED',
                      'ACTION_REQUIRED'
                  )
                RETURNING *
                """,
                (
                    order_id,
                    parity_user_id,
                ),
            )

            order = cur.fetchone()

            if not order:
                raise ValueError(
                    "Only working orders can be canceled"
                )

            conn.commit()

    return order

def record_execution_order_cancellation_request(
    *,
    parity_user_id: str,
    order_id: str,
    broker_response: dict,
) -> dict:
    """
    Store the broker's acknowledgement of a cancellation request while
    preserving the original submission response and child-order IDs.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_orders
                SET
                    broker_response = COALESCE(
                        broker_response,
                        '{}'::jsonb
                    ) || jsonb_build_object(
                        'cancellation',
                        %s::jsonb
                    ),
                    last_checked_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status = 'CANCELING'
                RETURNING *
                """,
                (
                    json.dumps(broker_response),
                    order_id,
                    parity_user_id,
                ),
            )

            order = cur.fetchone()

            if not order:
                raise ValueError(
                    "Only CANCELING orders can record cancellation"
                )

            conn.commit()

    return order




def mark_execution_order_cancellation_action_required(
    *,
    parity_user_id: str,
    order_id: str,
    reason: str,
    broker_response: dict,
) -> dict:
    """
    Preserve cancellation evidence and require human review when its
    broker outcome is uncertain.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_orders
                SET
                    status = 'ACTION_REQUIRED',
                    rejection_reason = %s,
                    broker_response = COALESCE(
                        broker_response,
                        '{}'::jsonb
                    ) || jsonb_build_object(
                        'cancellation',
                        %s::jsonb
                    ),
                    last_checked_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status = 'CANCELING'
                RETURNING *
                """,
                (
                    reason,
                    json.dumps(broker_response),
                    order_id,
                    parity_user_id,
                ),
            )

            order = cur.fetchone()

            if not order:
                raise ValueError(
                    "Only CANCELING orders can require review"
                )

            conn.commit()

    return order


def mark_execution_order_submitted(
    parity_user_id: str,
    order_id: str,
    broker_response: dict,
    broker_order_id: str | None = None,
) -> dict:
    """
    Persist SnapTrade's acknowledgement only after a broker submission
    has been accepted.

    This function does not call SnapTrade.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_orders
                SET
                    status = 'SUBMITTED',
                    broker_order_id = %s,
                    broker_response = %s::jsonb,
                    submitted_at = NOW(),
                    last_checked_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status = 'SUBMITTING'
                RETURNING *
                """,
                (
                    broker_order_id,
                    json.dumps(broker_response),
                    order_id,
                    parity_user_id,
                ),
            )

            submitted_order = cur.fetchone()

            if not submitted_order:
                cur.execute(
                    """
                    SELECT status
                    FROM execution_orders
                    WHERE id = %s
                      AND parity_user_id = %s
                    """,
                    (order_id, parity_user_id),
                )

                existing_order = cur.fetchone()

                if not existing_order:
                    raise ValueError(
                        "Execution order was not found"
                    )

                raise ValueError(
                    "Only SUBMITTING orders can be marked submitted"
                )

            conn.commit()

    return submitted_order


def mark_execution_order_action_required(
    parity_user_id: str,
    order_id: str,
    reason: str,
    broker_response: dict | None = None,
) -> dict:
    """
    Preserve an ambiguous broker-submission outcome for human review.

    This is intentionally not returned to PREPARED because the broker may
    have received the order even when the API call did not return normally.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_orders
                SET
                    status = 'ACTION_REQUIRED',
                    rejection_reason = %s,
                    broker_response = %s::jsonb,
                    last_checked_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status = 'SUBMITTING'
                RETURNING *
                """,
                (
                    reason,
                    json.dumps(broker_response or {}),
                    order_id,
                    parity_user_id,
                ),
            )

            action_required_order = cur.fetchone()

            if not action_required_order:
                raise ValueError(
                    "Only SUBMITTING orders can require action"
                )

            conn.commit()

    return action_required_order

def update_execution_order_broker_status(
    parity_user_id: str,
    order_id: str,
    status: str,
    filled_quantity: float,
    average_fill_price: float | None,
    broker_response: dict,
    rejection_reason: str | None = None,
    broker_executed_at: datetime | str | None = None,
) -> dict:
    """
    Save the latest broker-reported state for one submitted order.

    This records status only. It does not submit, cancel, replace, or
    advance any later workflow step.
    """

    allowed_statuses = {
        "SUBMITTED",
        "WORKING",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELED",
        "CANCELING",
        "EXPIRED",
        "REJECTED",
        "REQUOTE_REQUIRED",
        "ACTION_REQUIRED",
    }

    if status not in allowed_statuses:
        raise ValueError(
            f"Invalid execution order status: {status}"
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_orders
                SET
                    status = %s,
                    filled_quantity = %s,
                    average_fill_price = %s,
                    broker_response = %s::jsonb,
                    rejection_reason = %s,
                    last_checked_at = NOW(),
                    filled_at = CASE
                        WHEN %s = 'FILLED' THEN COALESCE(
                            filled_at,
                            %s::timestamptz,
                            NOW()
                        )
                        ELSE filled_at
                    END,
                    canceled_at = CASE
                        WHEN %s IN (
                            'CANCELED',
                            'EXPIRED',
                            'REJECTED'
                        ) THEN NOW()
                        ELSE canceled_at
                    END,
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status IN (
                      'SUBMITTED',
                      'WORKING',
                      'CANCELING',
                      'PARTIALLY_FILLED',
                      'ACTION_REQUIRED'
                  )
                RETURNING *
                """,
                (
                    status,
                    filled_quantity,
                    average_fill_price,
                    json.dumps(broker_response),
                    rejection_reason,
                    status,
                    broker_executed_at,
                    status,
                    order_id,
                    parity_user_id,
                ),
            )

            updated_order = cur.fetchone()

            if not updated_order:
                raise ValueError(
                    "Only submitted execution orders can be reconciled"
                )

            conn.commit()

    return updated_order








def close_reconciliation_required_position(
    *,
    parity_user_id: str,
    protected_lot_id: str,
) -> dict:
    """
    Close a reconciliation-required position after the user explicitly
    confirms it is no longer held.

    This never calls the broker and never submits or cancels an order.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE protected_position_lots
                SET
                    status = 'CLOSED',
                    closed_at = COALESCE(closed_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND status = 'RECONCILIATION_REQUIRED'
                RETURNING *
                """,
                (
                    protected_lot_id,
                    parity_user_id,
                ),
            )

            position = cur.fetchone()

            if not position:
                raise ValueError(
                    "Only a reconciliation-required position "
                    "can be marked as no longer held"
                )

            conn.commit()

    return position


    
def advance_execution_lot_after_fill(
    *,
    parity_user_id: str,
    workflow_id: str,
    lot_id: str,
    is_final_step: bool,
) -> dict:
    """
    Advance one lot after a child order has been broker-confirmed FILLED.
    """

    next_status = (
        "COMPLETE"
        if is_final_step
        else "WAITING_FOR_NEXT_STEP"
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_workflow_lots l
                SET
                    status = %s,
                    completed_at = CASE
                        WHEN %s THEN NOW()
                        ELSE completed_at
                    END,
                    updated_at = NOW()
                FROM execution_workflows w
                WHERE l.id = %s
                  AND l.workflow_id = %s
                  AND w.id = l.workflow_id
                  AND w.parity_user_id = %s
                RETURNING l.*
                """,
                (
                    next_status,
                    is_final_step,
                    lot_id,
                    workflow_id,
                    parity_user_id,
                ),
            )

            lot = cur.fetchone()

            if not lot:
                raise ValueError("Execution lot was not found")

            conn.commit()

    return lot

def mark_execution_workflow_complete_if_all_lots_complete(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> dict | None:
    """
    Mark a workflow COMPLETE only after every one of its lots is COMPLETE.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_workflows w
                SET
                    status = 'COMPLETE',
                    updated_at = NOW()
                WHERE w.id = %s
                  AND w.parity_user_id = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM execution_workflow_lots l
                      WHERE l.workflow_id = w.id
                        AND l.status <> 'COMPLETE'
                  )
                RETURNING w.*
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            workflow = cur.fetchone()
            conn.commit()

    return workflow


def abandon_execution_workflow(
    parity_user_id: str,
    workflow_id: str,
) -> dict:
    """
    Permanently abandon an unfilled execution workflow.

    This never calls a broker. It refuses to release reservations if
    any order is active, uncertain, or has filled quantity.
    """

    active_or_uncertain_statuses = {
        "SUBMITTING",
        "SUBMITTED",
        "WORKING",
        "PARTIALLY_FILLED",
        "CANCELING",
        "ACTION_REQUIRED",
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM execution_workflows
                WHERE id = %s
                  AND parity_user_id = %s
                FOR UPDATE
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )
            workflow = cur.fetchone()

            if not workflow:
                raise ValueError("Execution workflow was not found")

            if workflow["status"] == "COMPLETE":
                raise ValueError(
                    "Completed workflows cannot be abandoned"
                )

            if workflow["status"] == "CANCELED":
                return workflow

            cur.execute(
                """
                SELECT status, filled_quantity
                FROM execution_orders
                WHERE workflow_id = %s
                  AND parity_user_id = %s
                FOR UPDATE
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )
            orders = cur.fetchall()

            has_fills = any(
                float(order["filled_quantity"] or 0) > 0
                for order in orders
            )
            has_active_or_uncertain_order = any(
                order["status"] in active_or_uncertain_statuses
                for order in orders
            )

            if has_fills or has_active_or_uncertain_order:
                raise ValueError(
                    "A workflow can be abandoned only after every "
                    "order is terminal, confirmed, and unfilled"
                )

            cur.execute(
                """
                UPDATE execution_orders
                SET
                    status = 'CANCELED',
                    canceled_at = COALESCE(canceled_at, NOW()),
                    updated_at = NOW()
                WHERE workflow_id = %s
                  AND parity_user_id = %s
                  AND status IN (
                      'DRAFT',
                      'PREPARED',
                      'REQUOTE_REQUIRED'
                  )
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            cur.execute(
                """
                UPDATE execution_workflow_lots
                SET
                    reserved_share_quantity = 0,
                    status = 'CANCELED',
                    updated_at = NOW()
                WHERE workflow_id = %s
                  AND status <> 'COMPLETE'
                """,
                (workflow_id,),
            )

            cur.execute(
                """
                UPDATE execution_workflows
                SET
                    status = 'CANCELED',
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                RETURNING *
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )
            abandoned_workflow = cur.fetchone()

            conn.commit()

            return abandoned_workflow

def resolve_execution_workflow_attention(
    *,
    parity_user_id: str,
    workflow_id: str,
    resolution_code: str,
    resolution_note: str | None = None,
) -> dict:
    """
    Record that a user has reviewed an attention-required workflow.

    This preserves every workflow and order status. It never claims
    that an ambiguous order was canceled, never calls the broker, and
    never places, cancels, replaces, or closes an order.
    """

    allowed_resolution_codes = {
        "NO_ACTIVE_BROKER_ORDER",
        "POSITION_NO_LONGER_HELD",
        "POSITION_REVIEWED_UNPROTECTED",
        "DUPLICATE_OR_TEST_WORKFLOW",
    }

    if resolution_code not in allowed_resolution_codes:
        raise ValueError(
            "Invalid workflow attention resolution"
        )

    normalized_note = (
        resolution_note.strip()
        if resolution_note
        else None
    )

    if normalized_note and len(normalized_note) > 500:
        raise ValueError(
            "Resolution note cannot exceed 500 characters"
        )

    active_order_statuses = {
        "SUBMITTING",
        "SUBMITTED",
        "WORKING",
        "PARTIALLY_FILLED",
        "CANCELING",
    }

    attention_order_statuses = {
        "ACTION_REQUIRED",
        "REJECTED",
        "REQUOTE_REQUIRED",
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM execution_workflows
                WHERE id = %s
                  AND parity_user_id = %s
                FOR UPDATE
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            workflow = cur.fetchone()

            if not workflow:
                raise ValueError(
                    "Execution workflow was not found"
                )

            if workflow.get("attention_resolved_at"):
                return workflow

            cur.execute(
                """
                SELECT
                    id,
                    status,
                    broker_order_id,
                    filled_quantity
                FROM execution_orders
                WHERE workflow_id = %s
                  AND parity_user_id = %s
                FOR UPDATE
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            orders = cur.fetchall()

            has_attention_state = (
                workflow["status"] == "ACTION_REQUIRED"
                or any(
                    order["status"]
                    in attention_order_statuses
                    for order in orders
                )
            )

            if not has_attention_state:
                raise ValueError(
                    "This workflow does not require attention"
                )

            has_active_order = any(
                order["status"] in active_order_statuses
                for order in orders
            )

            if has_active_order:
                raise ValueError(
                    "Active brokerage orders must reach a terminal "
                    "status before this review can be resolved"
                )

            has_uncertain_broker_linked_order = any(
                order["status"] == "ACTION_REQUIRED"
                and order.get("broker_order_id")
                for order in orders
            )

            if has_uncertain_broker_linked_order:
                raise ValueError(
                    "A broker-linked uncertain order must be "
                    "reconciled before this review can be resolved"
                )

            cur.execute(
                """
                UPDATE execution_workflow_lots
                SET
                    reserved_share_quantity = 0,
                    updated_at = NOW()
                WHERE workflow_id = %s
                  AND status <> 'COMPLETE'
                """,
                (workflow_id,),
            )

            cur.execute(
                """
                UPDATE execution_workflows
                SET
                    attention_resolved_at = NOW(),
                    attention_resolution_code = %s,
                    attention_resolution_note = %s,
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                RETURNING *
                """,
                (
                    resolution_code,
                    normalized_note,
                    workflow_id,
                    parity_user_id,
                ),
            )

            resolved_workflow = cur.fetchone()

            if not resolved_workflow:
                raise ValueError(
                    "Workflow attention review could not be resolved"
                )

            conn.commit()

    return resolved_workflow


def claim_execution_workflow_unwind(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> dict:
    """
    Atomically record the user's request to stop a new-position
    workflow and sell only the shares acquired by that workflow.

    Repeated requests return the existing unwind state and do not
    create another unwind.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM execution_workflows
                WHERE id = %s
                  AND parity_user_id = %s
                FOR UPDATE
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            workflow = cur.fetchone()

            if not workflow:
                raise ValueError(
                    "Execution workflow was not found"
                )

            if workflow["underlying_source"] != "new":
                raise ValueError(
                    "Only new-position workflows can use "
                    "abort-and-sell"
                )

            if workflow.get("unwind_status"):
                conn.commit()
                return workflow

            if workflow["status"] == "COMPLETE":
                raise ValueError(
                    "This position is already complete and must use "
                    "the protected-position exit flow"
                )

            if workflow["status"] in {
                "DRAFT",
                "UNDERLYING_ORDER_PREPARED",
            }:
                raise ValueError(
                    "No shares have been submitted for this workflow"
                )

            cur.execute(
                """
                SELECT id
                FROM protected_position_lots
                WHERE opening_workflow_id = %s
                  AND parity_user_id = %s
                  AND status <> 'CLOSED'
                LIMIT 1
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            if cur.fetchone():
                raise ValueError(
                    "This workflow already created a protected "
                    "position and must use the protected-position "
                    "exit flow"
                )

            cur.execute(
                """
                SELECT id
                FROM execution_orders
                WHERE workflow_id = %s
                  AND parity_user_id = %s
                  AND order_role = 'BUY_UNDERLYING'
                  AND broker_order_id IS NOT NULL
                LIMIT 1
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            if not cur.fetchone():
                raise ValueError(
                    "The workflow does not have a submitted share "
                    "purchase to unwind"
                )

            cur.execute(
                """
                UPDATE execution_workflows
                SET
                    unwind_requested_at = NOW(),
                    unwind_status = 'REQUESTED',
                    unwind_final_share_quantity = NULL,
                    unwind_sell_order_id = NULL,
                    unwind_error = NULL,
                    unwind_completed_at = NULL,
                    unwind_broker_snapshot = '{}'::jsonb,
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND unwind_status IS NULL
                RETURNING *
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            claimed_workflow = cur.fetchone()

            if not claimed_workflow:
                raise ValueError(
                    "The workflow unwind could not be claimed"
                )

            conn.commit()

    return claimed_workflow


def update_execution_workflow_unwind(
    *,
    parity_user_id: str,
    workflow_id: str,
    unwind_status: str,
    final_share_quantity: int | None = None,
    sell_order_id: str | None = None,
    error: str | None = None,
    broker_snapshot: dict | None = None,
) -> dict:
    """
    Persist one step of an already requested workflow unwind.
    """

    allowed_statuses = {
        "REQUESTED",
        "CANCELING_ORDERS",
        "READY_TO_SELL",
        "SELL_SUBMITTED",
        "COMPLETE",
        "ACTION_REQUIRED",
    }

    if unwind_status not in allowed_statuses:
        raise ValueError(
            f"Unsupported workflow unwind status: {unwind_status}"
        )

    if (
        final_share_quantity is not None
        and final_share_quantity < 0
    ):
        raise ValueError(
            "Final unwind share quantity cannot be negative"
        )

    snapshot_payload = broker_snapshot or {}

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE execution_workflows
                SET
                    unwind_status = %s,
                    unwind_final_share_quantity = COALESCE(
                        %s,
                        unwind_final_share_quantity
                    ),
                    unwind_sell_order_id = COALESCE(
                        %s,
                        unwind_sell_order_id
                    ),
                    unwind_error = %s,
                    unwind_broker_snapshot = COALESCE(
                        unwind_broker_snapshot,
                        '{}'::jsonb
                    ) || %s::jsonb,
                    unwind_completed_at = CASE
                        WHEN %s = 'COMPLETE'
                        THEN COALESCE(
                            unwind_completed_at,
                            NOW()
                        )
                        ELSE unwind_completed_at
                    END,
                    status = CASE
                        WHEN %s = 'COMPLETE'
                        THEN 'CANCELED'
                        ELSE status
                    END,
                    updated_at = NOW()
                WHERE id = %s
                  AND parity_user_id = %s
                  AND unwind_requested_at IS NOT NULL
                RETURNING *
                """,
                (
                    unwind_status,
                    final_share_quantity,
                    sell_order_id,
                    error,
                    json.dumps(snapshot_payload),
                    unwind_status,
                    unwind_status,
                    workflow_id,
                    parity_user_id,
                ),
            )

            updated_workflow = cur.fetchone()

            if not updated_workflow:
                raise ValueError(
                    "Requested workflow unwind was not found"
                )
            if unwind_status == "COMPLETE":
                cur.execute(
                    """
                    UPDATE execution_workflow_lots
                    SET
                        status = CASE
                            WHEN status = 'COMPLETE'
                            THEN status
                            ELSE 'CANCELED'
                        END,
                        reserved_share_quantity = 0,
                        completed_at = COALESCE(
                            completed_at,
                            NOW()
                        ),
                        updated_at = NOW()
                    WHERE workflow_id = %s
                    """,
                    (workflow_id,),
                )
            conn.commit()

    return updated_workflow

def cancel_unsubmitted_orders_for_workflow_unwind(
    *,
    parity_user_id: str,
    workflow_id: str,
) -> list[dict]:
    """
    Cancel local drafts that never reached the broker after the user
    requested a workflow unwind.

    This never changes an order that has a brokerage order ID and
    never calls SnapTrade.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM execution_workflows
                WHERE id = %s
                  AND parity_user_id = %s
                FOR UPDATE
                """,
                (
                    workflow_id,
                    parity_user_id,
                ),
            )

            workflow = cur.fetchone()

            if not workflow:
                raise ValueError(
                    "Execution workflow was not found"
                )

            if not workflow.get("unwind_requested_at"):
                raise ValueError(
                    "The workflow does not have an unwind request"
                )

            cur.execute(
                """
                UPDATE execution_orders
                SET
                    status = 'CANCELED',
                    canceled_at = COALESCE(
                        canceled_at,
                        NOW()
                    ),
                    rejection_reason = (
                        'Canceled before broker submission because '
                        'the user requested a workflow unwind'
                    ),
                    updated_at = NOW()
                WHERE workflow_id = %s
                  AND parity_user_id = %s
                  AND broker_order_id IS NULL
                  AND status IN (
                      'DRAFT',
                      'PREPARED'
                  )
                  AND (
                      %s::uuid IS NULL
                      OR id <> %s::uuid
                  )
                RETURNING *
                """,
                (
                    workflow_id,
                    parity_user_id,
                    workflow.get("unwind_sell_order_id"),
                    workflow.get("unwind_sell_order_id"),
                ),
            )

            canceled_orders = cur.fetchall()

            conn.commit()

    return canceled_orders