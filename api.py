import os
from typing import Optional, List, Dict, Any

import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from parity_collar_engine import (
    fetch_orats_chain,
    get_closest_expiry_chains,
    find_classic_and_buffered_collars,
    build_frontend_payload,
    make_json_safe,
)


app = FastAPI(
    title="Parity Collar API",
    version="1.0.0",
    description="API for finding classic collar and buffered collar scenarios from ORATS option chains.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later once you know the Base44 domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CollarRequest(BaseModel):
    ticker: str = Field(default="XSP")
    loss: float = Field(..., description="Target loss as decimal. Example: 0.02")
    gain: float = Field(..., description="Target gain as decimal. Example: 0.07")
    horizon: int = Field(..., description="Target horizon in days. Example: 365")

    n_expiries: int = Field(default=5)
    max_net_cost_bps: float = Field(default=100)

    put_buffer_pct: float = Field(default=0.06)
    call_buffer_pct: float = Field(default=0.08)

    min_buffer_width: float = Field(default=1)
    max_buffer_width: float = Field(default=100)

    n_classic: int = Field(default=5)
    n_buffered: int = Field(default=5)


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "Parity Collar API",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }


@app.post("/collars")
def get_collars(request: CollarRequest):
    try:
        df = fetch_orats_chain(ticker=request.ticker)

        closest_chains, expiry_summary = get_closest_expiry_chains(
            df,
            target_dte=request.horizon,
            n_expiries=request.n_expiries,
            ticker=request.ticker,
        )

        collar_scenarios = find_classic_and_buffered_collars(
            closest_chains,
            target_loss_pct=request.loss,
            target_gain_pct=request.gain,
            max_net_cost_bps=request.max_net_cost_bps,
            put_buffer_pct=request.put_buffer_pct,
            call_buffer_pct=request.call_buffer_pct,
            min_buffer_width=request.min_buffer_width,
            max_buffer_width=request.max_buffer_width,
        )

        payload = build_frontend_payload(
            collar_scenarios,
            expiry_summary=expiry_summary,
            n_classic=request.n_classic,
            n_buffered=request.n_buffered,
        )

        return make_json_safe(payload)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))