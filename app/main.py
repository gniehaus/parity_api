import os
from typing import Any, Dict, List, Literal, Optional
from fastapi.responses import PlainTextResponse
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from snaptrade_client import SnapTrade
from .expense_ratio_service import get_expense_ratio
from pydantic import BaseModel, Field
from .auth import get_parity_user_id

from .subscriptions import (
    router as subscriptions_router,
    require_subscription_feature,
)

from .execution_status import (
    ExecutionStatusError,
    refresh_execution_order_status,
)

from .execution_closing import (
    prepare_close_options_overlay_draft,
)


from .defined_outcome_service import (
    choose_defined_outcome_match,
    get_defined_outcome,
choose_defined_floor_match,
 inspect_table,
)
from .execution_plan import build_execution_plan

from .execution_safety import (
    ExecutionSafetyError,
    validate_execution_order_safety,
)
from .advisory import router as advisory_router
from .execution_preparation import (
    prepare_equity_order_draft,
    prepare_option_order_draft,
)


from .execution_submission import (
    ExecutionSubmissionError,
    submit_prepared_option_order,
)

from .execution_cancellation import (
    ExecutionCancellationError,
    request_execution_order_cancellation,
)


from .db import (
    init_db,
    upsert_parity_user,
    get_conn,
    get_investor_profile,
    save_investor_profile_and_invalidate_recommendations,
    persist_recommendation_run,
    get_current_recommendation_run,
    create_execution_workflow,
    create_execution_workflow_lots,
    get_execution_workflow,
    get_execution_workflow_lots,
    save_execution_workflow_plan,
    get_execution_workflow_orders,
    get_execution_order,
    mark_execution_order_prepared,
    abandon_execution_workflow,
)

from .snaptrade_service import (
    create_connection_url,
    list_accounts,
    get_account_positions,
    sync_brokerage_accounts_and_holdings,
    get_portfolio_summary,
    get_dashboard_holdings_for_metrics,
    get_account_level_portfolio_summary,
    get_execution_account_context
)
from .plaid_service import (
    create_link_token,
    exchange_public_token,
    sync_bank_accounts,
    get_bank_accounts_from_db,
)
from .portfolio_dashboard_engine import calculate_portfolio_dashboard


from contextlib import asynccontextmanager

from .mcp_server import parity_mcp


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    async with parity_mcp.session_manager.run():
        yield


app = FastAPI(
    title="Parity SnapTrade API",
    lifespan=lifespan,
)


# Public OAuth metadata used by ChatGPT to authenticate with Parity.
@app.get("/.well-known/oauth-protected-resource/mcp/")
@app.get("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource_metadata():
    return {
        "resource": "https://mcp.parityoutcomes.com/mcp/",
        "authorization_servers": [
    "https://clerk.parityoutcomes.com"
        ],
        "bearer_methods_supported": ["header"],
    }


app.include_router(advisory_router)
app.include_router(subscriptions_router)


@app.get(
    "/.well-known/openai-apps-challenge",
    response_class=PlainTextResponse,
)
async def openai_apps_challenge():
    token = os.getenv("OPENAI_APPS_CHALLENGE_TOKEN")

    if not token:
        raise HTTPException(
            status_code=404,
            detail="Verification token not configured",
        )

    return token


app.mount(
    "/mcp",
    parity_mcp.streamable_http_app(),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

snaptrade = SnapTrade(
    client_id=os.getenv("SNAPTRADE_CLIENT_ID"),
    consumer_key=os.getenv("SNAPTRADE_CONSUMER_KEY"),
)


class ExecutionOrderSubmitRequest(BaseModel):
    confirm_submission: bool

class ExecutionOrderCancelRequest(BaseModel):
    confirm_cancellation: bool

class ExecutionCloseOptionsOverlayRequest(BaseModel):
    lot_id: str
    limit_price: float = Field(gt=0)
    price_effect: str
    time_in_force: str = "Day"


class ExecutionWorkflowAbandonRequest(BaseModel):
    confirm_abandonment: bool

class RecommendationRunRequest(BaseModel):
    engine_version: str = "v1"

    profile_version: str | None = "v1"
    profile_payload: Dict[str, Any] = Field(
        default_factory=dict
    )

    portfolio_signature: str
    portfolio_payload: Dict[str, Any] | None = None

    accounts_count: int = 0
    total_assets: float | None = None
    cash_pct: float | None = None
    portfolio_iv: float | None = None

    analysis_only: bool = False
    aggregate_benefit: float | None = None

    hero_title: str | None = None
    hero_ticker: str | None = None

    market_data_timestamp: str | None = None

    recommendations: List[Dict[str, Any]] = Field(
        default_factory=list
    )

    findings: List[Dict[str, Any]] = Field(
        default_factory=list
    )



class ExecutionWorkflowCreateRequest(BaseModel):
    account_id: str
    strategy_type: str
    underlying_source: str
    underlying_symbol: str
    underlying_shares: int
    execution_preference: Literal[
        "preserve_outcome",
        "balanced",
        "complete_sooner",
    ] = "balanced"



class ExecutionOptionContractRequest(BaseModel):
    ticker: str
    expiration: str
    option_type: str
    strike: float
    action: str


class ExecutionPrepareOptionOrderRequest(BaseModel):
    lot_id: str
    sequence: int
    contracts: list[ExecutionOptionContractRequest]
    limit_price: float = Field(gt=0)
    price_effect: str
    refresh_draft: bool = False
    time_in_force: str = "Day"

class ExecutionPrepareEquityOrderRequest(BaseModel):
    lot_id: str
    sequence: int
    time_in_force: str = "Day"


class ExpenseRatioRequest(BaseModel):
    symbols: List[str]


class RecommendRequest(BaseModel):
    holdings: List[Dict[str, Any]]
    cash: float = 0
    investment_amount: Optional[float] = None
    risk_preference: Optional[str] = "balanced"


class UserUpsertRequest(BaseModel):
    user_id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    raw: dict | None = None


from datetime import date
from pydantic import BaseModel


class InvestorProfilePayload(BaseModel):
    first_name: str
    last_name: str
    phone: str
    date_of_birth: date

    address_line1: str
    city: str
    state: str
    zip: str

    investment_objective: str
    risk_tolerance: str
    time_horizon: str

    annual_income: str
    net_worth: str
    investable_assets: str

    options_experience: str
    liquidity_needs: str
    
class PlaidExchangeRequest(BaseModel):
    public_token: str

class GuestClaimRequest(BaseModel):
    guest_id: str
    clerk_user_id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    raw: dict | None = None


@app.post("/api/guest/claim")
def claim_guest_session(req: GuestClaimRequest):
    if not req.guest_id.startswith("guest_"):
        raise HTTPException(status_code=400, detail="guest_id must start with guest_")

    if not req.clerk_user_id.startswith("user_"):
        raise HTTPException(status_code=400, detail="clerk_user_id must be a Clerk user id")

    upsert_parity_user(
        user_id=req.clerk_user_id,
        email=req.email,
        first_name=req.first_name,
        last_name=req.last_name,
        raw=req.raw,
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Prevent overwriting if Clerk user already has a SnapTrade user
            cur.execute(
                """
                SELECT parity_user_id
                FROM snaptrade_users
                WHERE parity_user_id = %s
                """,
                (req.clerk_user_id,),
            )
            existing_clerk_snaptrade = cur.fetchone()

            if existing_clerk_snaptrade:
                raise HTTPException(
                    status_code=409,
                    detail="Clerk user already has a SnapTrade connection"
                )

            cur.execute(
                """
                UPDATE snaptrade_users
                SET parity_user_id = %s
                WHERE parity_user_id = %s
                """,
                (req.clerk_user_id, req.guest_id),
            )

            cur.execute(
                """
                UPDATE brokerage_accounts
                SET parity_user_id = %s
                WHERE parity_user_id = %s
                """,
                (req.clerk_user_id, req.guest_id),
            )

            cur.execute(
                """
                UPDATE holdings
                SET parity_user_id = %s
                WHERE parity_user_id = %s
                """,
                (req.clerk_user_id, req.guest_id),
            )

            cur.execute(
                """
                UPDATE normalized_holdings
                SET parity_user_id = %s
                WHERE parity_user_id = %s
                """,
                (req.clerk_user_id, req.guest_id),
            )

            conn.commit()

    return {
        "status": "claimed",
        "guest_id": req.guest_id,
        "clerk_user_id": req.clerk_user_id,
    }


@app.post("/api/recommendation-runs")
def recommendation_run_create(
    req: RecommendationRunRequest,
    request: Request,
):
    parity_user_id = get_parity_user_id(request)

    if (
        not req.analysis_only
        and len(req.recommendations) == 0
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "A full recommendation run must contain "
                "at least one recommendation"
            ),
        )

    result = persist_recommendation_run(
        parity_user_id=parity_user_id,
        engine_version=req.engine_version,
        profile_version=req.profile_version,
        profile_payload=req.profile_payload,
        portfolio_signature=req.portfolio_signature,
        portfolio_payload=req.portfolio_payload,
        accounts_count=req.accounts_count,
        total_assets=req.total_assets,
        cash_pct=req.cash_pct,
        portfolio_iv=req.portfolio_iv,
        analysis_only=req.analysis_only,
        aggregate_benefit=req.aggregate_benefit,
        hero_title=req.hero_title,
        hero_ticker=req.hero_ticker,
        market_data_timestamp=(
            req.market_data_timestamp
        ),
        recommendations=req.recommendations,
        findings=req.findings,
    )

    return {
        "status": "saved",
        "run_id": result["run"]["id"],
        "recommendation_count": len(
            result["recommendations"]
        ),
        "finding_count": len(result["findings"]),
        **result,
    }

@app.get("/api/recommendation-runs/current")
def recommendation_run_current(request: Request):
    parity_user_id = get_parity_user_id(request)

    result = get_current_recommendation_run(
        parity_user_id
    )

    if not result:
        return {
            "exists": False,
            "run": None,
            "recommendations": [],
            "findings": [],
        }

    return {
        "exists": True,
        **result,
    }

@app.post("/api/expense-ratios")
def expense_ratios(req: ExpenseRatioRequest):
    symbols = sorted(
        {
            symbol.strip().upper()
            for symbol in req.symbols
            if symbol and symbol.strip()
        }
    )

    results = [
        get_expense_ratio(symbol)
        for symbol in symbols
    ]

    return {
        "count": len(results),
        "results": results,
    }


@app.put("/api/investor-profile")
def investor_profile_put(
    req: InvestorProfilePayload,
    request: Request,
):
    parity_user_id = get_parity_user_id(request)

    result = save_investor_profile_and_invalidate_recommendations(
        parity_user_id=parity_user_id,

        # Step 1 - Client Profile
        first_name=req.first_name,
        last_name=req.last_name,
        phone=req.phone,
        date_of_birth=req.date_of_birth,
        address_line1=req.address_line1,
        city=req.city,
        state=req.state,
        zip=req.zip,

        # Step 2 - Suitability
        investment_objective=req.investment_objective,
        risk_tolerance=req.risk_tolerance,
        time_horizon=req.time_horizon,
        annual_income=req.annual_income,
        net_worth=req.net_worth,
        investable_assets=req.investable_assets,
        options_experience=req.options_experience,
        liquidity_needs=req.liquidity_needs,

        # Backend-managed
        completed=True,
        raw=req.model_dump(mode="json"),
    )

    return {
        "status": "saved",
        **result,
    }
@app.get("/api/investor-profile")
def investor_profile_get(request: Request):
    parity_user_id = get_parity_user_id(request)

    profile = get_investor_profile(parity_user_id)

    if not profile:
        return {
            "exists": False,
            "completed": False,
            "profile": None,
        }

    return {
        "exists": True,
        "completed": bool(profile["completed"]),
        "profile": profile,
    }


from fastapi import HTTPException, Query


@app.get("/api/defined-outcomes/match")
def match_defined_outcome(
    strategy: str = Query(
        default="power_buffer",
        description=(
            "Matching strategy: power_buffer or defined_floor"
        ),
    ),
    reference_asset: str = Query(
        description="SPY, QQQ, EFA, or EEM",
    ),
    target_buffer: float | None = Query(
        default=None,
        ge=0,
        le=100,
        description=(
            "Requested remaining buffer percentage. "
            "Required when strategy=power_buffer."
        ),
    ),
    target_days_remaining: int = Query(
        default=365,
        ge=30,
        le=730,
        description=(
            "Requested remaining outcome period in days"
        ),
    ),
    maximum_buffer_difference: float = Query(
        default=10,
        ge=0,
        le=100,
        description=(
            "Maximum acceptable buffer difference for "
            "power buffer products"
        ),
    ),
    maximum_days_difference: int = Query(
        default=120,
        ge=0,
        le=365,
        description=(
            "Maximum acceptable duration difference"
        ),
    ),
    maximum_protection_gap: float | None = Query(
        default=None,
        ge=0,
        le=100,
        description=(
            "Maximum decline allowed before protection begins"
        ),
    ),
    minimum_remaining_protection: float = Query(
        default=95,
        ge=0,
        le=100,
        description=(
            "Minimum remaining protection percentage for "
            "defined floor products"
        ),
    ),
):
    try:
        normalized_strategy = strategy.strip().lower()

        if normalized_strategy not in {
            "power_buffer",
            "defined_floor",
        }:
            raise HTTPException(
                status_code=400,
                detail=(
                    "strategy must be power_buffer "
                    "or defined_floor"
                ),
            )

        if normalized_strategy == "defined_floor":
            result = choose_defined_floor_match(
                reference_asset=reference_asset,
                target_days_remaining=target_days_remaining,
                maximum_days_difference=maximum_days_difference,
                maximum_downside_before_floor=(
                    maximum_protection_gap
                    if maximum_protection_gap is not None
                    else 2.0
                ),
                minimum_remaining_protection=(
                    minimum_remaining_protection
                ),
            )

        else:
            if target_buffer is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "target_buffer is required when "
                        "strategy=power_buffer"
                    ),
                )

            result = choose_defined_outcome_match(
                reference_asset=reference_asset,
                target_buffer=target_buffer,
                target_days_remaining=target_days_remaining,
                maximum_buffer_difference=(
                    maximum_buffer_difference
                ),
                maximum_days_difference=(
                    maximum_days_difference
                ),
                maximum_protection_gap=(
                    maximum_protection_gap
                ),
            )

        if result is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No eligible defined outcome ETF meets "
                    "the requested strategy, protection, "
                    "and duration requirements."
                ),
            )

        return result

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to retrieve defined outcome "
                f"data: {exc}"
            ),
        ) from exc

@app.get("/test-defined-outcome-table")
def test_defined_outcome_table():
    return inspect_table()

from fastapi import HTTPException


@app.get("/api/defined-outcomes/{ticker}")
def defined_outcome(ticker: str):
    try:
        return get_defined_outcome(ticker)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to retrieve defined outcome data: {exc}",
        ) from exc


@app.get("/api/dashboard/risk")
def dashboard_risk(request: Request):
    parity_user_id = get_parity_user_id(request)

    holdings = get_dashboard_holdings_for_metrics(parity_user_id)

    return calculate_portfolio_dashboard(
        raw_holdings=holdings,
        years_back=1,
        risk_free_rate=0.04,
        include_implied_vol=True,
    )
    
@app.get("/")
def health():
    return {"status": "ok", "service": "parity-snaptrade-api"}


# @app.get("/api/plaid/investments/test")
# def plaid_investments_test(request: Request):
#     parity_user_id = get_parity_user_id(request)
#     return test_plaid_investments(parity_user_id)

    

@app.post("/api/users/upsert")
def users_upsert(req: UserUpsertRequest):
    upsert_parity_user(
        user_id=req.user_id,
        email=req.email,
        first_name=req.first_name,
        last_name=req.last_name,
        raw=req.raw,
    )

    return {
        "status": "ok",
        "parity_user_id": req.user_id,
    }

