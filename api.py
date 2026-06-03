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


import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from parity_collar_engine import (
    fetch_orats_chain,
    build_defined_outcome_recommendations,
)


app = FastAPI(title="Parity Defined Outcome API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RecommendationRequest(BaseModel):
    ticker: str = Field(default="XSP")
    horizon: int = Field(default=365, description="Target horizon in days")
    max_loss: float = Field(default=0.005, description="Max loss target for collar. Example: 0.005 = 0.50%")
    target_gain: float = Field(default=0.08, description="Target gain for buffer. Example: 0.08 = 8.00%")
    assumed_dividend_yield: float = Field(default=0.01, description="Annual dividend yield. Example: 0.01 = 1.00%")


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Parity Defined Outcome API is running",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }


@app.post("/recommendations")
def get_recommendations(request: RecommendationRequest):
    try:
        token = os.getenv("ORATS_TOKEN")

        if not token:
            raise HTTPException(
                status_code=500,
                detail="Missing ORATS_TOKEN environment variable",
            )

        df = fetch_orats_chain(
            ticker=request.ticker,
            token=token,
        )

        payload = build_defined_outcome_recommendations(
            df=df,
            ticker=request.ticker,
            horizon=request.horizon,
            max_loss_pct=request.max_loss,
            target_gain_pct=request.target_gain,
            assumed_dividend_yield=request.assumed_dividend_yield,
        )

        return payload

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


# Optional backwards-compatible GET endpoint for quick browser testing
@app.get("/recommendations")
def get_recommendations_get(
    ticker: str = "XSP",
    horizon: int = 365,
    max_loss: float = 0.005,
    target_gain: float = 0.08,
    assumed_dividend_yield: float = 0.01,
):
    try:
        token = os.getenv("ORATS_TOKEN")

        if not token:
            raise HTTPException(
                status_code=500,
                detail="Missing ORATS_TOKEN environment variable",
            )

        df = fetch_orats_chain(
            ticker=ticker,
            token=token,
        )

        payload = build_defined_outcome_recommendations(
            df=df,
            ticker=ticker,
            horizon=horizon,
            max_loss_pct=max_loss,
            target_gain_pct=target_gain,
            assumed_dividend_yield=assumed_dividend_yield,
        )

        return payload

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )