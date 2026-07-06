import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from snaptrade_client import SnapTrade
from .snaptrade_service import create_connection_url, list_accounts, get_account_positions
from .db import init_db

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



def normalize_account_with_positions(account: dict, positions: list):
    total_value = float(
        account.get("balance", {})
        .get("total", {})
        .get("amount") or 0
    )

    normalized_holdings = []
    invested_value = 0.0

    for p in positions:
        symbol_obj = p.get("symbol") or {}
        symbol = (
            symbol_obj.get("symbol")
            or symbol_obj.get("ticker")
            or symbol_obj.get("raw_symbol")
        )

        market_value = float(p.get("market_value") or p.get("value") or 0)
        invested_value += market_value

        normalized_holdings.append({
            "symbol": symbol,
            "quantity": float(p.get("units") or p.get("quantity") or 0),
            "price": float(p.get("price") or 0),
            "market_value": market_value,
            "asset_type": symbol_obj.get("type", {}).get("code", "unknown"),
        })

    cash_value = max(total_value - invested_value, 0)
    cash_weight = cash_value / total_value if total_value > 0 else 0

    if cash_weight >= 0.95:
        portfolio_status = "all_cash"
    elif cash_weight >= 0.50:
        portfolio_status = "mostly_cash"
    else:
        portfolio_status = "invested"

    return {
        "account_id": account.get("id"),
        "account_name": account.get("name"),
        "institution_name": account.get("institution_name"),
        "total_value": total_value,
        "cash_value": cash_value,
        "invested_value": invested_value,
        "cash_weight": cash_weight,
        "portfolio_status": portfolio_status,
        "holdings": normalized_holdings,
    }




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


@app.get("/")
def health():
    return {"status": "ok", "service": "parity-snaptrade-api"}




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


    


@app.post("/connect-url")
def connect_url():
    global USER_SECRET

    if not USER_SECRET:
        try:
            response = snaptrade.authentication.register_snap_trade_user(
                user_id=USER_ID
            )
            USER_SECRET = response.body["userSecret"]
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Missing SNAPTRADE_TEST_USER_SECRET. User may already exist. Error: {str(e)}",
            )

    login = snaptrade.authentication.login_snap_trade_user(
        user_id=USER_ID,
        user_secret=USER_SECRET,
    )

    return {
        "user_id": USER_ID,
        "redirect_url": login.body["redirectURI"],
    }


@app.get("/accounts")
def accounts():
    if not USER_SECRET:
        raise HTTPException(status_code=400, detail="Missing SNAPTRADE_TEST_USER_SECRET")

    response = snaptrade.account_information.list_user_accounts(
        user_id=USER_ID,
        user_secret=USER_SECRET,
    )

    return response.body


@app.get("/holdings/{account_id}")
def holdings(account_id: str):
    if not USER_SECRET:
        raise HTTPException(status_code=400, detail="Missing SNAPTRADE_TEST_USER_SECRET")

    positions = snaptrade.account_information.get_user_account_positions(
        user_id=USER_ID,
        user_secret=USER_SECRET,
        account_id=account_id,
    )

    normalized = []

    for p in positions.body:
        symbol_obj = p.get("symbol") or {}

        symbol = (
            symbol_obj.get("symbol")
            or symbol_obj.get("ticker")
            or symbol_obj.get("raw_symbol")
        )

        market_value = (
            p.get("market_value")
            or p.get("value")
            or 0
        )

        normalized.append({
            "symbol": symbol,
            "quantity": p.get("units") or p.get("quantity") or 0,
            "price": p.get("price") or 0,
            "market_value": market_value,
            "raw": p,
        })

    return {
        "account_id": account_id,
        "holdings": normalized,
    }


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
            },
        }

@app.get("/api/dashboard/portfolio")
def dashboard_portfolio(request: Request):
    parity_user_id = get_parity_user_id(request)

    accounts = list_accounts(parity_user_id)

    normalized_accounts = []
    total_value = 0.0
    total_cash = 0.0
    total_invested = 0.0

    for account in accounts:
        account_id = account.get("id")
        positions = get_account_positions(parity_user_id, account_id)

        normalized = normalize_account_with_positions(account, positions)

        normalized_accounts.append(normalized)
        total_value += normalized["total_value"]
        total_cash += normalized["cash_value"]
        total_invested += normalized["invested_value"]

    cash_weight = total_cash / total_value if total_value > 0 else 0

    if cash_weight >= 0.95:
        status = "all_cash"
        recommended_etf = "SPY"
        reason = "Your connected portfolio appears to be mostly cash. A defined outcome sleeve on SPY can add broad market exposure while keeping downside defined."
    elif cash_weight >= 0.50:
        status = "mostly_cash"
        recommended_etf = "SPY"
        reason = "Your portfolio has a large cash allocation. A defined outcome sleeve can help put some of that cash to work with a known downside range."
    else:
        status = "invested"
        recommended_etf = "SPY"
        reason = "Your connected portfolio is invested. SPY can act as a broad defined outcome sleeve to complement your existing holdings."

    return {
        "accounts": normalized_accounts,
        "portfolio_summary": {
            "total_value": total_value,
            "cash_value": total_cash,
            "invested_value": total_invested,
            "cash_weight": cash_weight,
            "status": status,
        },
        "recommendation": {
            "recommended_etf": recommended_etf,
            "reason": reason,
            "suggested_outcome_inputs": {
                "ticker": recommended_etf,
                "max_loss": 0.10,
                "horizon_days": 365,
            },
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