@app.get("/api/dashboard/accounts")
def dashboard_accounts(request: Request):
    parity_user_id = get_parity_user_id(request)

    return get_account_level_portfolio_summary(parity_user_id)


    
@app.post("/api/plaid/link-token")
def plaid_link_token(request: Request, connection_type: str = "bank"):
    parity_user_id = get_parity_user_id(request)
    return create_link_token(parity_user_id, connection_type)


@app.post("/api/plaid/exchange-public-token")
def plaid_exchange_public_token(req: PlaidExchangeRequest, request: Request):
    parity_user_id = get_parity_user_id(request)
    return exchange_public_token(parity_user_id, req.public_token)


@app.post("/api/plaid/sync")
def plaid_sync(request: Request):
    parity_user_id = get_parity_user_id(request)
    return sync_bank_accounts(parity_user_id)


@app.get("/api/plaid/bank-accounts")
def plaid_bank_accounts(request: Request):
    parity_user_id = get_parity_user_id(request)
    return get_bank_accounts_from_db(parity_user_id)


@app.post("/api/brokerage/connect-url")
def brokerage_connect_url(request: Request):
    parity_user_id = get_parity_user_id(request)
    return create_connection_url(parity_user_id)


@app.get("/api/brokerage/accounts")
def brokerage_accounts(request: Request):
    parity_user_id = get_parity_user_id(request)
    return list_accounts(parity_user_id)


