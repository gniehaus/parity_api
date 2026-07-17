import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from snaptrade_client import SnapTrade

from .db import (
    init_db,
    upsert_parity_user,
    get_conn,
    get_investor_profile,
    upsert_investor_profile,
)

from .snaptrade_service import (
    create_connection_url,
    list_accounts,
    get_account_positions,
    sync_brokerage_accounts_and_holdings,
    get_portfolio_summary,
    get_dashboard_holdings_for_metrics,
    get_account_level_portfolio_summary,
)
from .plaid_service import (
    create_link_token,
    exchange_public_token,
    sync_bank_accounts,
    get_bank_accounts_from_db,
    test_plaid_investments,
)
from .portfolio_dashboard_engine import calculate_portfolio_dashboard

app = FastAPI(title="Parity SnapTrade API")


@app.on_event("startup")
def startup():
    init_db()


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


def get_parity_user_id(request: Request) -> str:
    user_id = request.headers.get("X-Parity-User-Id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing X-Parity-User-Id")
    return user_id


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


class InvestorProfileRequest(BaseModel):
    recommendation_use: str | None = None
    primary_goal: str | None = None
    max_acceptable_loss: float | None = None
    time_horizon: str | None = None
    liquidity_need: str | None = None
    tradeoff_preference: str | None = None
    investment_experience: str | None = None
    scope: str | None = None
    new_investment_amount: float | None = None
    contradiction_acknowledged: bool = False
    completed: bool = False
    raw: dict | None = None

    
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


@app.put("/api/investor-profile")
def investor_profile_put(
    req: InvestorProfileRequest,
    request: Request,
):
    parity_user_id = get_parity_user_id(request)

    profile = upsert_investor_profile(
        parity_user_id=parity_user_id,
        recommendation_use=req.recommendation_use,
        primary_goal=req.primary_goal,
        max_acceptable_loss=req.max_acceptable_loss,
        time_horizon=req.time_horizon,
        liquidity_need=req.liquidity_need,
        tradeoff_preference=req.tradeoff_preference,
        investment_experience=req.investment_experience,
        scope=req.scope,
        new_investment_amount=req.new_investment_amount,
        contradiction_acknowledged=req.contradiction_acknowledged,
        completed=req.completed,
        raw=req.raw,
    )

    return {
        "status": "saved",
        "profile": profile,
    }


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


@app.get("/api/plaid/investments/test")
def plaid_investments_test(request: Request):
    parity_user_id = get_parity_user_id(request)
    return test_plaid_investments(parity_user_id)

    

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


@app.get("/api/dashboard/portfolio")
def dashboard_portfolio(request: Request):
    parity_user_id = get_parity_user_id(request)
    return get_portfolio_summary(parity_user_id)


@app.post("/recommend")
def recommend(req: RecommendRequest):
    holdings = req.holdings
    cash = req.cash or 0

    total_value = cash + sum(float(h.get("market_value") or 0) for h in holdings)

    if total_value <= 0:
        return {
            "recommended_etf": "SPY",
            "reason": "Default broad market recommendation.",
            "suggested_outcome_inputs": {
                "ticker": "SPY",
                "max_loss": 0.10,
                "horizon_days": 365,
                "investment_amount": req.investment_amount,
            },
        }

    tech_symbols = {"QQQ", "XLK", "NVDA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "GOOG", "TSLA"}
    international_symbols = {"EFA", "VWO", "VEA", "VXUS", "IEFA", "IEMG"}

    tech_weight = sum(
        float(h.get("market_value") or 0)
        for h in holdings
        if str(h.get("symbol", "")).upper() in tech_symbols
    ) / total_value

    international_weight = sum(
        float(h.get("market_value") or 0)
        for h in holdings
        if str(h.get("symbol", "")).upper() in international_symbols
    ) / total_value

    cash_weight = cash / total_value

    top_holding = None
    top_weight = 0

    for h in holdings:
        weight = float(h.get("market_value") or 0) / total_value
        if weight > top_weight:
            top_weight = weight
            top_holding = h.get("symbol")

    if cash_weight > 0.30:
        etf = "SPY"
        reason = "You have a large cash position. A protected SPY outcome can add broad market exposure with defined downside."
    elif top_weight > 0.25:
        etf = "SGOV"
        reason = f"Your portfolio appears concentrated in {top_holding}. SGOV can add a conservative sleeve while keeping the recommendation simple."
    elif tech_weight > 0.35:
        etf = "SCHD"
        reason = "You already have meaningful tech exposure. SCHD may complement it with dividend/value exposure."
    elif international_weight < 0.10:
        etf = "EFA"
        reason = "Your portfolio appears light on international developed-market exposure."
    else:
        etf = "SPY"
        reason = "SPY gives broad U.S. market exposure and works well for a general defined outcome sleeve."

    return {
        "recommended_etf": etf,
        "reason": reason,
        "suggested_outcome_inputs": {
            "ticker": etf,
            "max_loss": 0.10,
            "horizon_days": 365,
            "investment_amount": req.investment_amount,
        },
    }