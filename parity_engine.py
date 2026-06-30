# parity_engine.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import combinations
from typing import Any, List, Optional
from datetime import datetime, timezone

import pandas as pd

from parity_collar_engine import (
    fetch_orats_chain,
    clean_chain,
    select_single_expiry,
)


# ----------------------------
# Data models
# ----------------------------

@dataclass
class OptionLeg:
    side: str              # "buy" or "sell"
    option_type: str       # "put" or "call"
    strike: float
    expiration: str
    bid: float
    ask: float
    mid: float
    volume: int
    open_interest: int


@dataclass
class CollarCandidate:
    ticker: str
    exposure: str
    shares: int
    contracts: int
    stock_price: float
    stock_value: float
    long_put: OptionLeg
    short_call: OptionLeg
    sleeve_max_loss_pct: float
    sleeve_max_gain_pct: float
    liquidity_score: float
    quote_timestamp: str


# ----------------------------
# Account tier / universe
# ----------------------------

def get_account_tier(amount: float) -> str:
    if amount < 10_000:
        return "below_minimum"
    if amount < 25_000:
        return "tier_1"
    if amount < 75_000:
        return "tier_2"
    if amount < 250_000:
        return "tier_3"
    return "tier_4"


def allowed_etfs(tier: str) -> list[str]:
    return {
        "tier_1": ["TQQQ"],
        "tier_2": ["TQQQ", "EEM"],
        "tier_3": ["TQQQ", "UPRO", "EEM", "EFA"],
        "tier_4": ["SPY", "QQQ", "IWM", "EEM", "EFA"],
    }.get(tier, [])


def max_collars_for_tier(tier: str) -> int:
    return {
        "tier_1": 1,
        "tier_2": 2,
        "tier_3": 3,
        "tier_4": 5,
    }.get(tier, 0)


def exposure_name(ticker: str) -> str:
    return {
        "TQQQ": "U.S. Growth",
        "UPRO": "U.S. Large Cap",
        "EEM": "Emerging Markets",
        "EFA": "Developed International",
        "SPY": "U.S. Large Cap",
        "QQQ": "Technology Growth",
        "IWM": "U.S. Small Cap",
    }.get(ticker.upper(), ticker.upper())


# ----------------------------
# Column helpers
# ----------------------------

def _first_existing_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for name in candidates:
        if name in df.columns:
            return name
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return None


def _get_stock_price(df: pd.DataFrame) -> float:
    col = _first_existing_col(
        df,
        [
            "stockPrice",
            "stock_price",
            "underlyingPrice",
            "underlying_price",
            "spot",
            "underlying",
            "price",
            "stkPx",
            "uPx",
        ],
    )
    if col is None:
        raise ValueError(f"Could not find stock price column. Columns: {list(df.columns)}")

    value = pd.to_numeric(df[col], errors="coerce").dropna()
    if value.empty:
        raise ValueError("Stock price column exists but has no numeric values.")

    return float(value.iloc[0])


def _get_strike_col(df: pd.DataFrame) -> str:
    col = _first_existing_col(df, ["strike", "strikePrice", "strike_price"])
    if col is None:
        raise ValueError(f"Could not find strike column. Columns: {list(df.columns)}")
    return col


def _get_expiration_value(df: pd.DataFrame) -> str:
    col = _first_existing_col(df, ["expiration", "expirDate", "expirationDate", "expDate", "expiry"])
    if col is None:
        return "unknown"
    value = df[col].dropna()
    return str(value.iloc[0]) if not value.empty else "unknown"


def _get_numeric(row: pd.Series, names: list[str], default: float = 0.0) -> float:
    for name in names:
        if name in row.index:
            val = pd.to_numeric(row[name], errors="coerce")
            if pd.notna(val):
                return float(val)

        lower_map = {str(c).lower(): c for c in row.index}
        if name.lower() in lower_map:
            real_name = lower_map[name.lower()]
            val = pd.to_numeric(row[real_name], errors="coerce")
            if pd.notna(val):
                return float(val)

    return float(default)