@app.get("/api/brokerage/accounts/{account_id}/positions")
def brokerage_positions(account_id: str, request: Request):
    parity_user_id = get_parity_user_id(request)
    return get_account_positions(parity_user_id, account_id)


@app.post("/api/brokerage/sync")
def brokerage_sync(request: Request):
    parity_user_id = get_parity_user_id(request)
    return sync_brokerage_accounts_and_holdings(parity_user_id)


@app.get(
    "/api/brokerage/accounts/{account_id}/execution-capabilities"
)
def brokerage_execution_capabilities(
    account_id: str,
    request: Request,
):
    """
    Read-only check used before Parity allows a user to prepare an order.
    """
    parity_user_id = get_parity_user_id(request)

    try:
        context = get_execution_account_context(
            parity_user_id=parity_user_id,
            account_id=account_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    capabilities = context["capabilities"]

    return {
        "account_id": context["account_id"],
        "institution_name": context["institution_name"],
        "account_name": context["account_name"],
        "account_number_mask": context["account_number_mask"],
        "brokerage_slug": context["brokerage_slug"],
        "is_paper": context["is_paper"],
        "supports_execution": (
            capabilities.supports_execution
        ),
        "supports_multi_leg_options": (
            capabilities.supports_multi_leg_options
        ),
        "supports_order_preview": (
            capabilities.supports_order_preview
        ),
        "supports_equity_orders": (
            capabilities.supports_equity_orders
        ),
        "supports_option_orders": (
            capabilities.supports_option_orders
        ),
    }


@app.post("/api/execution/workflows")
def execution_workflow_create(
    req: ExecutionWorkflowCreateRequest,
    request: Request,
):
    """
    Create a lot-aware execution workflow.

    This endpoint does not prepare or submit brokerage orders.
    """
    parity_user_id = get_parity_user_id(request)

    allowed_strategies = {
        "covered_call",
        "married_put",
        "collar",
        "buffer",
    }

    if req.strategy_type not in allowed_strategies:
        raise HTTPException(
            status_code=400,
            detail="Unsupported strategy type",
        )

    if req.underlying_source not in {"existing", "new"}:
        raise HTTPException(
            status_code=400,
            detail=(
                "underlying_source must be 'existing' or 'new'"
            ),
        )

    if req.underlying_shares < 100:
        raise HTTPException(
            status_code=400,
            detail="At least 100 shares are required",
        )

    if req.underlying_shares % 100 != 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "Shares must be selected in 100-share increments"
            ),
        )

    try:
        account_context = get_execution_account_context(
            parity_user_id=parity_user_id,
            account_id=req.account_id,
        )

        execution_plan = build_execution_plan(
            strategy_type=req.strategy_type,
            underlying_source=req.underlying_source,
        )

        workflow = create_execution_workflow(
            parity_user_id=parity_user_id,
            account_id=req.account_id,
            brokerage_slug=account_context["brokerage_slug"],
            strategy_type=req.strategy_type,
            underlying_source=req.underlying_source,
            underlying_symbol=req.underlying_symbol,
            underlying_shares=req.underlying_shares,
        )

        lots = create_execution_workflow_lots(
            parity_user_id=parity_user_id,
            workflow_id=str(workflow["id"]),
            underlying_source=req.underlying_source,
            option_contracts=workflow["option_contracts"],
        )

        workflow = save_execution_workflow_plan(
            parity_user_id=parity_user_id,
            workflow_id=workflow["id"],
            execution_plan=execution_plan,
            execution_preference=req.execution_preference,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return {
        "workflow": workflow,
        "lots": lots,
        "execution_plan": execution_plan,
        "next_step": execution_plan[0],
    }


@app.get("/api/execution/workflows/{workflow_id}")
def execution_workflow_get(
    workflow_id: str,
    request: Request,
):
    parity_user_id = get_parity_user_id(request)

    workflow = get_execution_workflow(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    if not workflow:
        raise HTTPException(
            status_code=404,
            detail="Execution workflow not found",
        )

    lots = get_execution_workflow_lots(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )

    orders = get_execution_workflow_orders(
        parity_user_id=parity_user_id,
        workflow_id=workflow_id,
    )
    execution_plan = (
        workflow["execution_plan"]
        or build_execution_plan(
            strategy_type=workflow["strategy_type"],
            underlying_source=workflow["underlying_source"],
        )
    )

    return {
        "workflow": workflow,
        "lots": lots,
        "orders": orders,
        "execution_plan": execution_plan
    }
@app.post(
    "/api/execution/workflows/{workflow_id}/option-orders/draft"
)
def execution_option_order_draft_create(
    workflow_id: str,
    req: ExecutionPrepareOptionOrderRequest,
    request: Request,
):
    parity_user_id = get_parity_user_id(request)

    try:
        order = prepare_option_order_draft(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            lot_id=req.lot_id,
            sequence=req.sequence,
            contracts=[
                contract.model_dump()
                for contract in req.contracts
            ],
            limit_price=req.limit_price,
            price_effect=req.price_effect,
            time_in_force=req.time_in_force,
            refresh_draft=req.refresh_draft,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return {
        "order": order,
    }


@app.post(
    "/api/execution/workflows/{workflow_id}/equity-orders/draft"
)
def execution_equity_order_draft_create(
    workflow_id: str,
    req: ExecutionPrepareEquityOrderRequest,
    request: Request,
):
    parity_user_id = get_parity_user_id(request)

    try:
        order = prepare_equity_order_draft(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            lot_id=req.lot_id,
            sequence=req.sequence,
            time_in_force=req.time_in_force,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return {
        "order": order,
    }





@app.post("/api/execution/workflows/{workflow_id}/options-close/draft")
def execution_options_close_draft_create(
    workflow_id: str,
    req: ExecutionCloseOptionsOverlayRequest,
    request: Request,
):
    """
    Create a fresh-quoted, unsubmitted draft that closes only the
    option overlay of a completed workflow lot.
    """

    parity_user_id = get_parity_user_id(request)

    require_subscription_feature(
        request,
        "can_close_existing_outcomes",
    )

    try:
        order = prepare_close_options_overlay_draft(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
            lot_id=req.lot_id,
            limit_price=req.limit_price,
            price_effect=req.price_effect,
            time_in_force=req.time_in_force,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return {
        "order": order,
    }








@app.post("/api/execution/orders/{order_id}/prepare")
def execution_order_prepare(
    order_id: str,
    request: Request,
):
    parity_user_id = get_parity_user_id(request)

    required_feature = (
        "can_close_existing_outcomes"
        if order["execution_phase"] == "CLOSE_OPTIONS"
        else "can_execute_new_orders"
    )

    require_subscription_feature(
        request,
        required_feature,
    )

    order = get_execution_order(
        parity_user_id=parity_user_id,
        order_id=order_id,
    )

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Execution order was not found",
        )

    try:
        safety = validate_execution_order_safety(order)

        prepared_order = mark_execution_order_prepared(
            parity_user_id=parity_user_id,
            order_id=order_id,
        )

    except ExecutionSafetyError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return {
        "order": prepared_order,
        "safety": safety,
    }


@app.post("/api/execution/orders/{order_id}/submit")
def execution_order_submit(
    order_id: str,
    req: ExecutionOrderSubmitRequest,
    request: Request,
):
    """
    Explicitly submit one PREPARED option order.

    This is the only endpoint in this workflow that may call SnapTrade.
    """

    if req.confirm_submission is not True:
        raise HTTPException(
            status_code=400,
            detail=(
                "confirm_submission must be true before an order "
                "can be submitted"
            ),
        )

    parity_user_id = get_parity_user_id(request)

    order = get_execution_order(
        parity_user_id=parity_user_id,
        order_id=order_id,
    )

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Execution order was not found",
        )

    required_feature = (
        "can_close_existing_outcomes"
        if order["execution_phase"] == "CLOSE_OPTIONS"
        else "can_execute_new_orders"
    )

    require_subscription_feature(
        request,
        required_feature,
    )
    try:
        result = submit_prepared_option_order(
            parity_user_id=parity_user_id,
            order_id=order_id,
        )

    except ExecutionSubmissionError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    return result



@app.post("/api/execution/orders/{order_id}/cancel")
def execution_order_cancel(
    order_id: str,
    req: ExecutionOrderCancelRequest,
    request: Request,
):
    """
    Explicitly request cancellation of one working brokerage order.
    """

    if req.confirm_cancellation is not True:
        raise HTTPException(
            status_code=400,
            detail=(
                "confirm_cancellation must be true before an order "
                "can be canceled"
            ),
        )

    parity_user_id = get_parity_user_id(request)

    require_subscription_feature(
        request,
        "can_close_existing_outcomes",
    )

    try:
        return request_execution_order_cancellation(
            parity_user_id=parity_user_id,
            order_id=order_id,
        )

    except ExecutionCancellationError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc



@app.post(
    "/api/execution/workflows/{workflow_id}/abandon"
)
def execution_workflow_abandon(
    workflow_id: str,
    req: ExecutionWorkflowAbandonRequest,
    request: Request,
):
    """
    Permanently abandon an unfilled workflow and release any
    existing-share reservation. This never calls SnapTrade.
    """

    if req.confirm_abandonment is not True:
        raise HTTPException(
            status_code=400,
            detail=(
                "confirm_abandonment must be true before a workflow "
                "can be abandoned"
            ),
        )

    parity_user_id = get_parity_user_id(request)

    require_subscription_feature(
        request,
        "can_close_existing_outcomes",
    )

    try:
        workflow = abandon_execution_workflow(
            parity_user_id=parity_user_id,
            workflow_id=workflow_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    return {
        "workflow": workflow,
    }



@app.post("/api/execution/orders/{order_id}/status/refresh")
def execution_order_status_refresh(
    order_id: str,
    request: Request,
):
    """
    Refresh one submitted order from SnapTrade.

    This reads broker status and updates Parity's local record only.
    It never submits, cancels, replaces, or re-prices an order.
    """

    parity_user_id = get_parity_user_id(request)

    require_subscription_feature(
        request,
        "can_view_existing_outcomes",
    )

    try:
        return refresh_execution_order_status(
            parity_user_id=parity_user_id,
            order_id=order_id,
        )

    except ExecutionStatusError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@app.get("/api/dashboard/portfolio")
def dashboard_portfolio(request: Request):
    parity_user_id = get_parity_user_id(request)
    return get_portfolio_summary(parity_user_id)



