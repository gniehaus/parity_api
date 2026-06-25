# import os
# from typing import Optional, List, Dict, Any

# import pandas as pd
# import numpy as np
# from fastapi import FastAPI, HTTPException
# from fastapi.middleware.cors import CORSMiddleware
# from pydantic import BaseModel, Field

# from parity_collar_engine import (
#     fetch_orats_chain,
#     get_closest_expiry_chains,
#     find_classic_and_buffered_collars,
#     build_frontend_payload,
#     make_json_safe,
# )


# app = FastAPI(
#     title="Parity Collar API",
#     version="1.0.0",
#     description="API for finding classic collar and buffered collar scenarios from ORATS option chains.",
# )

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # tighten later once you know the Base44 domain
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


# class CollarRequest(BaseModel):
#     ticker: str = Field(default="XSP")
#     loss: float = Field(..., description="Target loss as decimal. Example: 0.02")
#     gain: float = Field(..., description="Target gain as decimal. Example: 0.07")
#     horizon: int = Field(..., description="Target horizon in days. Example: 365")

#     n_expiries: int = Field(default=5)
#     max_net_cost_bps: float = Field(default=100)

#     put_buffer_pct: float = Field(default=0.06)
#     call_buffer_pct: float = Field(default=0.08)

#     min_buffer_width: float = Field(default=1)
#     max_buffer_width: float = Field(default=100)

#     n_classic: int = Field(default=5)
#     n_buffered: int = Field(default=5)


# @app.get("/")
# def root():
#     return {
#         "status": "ok",
#         "service": "Parity Collar API",
#     }


# @app.get("/health")
# def health():
#     return {
#         "status": "healthy",
#     }


# @app.post("/collars")
# def get_collars(request: CollarRequest):
#     try:
#         df = fetch_orats_chain(ticker=request.ticker)

#         closest_chains, expiry_summary = get_closest_expiry_chains(
#             df,
#             target_dte=request.horizon,
#             n_expiries=request.n_expiries,
#             ticker=request.ticker,
#         )

#         collar_scenarios = find_classic_and_buffered_collars(
#             closest_chains,
#             target_loss_pct=request.loss,
#             target_gain_pct=request.gain,
#             max_net_cost_bps=request.max_net_cost_bps,
#             put_buffer_pct=request.put_buffer_pct,
#             call_buffer_pct=request.call_buffer_pct,
#             min_buffer_width=request.min_buffer_width,
#             max_buffer_width=request.max_buffer_width,
#         )

#         payload = build_frontend_payload(
#             collar_scenarios,
#             expiry_summary=expiry_summary,
#             n_classic=request.n_classic,
#             n_buffered=request.n_buffered,
#         )

#         return make_json_safe(payload)

#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))


# import os
# from typing import Literal

# from fastapi import FastAPI, HTTPException
# from fastapi.middleware.cors import CORSMiddleware
# from pydantic import BaseModel, Field

# from parity_collar_engine import (
#     fetch_orats_chain,
#     build_defined_outcome_recommendations,
#     clean_chain,
#     select_single_expiry,
#     analyze_defined_income_product,
#     make_json_safe,

# )


# app = FastAPI(title="Parity Defined Outcome API")

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


# class RecommendationRequest(BaseModel):
#     # Base44 should pass this from the selected product universe.
#     # Phase 1 default in the UI can still be XSP, but the API should receive it explicitly.
#     ticker: str = Field(..., description="Options ticker to use, e.g. XSP")

#     # Base44 should pass this from the user's survey answer.
#     # Example mappings:
#     # 3 months -> 90
#     # 6 months -> 180
#     # 1 year -> 365
#     horizon: int = Field(..., description="Target outcome period in days")

#     # Base44 should pass this from the user's downside preference.
#     # Example:
#     # close to no loss -> 0.005
#     # small loss -> 0.01 or 0.015
#     # more downside -> 0.025
#     max_loss: float = Field(
#         ...,
#         description="Max loss target for Defined Floor collar. Example: 0.005 = 0.50%",
#     )

#     # Base44 should pass this from the user's upside preference.
#     # Example:
#     # conservative -> 0.05
#     # balanced -> 0.07
#     # growth -> 0.10
#     target_gain: float = Field(
#         ...,
#         description="Target gain used to select the Buffered Growth call. Example: 0.08 = 8.00%",
#     )

#     # Base44 should pass this explicitly.
#     # For now Base44 can send 0.01.
#     assumed_dividend_yield: float = Field(
#         ...,
#         description="Annual dividend yield assumption. Example: 0.01 = 1.00%",
#     )
    