def _get_bid_ask_mid(row: pd.Series, option_type: str) -> tuple[float, float, float]:
    """
    Supports both normalized rows and ORATS-style call/put columns.
    """

    if option_type == "put":
        bid = _get_numeric(row, ["putBid", "pBid", "bid", "put_bid"])
        ask = _get_numeric(row, ["putAsk", "pAsk", "ask", "put_ask"])
        mid = _get_numeric(row, ["putMid", "pMid", "mid", "put_mid"], default=(bid + ask) / 2 if ask > 0 else bid)
    else:
        bid = _get_numeric(row, ["callBid", "cBid", "bid", "call_bid"])
        ask = _get_numeric(row, ["callAsk", "cAsk", "ask", "call_ask"])
        mid = _get_numeric(row, ["callMid", "cMid", "mid", "call_mid"], default=(bid + ask) / 2 if ask > 0 else bid)

    if mid == 0 and bid > 0 and ask > 0:
        mid = (bid + ask) / 2

    return float(bid), float(ask), float(mid)


def _get_volume_oi(row: pd.Series, option_type: str) -> tuple[int, int]:
    if option_type == "put":
        volume = _get_numeric(row, ["putVolume", "pVolu", "pVolume", "volume", "put_volume"], 0)
        oi = _get_numeric(row, ["putOpenInterest", "pOpenInterest", "pOI", "open_interest", "put_open_interest"], 0)
    else:
        volume = _get_numeric(row, ["callVolume", "cVolu", "cVolume", "volume", "call_volume"], 0)
        oi = _get_numeric(row, ["callOpenInterest", "cOpenInterest", "cOI", "open_interest", "call_open_interest"], 0)

    return int(volume or 0), int(oi or 0)


# ----------------------------
# Collar math
# ----------------------------

def collar_option_cost(c: CollarCandidate) -> float:
    """
    Conservative executable estimate:
    buy put at ask, sell call at bid.
    """
    return (c.long_put.ask - c.short_call.bid) * 100 * c.contracts


def collar_capital_required(c: CollarCandidate) -> float:
    return c.stock_value + collar_option_cost(c)


def collar_spread_cost(c: CollarCandidate) -> dict:
    put_spread = max(c.long_put.ask - c.long_put.bid, 0)
    call_spread = max(c.short_call.ask - c.short_call.bid, 0)

    gross_spread = (put_spread + call_spread) * 100 * c.contracts
    net_option_mid = (c.long_put.mid - c.short_call.mid) * 100 * c.contracts
    net_option_worst = collar_option_cost(c)

    return {
        "put_bid_ask_spread": round(put_spread, 4),
        "call_bid_ask_spread": round(call_spread, 4),
        "total_option_spread_dollars": round(gross_spread, 2),
        "net_option_mid_dollars": round(net_option_mid, 2),
        "net_option_conservative_dollars": round(net_option_worst, 2),
    }


def _liquidity_score(long_put: OptionLeg, short_call: OptionLeg) -> float:
    """
    Simple 0-100 score. Tune later with real fills.
    """
    put_spread_pct = (long_put.ask - long_put.bid) / long_put.mid if long_put.mid > 0 else 1
    call_spread_pct = (short_call.ask - short_call.bid) / short_call.mid if short_call.mid > 0 else 1

    avg_spread_pct = max((put_spread_pct + call_spread_pct) / 2, 0)
    total_oi = long_put.open_interest + short_call.open_interest
    total_volume = long_put.volume + short_call.volume

    spread_score = max(0, 100 - avg_spread_pct * 500)
    oi_score = min(100, total_oi / 20)
    volume_score = min(100, total_volume / 5)

    return round(spread_score * 0.55 + oi_score * 0.30 + volume_score * 0.15, 2)


def _build_leg(row: pd.Series, option_type: str, side: str, strike: float, expiration: str) -> OptionLeg:
    bid, ask, mid = _get_bid_ask_mid(row, option_type)
    volume, oi = _get_volume_oi(row, option_type)

    return OptionLeg(
        side=side,
        option_type=option_type,
        strike=float(strike),
        expiration=str(expiration),
        bid=round(float(bid), 4),
        ask=round(float(ask), 4),
        mid=round(float(mid), 4),
        volume=int(volume),
        open_interest=int(oi),
    )


def build_collar_candidates_for_expiry(
    expiry_chain: pd.DataFrame,
    ticker: str,
    max_candidates: int = 40,
    min_liquidity_score: float = 50,
) -> list[CollarCandidate]:
    """
    Generates classic collars:
    - Long 100 shares
    - Buy put below/near spot
    - Sell call above spot

    It searches many put/call combinations and returns candidates ranked by
    liquidity and max gain.
    """

    if expiry_chain is None or expiry_chain.empty:
        return []

    chain = expiry_chain.copy()
    strike_col = _get_strike_col(chain)
    stock_price = _get_stock_price(chain)
    expiration = _get_expiration_value(chain)
    quote_timestamp = datetime.now(timezone.utc).isoformat()

    chain[strike_col] = pd.to_numeric(chain[strike_col], errors="coerce")
    chain = chain.dropna(subset=[strike_col]).sort_values(strike_col)

    puts = chain[chain[strike_col] <= stock_price].copy()
    calls = chain[chain[strike_col] >= stock_price].copy()

    if puts.empty or calls.empty:
        return []

    candidates: list[CollarCandidate] = []

    # Limit search to realistic collar strikes.
    # Puts: 5% to 40% below spot.
    # Calls: 5% to 100% above spot.
    puts = puts[
        (puts[strike_col] >= stock_price * 0.60)
        & (puts[strike_col] <= stock_price * 0.98)
    ]

    calls = calls[
        (calls[strike_col] >= stock_price * 1.03)
        & (calls[strike_col] <= stock_price * 2.00)
    ]

    for _, put_row in puts.iterrows():
        put_strike = float(put_row[strike_col])
        long_put = _build_leg(
            row=put_row,
            option_type="put",
            side="buy",
            strike=put_strike,
            expiration=expiration,
        )

        if long_put.ask <= 0:
            continue

        for _, call_row in calls.iterrows():
            call_strike = float(call_row[strike_col])
            short_call = _build_leg(
                row=call_row,
                option_type="call",
                side="sell",
                strike=call_strike,
                expiration=expiration,
            )

            if short_call.bid <= 0:
                continue

            contracts = 1
            shares = 100
            stock_value = stock_price * shares
            net_option_cost = (long_put.ask - short_call.bid) * 100

            # Conservative downside:
            # if ETF expires below put, value = put strike * 100
            # plus/minus conservative option cost.
            sleeve_max_loss_dollars = max(
                0,
                stock_value + net_option_cost - (put_strike * 100),
            )
            sleeve_max_loss_pct = sleeve_max_loss_dollars / max(stock_value + net_option_cost, 1)

            # Conservative upside:
            # if ETF expires above call, value = call strike * 100
            sleeve_max_gain_dollars = max(
                0,
                (call_strike * 100) - stock_value - net_option_cost,
            )
            sleeve_max_gain_pct = sleeve_max_gain_dollars / max(stock_value + net_option_cost, 1)

            liq = _liquidity_score(long_put, short_call)

            if liq < min_liquidity_score:
                continue

            candidates.append(
                CollarCandidate(
                    ticker=ticker.upper(),
                    exposure=exposure_name(ticker),
                    shares=shares,
                    contracts=contracts,
                    stock_price=round(stock_price, 4),
                    stock_value=round(stock_value, 2),
                    long_put=long_put,
                    short_call=short_call,
                    sleeve_max_loss_pct=round(sleeve_max_loss_pct, 4),
                    sleeve_max_gain_pct=round(sleeve_max_gain_pct, 4),
                    liquidity_score=liq,
                    quote_timestamp=quote_timestamp,
                )
            )

    candidates.sort(
        key=lambda c: (
            c.liquidity_score,
            c.sleeve_max_gain_pct,
            -c.sleeve_max_loss_pct,
        ),
        reverse=True,
    )

    return candidates[:max_candidates]


