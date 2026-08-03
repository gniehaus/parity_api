import os
from typing import Literal
from typing import Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
)

from nanos_engine import build_weekly_outcomes_payload
from app.db import get_public_scorecard_snapshot
from parity_collar_engine import (
    fetch_orats_chain,
    build_defined_outcome_recommendations,
    clean_chain,
    select_single_expiry,
    build_married_put,
    build_covered_call,
    make_json_safe,
    build_unlimited_upside_put_spread_ladder,
)

from parity_engine import (
    generate_portfolio_collar_candidates,
    optimize_parity_portfolio,
)


app = FastAPI(
    title="Parity Outcome API",
    version="3.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PortfolioRequest(BaseModel):
    investment_amount: float = Field(..., description="Example: 25000")
    max_loss_pct: float = Field(..., description="Example: 0.10 = 10% max portfolio loss")
    time_horizon_days: int = Field(default=365)
    growth_preference: str = "balanced"
    assumed_treasury_yield: float = Field(default=0.035)
    dividend_yields: Dict[str, float] = {}


class RecommendationRequest(BaseModel):
    ticker: str = Field(..., description="Example: SPY, QQQ, TSLA")
    horizon: int = Field(..., description="Target outcome period in days")
    max_loss: float = Field(..., description="Example: 0.10 = 10% max loss")
    target_gain: float = Field(default=0.08)
    assumed_dividend_yield: float = Field(default=0.0)
    target_buffer_pct: float = Field(default=0.10)
    investment_amount: float | None = None
    protection_style: Literal["hard_floor", "buffer", "show_both"] | None = None
    risk_profile: Literal["conservative", "balanced", "growth"] | None = None

class PutSpreadLadderRequest(BaseModel):
    ticker: str
    horizon: int = 365
    assumed_dividend_yield: float = 0.01
    protection_levels: list[float] = [0.05, 0.10, 0.15, 0.20]

class MarriedPutRequest(BaseModel):
    ticker: str
    horizon: int
    protection: float
    assumed_dividend_yield: float = 0.0
    investment_amount: float | None = None


class CoveredCallRequest(BaseModel):
    ticker: str
    horizon: int
    target_protection: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Target total protection as a decimal. "
            "Includes executable call premium and "
            "expected dividends. Example: 0.04 = 4%."
        ),
        validation_alias=AliasChoices(
            "target_protection",
            "target_income",
        ),
    )
    assumed_dividend_yield: float = 0.0
    investment_amount: float | None = None

class MarketplaceRequest(BaseModel):
    ticker: str
    horizon: int
    max_loss: float = 0.10
    target_protection: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description=(
            "Covered-call target total protection as "
            "a decimal, including executable call "
            "premium and expected dividends."
        ),
        validation_alias=AliasChoices(
            "target_protection",
            "target_income",
        ),
    )
    target_gain: float = 0.08
    assumed_dividend_yield: float = 0.0
    investment_amount: float | None = None

def get_orats_token() -> str:
    token = os.getenv("ORATS_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="Missing ORATS_TOKEN environment variable")
    return token


def get_selected_expiry_chain(ticker: str, horizon: int):
    token = get_orats_token()
    raw_df = fetch_orats_chain(ticker=ticker, token=token)
    chain = clean_chain(raw_df, ticker=ticker)

    expiry_chain, selected_expiry_summary, _ = select_single_expiry(
        chain,
        target_dte=horizon,
        prefer_at_or_after=True,
        max_dte_overage=250,
    )

    return expiry_chain, selected_expiry_summary

@app.post("/put-spread-ladder")
def get_put_spread_ladder(request: PutSpreadLadderRequest):
    try:
        expiry_chain, selected_expiry_summary = get_selected_expiry_chain(
            ticker=request.ticker,
            horizon=request.horizon,
        )

        result = build_unlimited_upside_put_spread_ladder(
            expiry_chain=expiry_chain,
            protection_levels=request.protection_levels,
            assumed_dividend_yield=request.assumed_dividend_yield,
        )

        if result is None:
            raise HTTPException(status_code=404, detail="No put spread ladder found.")

        result["ticker"] = request.ticker
        result["horizon"] = request.horizon
        result["selected_expiry"] = selected_expiry_summary
        result["request"] = request.model_dump()

        return make_json_safe(result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "Parity Outcome API",
        "version": "3.1.0",
        "main_endpoint": "POST /portfolio",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/portfolio")
def get_portfolio(request: PortfolioRequest):
    try:
        token = get_orats_token()

        collar_candidates = generate_portfolio_collar_candidates(
            token=token,
            investment_amount=request.investment_amount,
            max_loss_pct=request.max_loss_pct,
            time_horizon_days=request.time_horizon_days,
            dividend_yields=request.dividend_yields,
            growth_preference=request.growth_preference,
        )
    
        portfolio = optimize_parity_portfolio(
            investment_amount=request.investment_amount,
            max_loss_pct=request.max_loss_pct,
            time_horizon_days=request.time_horizon_days,
            collar_candidates=collar_candidates,
            treasury_ticker="SGOV",
            assumed_treasury_yield=request.assumed_treasury_yield,
            growth_preference=request.growth_preference,
        )

        portfolio["request"] = request.model_dump()
        return make_json_safe(portfolio)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/public-scorecards")
def get_public_scorecards():
    """
    Return the latest cached homepage scorecards from Postgres.
    This endpoint does not call ORATS or recalculate outcomes.
    """

    try:
        snapshot = get_public_scorecard_snapshot(
            cache_key="homepage_v1"
        )

        if snapshot is None:
            raise HTTPException(
                status_code=503,
                detail="Public scorecards have not been generated yet.",
            )

        payload = snapshot["payload"]

        return make_json_safe(
            {
                "cache_key": snapshot["cache_key"],
                "market_data_timestamp": (
                    snapshot["market_data_timestamp"]
                ),
                "generated_at": snapshot["generated_at"],
                "updated_at": snapshot["updated_at"],
                "cards": payload.get("cards", []),
            }
        )

    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to load public scorecards: {error}",
        )


@app.get("/weekly-outcomes")
def get_weekly_outcomes():
    try:
        payload = build_weekly_outcomes_payload(
            etf_symbol="SPY",
            income_targets=[0.0025, 0.005, 0.0075],
            max_expirations=6,
        )
        return make_json_safe(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recommendations")
def get_recommendations(request: RecommendationRequest):
    try:
        token = get_orats_token()
        df = fetch_orats_chain(ticker=request.ticker, token=token)

        payload = build_defined_outcome_recommendations(
            df=df,
            ticker=request.ticker,
            horizon=request.horizon,
            max_loss_pct=request.max_loss,
            target_gain_pct=request.target_gain,
            target_buffer_pct=request.target_buffer_pct,
        )

        payload["request"] = request.model_dump()
        return make_json_safe(payload)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/married-put")
def get_married_put(request: MarriedPutRequest):
    try:
        expiry_chain, selected_expiry_summary = get_selected_expiry_chain(
            ticker=request.ticker,
            horizon=request.horizon,
        )

        result = build_married_put(
            expiry_chain=expiry_chain,
            max_loss_pct=request.protection,
            assumed_dividend_yield=request.assumed_dividend_yield,
        )

        if result is None:
            raise HTTPException(status_code=404, detail="No married put found.")

        result["selected_expiry"] = selected_expiry_summary
        result["request"] = request.model_dump()
        return make_json_safe(result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/covered-call")
def get_covered_call(request: CoveredCallRequest):
    try:
        expiry_chain, selected_expiry_summary = get_selected_expiry_chain(
            ticker=request.ticker,
            horizon=request.horizon,
        )

        result = build_covered_call(
            expiry_chain=expiry_chain,
            target_protection_pct=(
                request.target_protection
            ),
            assumed_dividend_yield=(
                request.assumed_dividend_yield
            ),
        )

        if result is None:
            raise HTTPException(status_code=404,detail=(
                            "No covered call was found within 0.50 "
                            "percentage points of the requested total "
                            "protection."
                        ),
                    )

        result["selected_expiry"] = selected_expiry_summary
        result["request"] = request.model_dump()
        return make_json_safe(result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/marketplace")
def get_marketplace_products(request: MarketplaceRequest):
    try:
        token = get_orats_token()
        df = fetch_orats_chain(ticker=request.ticker, token=token)

        legacy_payload = build_defined_outcome_recommendations(
            df=df,
            ticker=request.ticker,
            horizon=request.horizon,
            max_loss_pct=request.max_loss,
            target_gain_pct=request.target_gain,
            assumed_dividend_yield=request.assumed_dividend_yield,
        )

        expiry_chain, selected_expiry_summary = get_selected_expiry_chain(
            ticker=request.ticker,
            horizon=request.horizon,
        )

        married_put = build_married_put(
            expiry_chain=expiry_chain,
            max_loss_pct=request.max_loss,
            assumed_dividend_yield=request.assumed_dividend_yield,
        )

        covered_call = build_covered_call(
            expiry_chain=expiry_chain,
            target_protection_pct=(
                request.target_protection
            ),
            assumed_dividend_yield=(
                request.assumed_dividend_yield
            ),
        )
        payload = {
            "ticker": request.ticker,
            "horizon": request.horizon,
            "selected_expiry": selected_expiry_summary,
            "request": request.model_dump(),
            "products": {
                "defined_range": legacy_payload["products"]["defined_floor"],
                "buffered_growth": legacy_payload["products"]["buffered_growth"],
                "insured_upside": married_put,
                "income": covered_call,
            },
        }

        return make_json_safe(payload)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))