#     target_buffer_pct: float = Field(default=0.10)

#     # Optional metadata from Base44. The API does not need this for math yet,
#     # but it can be returned/logged later if useful.
#     investment_amount: float | None = Field(
#         default=None,
#         description="User's intended investment amount, if collected",
#     )

#     protection_style: Literal["hard_floor", "buffer", "show_both"] | None = Field(
#         default=None,
#         description="User's selected protection style",
#     )

#     risk_profile: Literal["conservative", "balanced", "growth"] | None = Field(
#         default=None,
#         description="Derived front-end risk profile",
#     )
    
# class IncomeRequest(BaseModel):
#     ticker: str = Field(..., description="Options ticker to use, e.g. SPY or XSP")
#     horizon: int = Field(..., description="Target income period in days")
#     floor_pct: float = Field(..., description="Max downside as decimal. Example: 0.10 = 10%")
#     cap_pct: float = Field(..., description="Upside cap as decimal. Example: 0.08 = 8%")
#     assumed_dividend_yield: float = Field(default=0.0)
#     investment_amount: float | None = None
#     income_goal: Literal["conservative_income", "balanced_income", "higher_income"] | None = None

# @app.get("/")
# def root():
#     return {
#         "status": "ok",
#         "message": "Parity Defined Outcome API is running",
#         "usage": "POST /recommendations with ticker, horizon, max_loss, target_gain, and assumed_dividend_yield",
#     }


# @app.get("/health")
# def health():
#     return {"status": "healthy"}


# @app.post("/recommendations")
# def get_recommendations(request: RecommendationRequest):
#     try:
#         token = os.getenv("ORATS_TOKEN")

#         if not token:
#             raise HTTPException(
#                 status_code=500,
#                 detail="Missing ORATS_TOKEN environment variable",
#             )

#         df = fetch_orats_chain(
#             ticker=request.ticker,
#             token=token,
#         )

#         payload = build_defined_outcome_recommendations(
#             df=df,
#             ticker=request.ticker,
#             horizon=request.horizon,
#             max_loss_pct=request.max_loss,
#             target_gain_pct=request.target_gain,
#             assumed_dividend_yield=request.assumed_dividend_yield,
#             target_buffer_pct=request.target_buffer_pct,
#         )

#         # Echo the Base44 inputs back so the front end can confirm what was used.
#         payload["request"] = {
#             "ticker": request.ticker,
#             "horizon": request.horizon,
#             "max_loss": request.max_loss,
#             "target_gain": request.target_gain,
#             "assumed_dividend_yield": request.assumed_dividend_yield,
#             "investment_amount": request.investment_amount,
#             "protection_style": request.protection_style,
#             "risk_profile": request.risk_profile,
#         }

#         return payload

#     except HTTPException:
#         raise

#     except Exception as e:
#         raise HTTPException(
#             status_code=500,
#             detail=str(e),
#         )

# @app.post("/income")
# def get_income_product(request: IncomeRequest):
#     try:
#         token = os.getenv("ORATS_TOKEN")

#         if not token:
#             raise HTTPException(
#                 status_code=500,
#                 detail="Missing ORATS_TOKEN environment variable",
#             )

#         raw_df = fetch_orats_chain(
#             ticker=request.ticker,
#             token=token,
#         )

#         chain = clean_chain(raw_df, ticker=request.ticker)

#         expiry_chain, selected_expiry_summary, _ = select_single_expiry(
#             chain,
#             target_dte=request.horizon,
#             prefer_at_or_after=True,
#             max_dte_overage=60,
#         )

#         result = analyze_defined_income_product(
#             expiry_chain=expiry_chain,
#             floor_pct=request.floor_pct,
#             cap_pct=request.cap_pct,
#             assumed_dividend_yield=request.assumed_dividend_yield,
#         )

#         result["selected_expiry"] = selected_expiry_summary
#         result["request"] = request.dict()

#         return make_json_safe(result)

#     except HTTPException:
#         raise

#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))


import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from parity_collar_engine import (
    fetch_orats_chain,
    build_defined_outcome_recommendations,
    clean_chain,
    select_single_expiry,
    build_married_put,
    build_covered_call,
    make_json_safe,
)


app = FastAPI(
    title="Parity Outcome API",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RecommendationRequest(BaseModel):
    ticker: str = Field(..., description="Example: SPY, QQQ, TSLA")
    horizon: int = Field(..., description="Target outcome period in days")
    max_loss: float = Field(..., description="Example: 0.10 = 10% max loss")
    target_gain: float = Field(default=0.08, description="Target gain for legacy buffer")
    assumed_dividend_yield: float = Field(default=0.0)
    target_buffer_pct: float = Field(default=0.10)

    investment_amount: float | None = None
    protection_style: Literal["hard_floor", "buffer", "show_both"] | None = None
    risk_profile: Literal["conservative", "balanced", "growth"] | None = None


class MarriedPutRequest(BaseModel):
    ticker: str = Field(..., description="Example: TSLA")
    horizon: int = Field(..., description="Target protection period in days")
    protection: float = Field(..., description="Max loss target. Example: 0.10 = 10%")
    assumed_dividend_yield: float = Field(default=0.0)
    investment_amount: float | None = None


class CoveredCallRequest(BaseModel):
    ticker: str = Field(..., description="Example: TSLA")
    horizon: int = Field(..., description="Target income period in days")
    target_income: float = Field(..., description="Target income. Example: 0.05 = 5%")
    assumed_dividend_yield: float = Field(default=0.0)
    investment_amount: float | None = None


class MarketplaceRequest(BaseModel):
    ticker: str = Field(..., description="Example: TSLA")
    horizon: int = Field(..., description="Target period in days")
    max_loss: float = Field(default=0.10)
    target_income: float = Field(default=0.05)
    target_gain: float = Field(default=0.08)
    assumed_dividend_yield: float = Field(default=0.0)
    investment_amount: float | None = None


def get_selected_expiry_chain(ticker: str, horizon: int):
    token = os.getenv("ORATS_TOKEN")

    if not token:
        raise HTTPException(
            status_code=500,
            detail="Missing ORATS_TOKEN environment variable",
        )

    raw_df = fetch_orats_chain(ticker=ticker, token=token)
    chain = clean_chain(raw_df, ticker=ticker)

    expiry_chain, selected_expiry_summary, _ = select_single_expiry(
        chain,
        target_dte=horizon,
        prefer_at_or_after=True,
        max_dte_overage=60,
    )

    return expiry_chain, selected_expiry_summary


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "Parity Outcome API",
        "products": [
            "defined_range",
            "insured_upside",
            "income",
        ],
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/recommendations")
def get_recommendations(request: RecommendationRequest):
    """
    Legacy endpoint:
    Returns current defined floor / buffered growth payload.
    Keep this so Base44 does not break immediately.
    """
    try:
        token = os.getenv("ORATS_TOKEN")

        if not token:
            raise HTTPException(
                status_code=500,
                detail="Missing ORATS_TOKEN environment variable",
            )

        df = fetch_orats_chain(ticker=request.ticker, token=token)

        payload = build_defined_outcome_recommendations(
            df=df,
            ticker=request.ticker,
            horizon=request.horizon,
            max_loss_pct=request.max_loss,
            target_gain_pct=request.target_gain,
            assumed_dividend_yield=request.assumed_dividend_yield,
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
    """
    Product: Insured Upside
    Long stock + long put.
    """
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
            raise HTTPException(
                status_code=404,
                detail="No married put found for the requested inputs.",
            )

        result["selected_expiry"] = selected_expiry_summary
        result["request"] = request.model_dump()

        return make_json_safe(result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/covered-call")
def get_covered_call(request: CoveredCallRequest):
    """
    Product: Income
    Long stock + short call.
    """
    try:
        expiry_chain, selected_expiry_summary = get_selected_expiry_chain(
            ticker=request.ticker,
            horizon=request.horizon,
        )

        result = build_covered_call(
            expiry_chain=expiry_chain,
            target_income_pct=request.target_income,
            assumed_dividend_yield=request.assumed_dividend_yield,
        )

        if result is None:
            raise HTTPException(
                status_code=404,
                detail="No covered call found for the requested inputs.",
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
    """
    New product marketplace endpoint:
    Returns all three products:
    - Defined Range: collar
    - Insured Upside: married put
    - Income: covered call
    """
    try:
        token = os.getenv("ORATS_TOKEN")

        if not token:
            raise HTTPException(
                status_code=500,
                detail="Missing ORATS_TOKEN environment variable",
            )

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
            target_income_pct=request.target_income,
            assumed_dividend_yield=request.assumed_dividend_yield,
        )

        payload = {
            "ticker": request.ticker,
            "horizon": request.horizon,
            "selected_expiry": selected_expiry_summary,
            "request": request.model_dump(),
            "products": {
                "defined_range": legacy_payload["products"]["defined_floor"],
                "insured_upside": married_put,
                "income": covered_call,
            },
        }

        return make_json_safe(payload)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))