def generate_portfolio_collar_candidates(
    token: str,
    investment_amount: float,
    max_loss_pct: float,
    time_horizon_days: int,
    objective: str = "growth",
) -> list[CollarCandidate]:
    """
    Called by api.py /portfolio.

    Fetches ORATS chains for the eligible ETF universe,
    chooses the closest usable expiration, and generates executable collar candidates.
    """

    tier = get_account_tier(investment_amount)

    if tier == "below_minimum":
        return []

    tickers = allowed_etfs(tier)
    all_candidates: list[CollarCandidate] = []

    for ticker in tickers:
        try:
            raw_df = fetch_orats_chain(ticker=ticker, token=token)
            chain = clean_chain(raw_df, ticker=ticker)

            expiry_chain, selected_expiry_summary, _ = select_single_expiry(
                chain,
                target_dte=time_horizon_days,
                prefer_at_or_after=True,
                max_dte_overage=250,
            )

            candidates = build_collar_candidates_for_expiry(
                expiry_chain=expiry_chain,
                ticker=ticker,
                max_candidates=30,
                min_liquidity_score=50,
            )

            all_candidates.extend(candidates)

        except Exception as e:
            print(f"[portfolio_engine] Skipping {ticker}: {e}")
            continue

    return all_candidates


# ----------------------------
# Portfolio optimizer
# ----------------------------

def optimize_parity_portfolio(
    investment_amount: float,
    max_loss_pct: float,
    time_horizon_days: int,
    objective: str,
    collar_candidates: List[CollarCandidate],
    treasury_ticker: str = "SGOV",
    assumed_treasury_yield: float = 0.045,
) -> dict:

    tier = get_account_tier(investment_amount)

    if tier == "below_minimum":
        return {
            "status": "below_minimum",
            "minimum_account_size": 10000,
            "message": "Minimum account size is $10,000.",
        }

    eligible = [
        c for c in collar_candidates
        if c.ticker in allowed_etfs(tier)
        and c.liquidity_score >= 50
        and collar_capital_required(c) <= investment_amount
        and c.sleeve_max_loss_pct > 0
        and c.sleeve_max_gain_pct > 0
    ]

    max_n = min(max_collars_for_tier(tier), len(eligible))
    possible_portfolios = []

    for n in range(1, max_n + 1):
        for combo in combinations(eligible, n):
            # Prevent duplicate tickers in same portfolio.
            tickers = [c.ticker for c in combo]
            if len(tickers) != len(set(tickers)):
                continue

            collar_capital = sum(collar_capital_required(c) for c in combo)

            if collar_capital > investment_amount:
                continue

            treasury_amount = investment_amount - collar_capital

            portfolio_max_loss_dollars = sum(
                collar_capital_required(c) * c.sleeve_max_loss_pct
                for c in combo
            )

            portfolio_max_gain_dollars = sum(
                collar_capital_required(c) * c.sleeve_max_gain_pct
                for c in combo
            ) + treasury_amount * assumed_treasury_yield * (time_horizon_days / 365)

            actual_max_loss_pct = portfolio_max_loss_dollars / investment_amount
            actual_max_gain_pct = portfolio_max_gain_dollars / investment_amount

            if actual_max_loss_pct > max_loss_pct:
                continue

            avg_liquidity = sum(c.liquidity_score for c in combo) / len(combo)
            treasury_pct = treasury_amount / investment_amount

            if objective == "growth":
                score = actual_max_gain_pct * 100 * 0.60 + avg_liquidity * 0.30 - n * 0.50
            elif objective == "balanced":
                score = actual_max_gain_pct * 100 * 0.45 + avg_liquidity * 0.35 + treasury_pct * 10
            else:
                score = actual_max_gain_pct * 100 * 0.30 + avg_liquidity * 0.30 + treasury_pct * 25

            sleeves = []

            for c in combo:
                capital = collar_capital_required(c)
                spread = collar_spread_cost(c)

                sleeves.append({
                    "type": "collar",
                    "ticker": c.ticker,
                    "exposure": c.exposure,
                    "allocation_dollars": round(capital, 2),
                    "allocation_pct": round(capital / investment_amount, 4),

                    "stock_price": c.stock_price,
                    "shares": c.shares,
                    "contracts": c.contracts,
                    "stock_value": round(c.stock_value, 2),

                    "sleeve_max_loss_pct": round(c.sleeve_max_loss_pct, 4),
                    "sleeve_max_gain_pct": round(c.sleeve_max_gain_pct, 4),

                    "portfolio_max_loss_contribution_dollars": round(capital * c.sleeve_max_loss_pct, 2),
                    "portfolio_max_loss_contribution_pct": round((capital * c.sleeve_max_loss_pct) / investment_amount, 4),
                    "portfolio_max_gain_contribution_dollars": round(capital * c.sleeve_max_gain_pct, 2),
                    "portfolio_max_gain_contribution_pct": round((capital * c.sleeve_max_gain_pct) / investment_amount, 4),

                    "option_legs": {
                        "long_put": asdict(c.long_put),
                        "short_call": asdict(c.short_call),
                    },

                    "option_execution": spread,
                    "liquidity_score": c.liquidity_score,
                    "quote_timestamp": c.quote_timestamp,
                })

            sleeves.append({
                "type": "treasury",
                "ticker": treasury_ticker,
                "exposure": "Treasury Sleeve",
                "allocation_dollars": round(treasury_amount, 2),
                "allocation_pct": round(treasury_pct, 4),
                "assumed_yield": assumed_treasury_yield,
                "estimated_income_dollars": round(
                    treasury_amount * assumed_treasury_yield * (time_horizon_days / 365),
                    2,
                ),
                "sleeve_max_loss_pct": 0,
                "sleeve_max_gain_pct": round(
                    assumed_treasury_yield * (time_horizon_days / 365),
                    4,
                ),
            })

            warnings = []

            for c in combo:
                spread = collar_spread_cost(c)
                if spread["total_option_spread_dollars"] > 100:
                    warnings.append(f"{c.ticker} collar has a wide combined option spread.")

            if any(c.ticker in ["TQQQ", "UPRO"] for c in combo):
                warnings.append(
                    "Portfolio uses leveraged ETFs, which reset daily and may behave differently over longer periods."
                )

            possible_portfolios.append({
                "score": round(score, 4),
                "account_tier": tier,
                "investment_amount": investment_amount,
                "input_max_loss_pct": max_loss_pct,
                "actual_max_loss_dollars": round(portfolio_max_loss_dollars, 2),
                "actual_max_loss_pct": round(actual_max_loss_pct, 4),
                "estimated_max_gain_dollars": round(portfolio_max_gain_dollars, 2),
                "estimated_max_gain_pct": round(actual_max_gain_pct, 4),
                "objective": objective,
                "time_horizon_days": time_horizon_days,
                "treasury_ticker": treasury_ticker,
                "assumed_treasury_yield": assumed_treasury_yield,
                "sleeves": sleeves,
                "warnings": warnings,
            })

    if not possible_portfolios:
        return {
            "status": "no_portfolio_found",
            "message": "No executable portfolio met the user's account size and risk tolerance.",
            "investment_amount": investment_amount,
            "max_loss_pct": max_loss_pct,
            "account_tier": tier,
            "eligible_candidate_count": len(eligible),
            "raw_candidate_count": len(collar_candidates),
        }

    possible_portfolios.sort(key=lambda x: x["score"], reverse=True)

    return {
        "status": "success",
        "recommended_portfolio": possible_portfolios[0],
        "alternatives": possible_portfolios[1:4],
    }