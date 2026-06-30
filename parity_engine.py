# parity_engine.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import combinations
from typing import List, Optional
from datetime import datetime, timezone

import pandas as pd

from parity_collar_engine import (
    fetch_orats_chain,
    clean_chain,
    select_single_expiry,
)


@dataclass
class OptionLeg:
    side: str
    option_type: str
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


LAST_DEBUG = []


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


def _col(df: pd.DataFrame, names: list[str]) -> Optional[str]:
    lower = {c.lower(): c for c in df.columns}
    for n in names:
        if n in df.columns:
            return n
        if n.lower() in lower:
            return lower[n.lower()]
    return None


def _num(row, names: list[str], default: float = 0.0) -> float:
    lower = {str(c).lower(): c for c in row.index}
    for n in names:
        c = n if n in row.index else lower.get(n.lower())
        if c is not None:
            v = pd.to_numeric(row[c], errors="coerce")
            if pd.notna(v):
                return float(v)
    return float(default)


def _stock_price(df: pd.DataFrame) -> float:
    c = _col(df, [
        "stockPrice", "stock_price", "underlyingPrice", "underlying_price",
        "underlying", "spot", "price", "stkPx", "uPx", "last"
    ])
    if c:
        vals = pd.to_numeric(df[c], errors="coerce").dropna()
        if not vals.empty:
            return float(vals.iloc[0])

    # Fallback: infer from strike closest to where call/put mids are similar.
    strike_col = _strike_col(df)
    df2 = df.copy()
    df2[strike_col] = pd.to_numeric(df2[strike_col], errors="coerce")
    df2 = df2.dropna(subset=[strike_col])
    if not df2.empty:
        return float(df2[strike_col].median())

    raise ValueError(f"Could not infer stock price. Columns: {list(df.columns)}")


def _strike_col(df: pd.DataFrame) -> str:
    c = _col(df, ["strike", "strikePrice", "strike_price"])
    if not c:
        raise ValueError(f"Could not find strike column. Columns: {list(df.columns)}")
    return c


def _expiration(df: pd.DataFrame) -> str:
    c = _col(df, ["expiration", "expirDate", "expirationDate", "expDate", "expiry", "tradeDate"])
    if not c:
        return "unknown"
    vals = df[c].dropna()
    return str(vals.iloc[0]) if not vals.empty else "unknown"


def _bid_ask_mid(row, option_type: str) -> tuple[float, float, float]:
    if option_type == "put":
        bid = _num(row, ["putBid", "pBid", "put_bid", "bid"])
        ask = _num(row, ["putAsk", "pAsk", "put_ask", "ask"])
        mid = _num(row, ["putMid", "pMid", "put_mid", "mid"], (bid + ask) / 2 if ask > 0 else bid)
    else:
        bid = _num(row, ["callBid", "cBid", "call_bid", "bid"])
        ask = _num(row, ["callAsk", "cAsk", "call_ask", "ask"])
        mid = _num(row, ["callMid", "cMid", "call_mid", "mid"], (bid + ask) / 2 if ask > 0 else bid)

    if mid <= 0 and bid > 0 and ask > 0:
        mid = (bid + ask) / 2

    return bid, ask, mid


def _volume_oi(row, option_type: str) -> tuple[int, int]:
    if option_type == "put":
        vol = _num(row, ["putVolume", "pVolu", "pVolume", "put_volume", "volume"], 0)
        oi = _num(row, ["putOpenInterest", "pOpenInterest", "pOI", "put_open_interest", "open_interest"], 0)
    else:
        vol = _num(row, ["callVolume", "cVolu", "cVolume", "call_volume", "volume"], 0)
        oi = _num(row, ["callOpenInterest", "cOpenInterest", "cOI", "call_open_interest", "open_interest"], 0)

    return int(vol or 0), int(oi or 0)


def _leg(row, option_type: str, side: str, strike: float, expiration: str) -> OptionLeg:
    bid, ask, mid = _bid_ask_mid(row, option_type)
    vol, oi = _volume_oi(row, option_type)

    return OptionLeg(
        side=side,
        option_type=option_type,
        strike=float(strike),
        expiration=str(expiration),
        bid=round(float(bid), 4),
        ask=round(float(ask), 4),
        mid=round(float(mid), 4),
        volume=int(vol),
        open_interest=int(oi),
    )


def _liquidity_score(put: OptionLeg, call: OptionLeg) -> float:
    put_spread_pct = (put.ask - put.bid) / put.mid if put.mid > 0 else 1
    call_spread_pct = (call.ask - call.bid) / call.mid if call.mid > 0 else 1

    avg_spread_pct = max((put_spread_pct + call_spread_pct) / 2, 0)
    total_oi = put.open_interest + call.open_interest
    total_volume = put.volume + call.volume

    spread_score = max(0, 100 - avg_spread_pct * 400)
    oi_score = min(100, total_oi / 10)
    volume_score = min(100, total_volume / 2)

    return round(spread_score * 0.60 + oi_score * 0.25 + volume_score * 0.15, 2)


def collar_option_cost(c: CollarCandidate) -> float:
    return (c.long_put.ask - c.short_call.bid) * 100 * c.contracts


def collar_capital_required(c: CollarCandidate) -> float:
    return c.stock_value + collar_option_cost(c)


def collar_spread_cost(c: CollarCandidate) -> dict:
    put_spread = max(c.long_put.ask - c.long_put.bid, 0)
    call_spread = max(c.short_call.ask - c.short_call.bid, 0)

    return {
        "put_bid_ask_spread": round(put_spread, 4),
        "call_bid_ask_spread": round(call_spread, 4),
        "total_option_spread_dollars": round((put_spread + call_spread) * 100 * c.contracts, 2),
        "net_option_mid_dollars": round((c.long_put.mid - c.short_call.mid) * 100 * c.contracts, 2),
        "net_option_conservative_dollars": round(collar_option_cost(c), 2),
    }


def build_collar_candidates_for_expiry(
    expiry_chain: pd.DataFrame,
    ticker: str,
    max_candidates: int = 50,
    min_liquidity_score: float = 0,
) -> list[CollarCandidate]:

    if expiry_chain is None or expiry_chain.empty:
        return []

    chain = expiry_chain.copy()
    strike_col = _strike_col(chain)
    stock_price = _stock_price(chain)
    expiration = _expiration(chain)
    timestamp = datetime.now(timezone.utc).isoformat()

    chain[strike_col] = pd.to_numeric(chain[strike_col], errors="coerce")
    chain = chain.dropna(subset=[strike_col]).sort_values(strike_col)

    # Wider filters for debugging / MVP.
    puts = chain[(chain[strike_col] >= stock_price * 0.50) & (chain[strike_col] <= stock_price * 1.00)]
    calls = chain[(chain[strike_col] >= stock_price * 1.01) & (chain[strike_col] <= stock_price * 2.50)]

    candidates = []

    for _, put_row in puts.iterrows():
        put_strike = float(put_row[strike_col])
        put = _leg(put_row, "put", "buy", put_strike, expiration)

        if put.ask <= 0:
            continue

        for _, call_row in calls.iterrows():
            call_strike = float(call_row[strike_col])
            call = _leg(call_row, "call", "sell", call_strike, expiration)

            if call.bid <= 0:
                continue

            shares = 100
            contracts = 1
            stock_value = stock_price * shares
            net_option_cost = (put.ask - call.bid) * 100

            capital = stock_value + net_option_cost
            if capital <= 0:
                continue

            max_loss_dollars = max(0, capital - put_strike * 100)
            max_gain_dollars = max(0, call_strike * 100 - capital)

            sleeve_max_loss_pct = max_loss_dollars / capital
            sleeve_max_gain_pct = max_gain_dollars / capital

            if sleeve_max_loss_pct <= 0 or sleeve_max_gain_pct <= 0:
                continue

            liq = _liquidity_score(put, call)

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
                    long_put=put,
                    short_call=call,
                    sleeve_max_loss_pct=round(sleeve_max_loss_pct, 4),
                    sleeve_max_gain_pct=round(sleeve_max_gain_pct, 4),
                    liquidity_score=liq,
                    quote_timestamp=timestamp,
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
) -> list[CollarCandidate]:

    global LAST_DEBUG
    LAST_DEBUG = []

    tier = get_account_tier(investment_amount)
    if tier == "below_minimum":
        return []

    all_candidates = []

    for ticker in allowed_etfs(tier):
        debug = {
            "ticker": ticker,
            "stage": "start",
            "raw_rows": None,
            "clean_rows": None,
            "expiry_rows": None,
            "candidate_count": 0,
            "error": None,
        }

        try:
            raw_df = fetch_orats_chain(ticker=ticker, token=token)
            debug["raw_rows"] = len(raw_df)
            debug["raw_columns"] = list(raw_df.columns)

            chain = clean_chain(raw_df, ticker=ticker)
            debug["clean_rows"] = len(chain)
            debug["clean_columns"] = list(chain.columns)

            expiry_chain, selected_expiry_summary, _ = select_single_expiry(
                chain,
                target_dte=time_horizon_days,
                prefer_at_or_after=True,
                max_dte_overage=250,
            )

            debug["selected_expiry"] = selected_expiry_summary
            debug["expiry_rows"] = len(expiry_chain)
            debug["expiry_columns"] = list(expiry_chain.columns)

            candidates = build_collar_candidates_for_expiry(
                expiry_chain=expiry_chain,
                ticker=ticker,
                max_candidates=50,
                min_liquidity_score=0,
            )

            debug["candidate_count"] = len(candidates)
            debug["stage"] = "complete"

            all_candidates.extend(candidates)

        except Exception as e:
            debug["stage"] = "error"
            debug["error"] = repr(e)

        LAST_DEBUG.append(debug)

    return all_candidates


def optimize_parity_portfolio(
    investment_amount: float,
    max_loss_pct: float,
    time_horizon_days: int,
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
            "debug": LAST_DEBUG,
        }

    eligible = [
        c for c in collar_candidates
        if c.ticker in allowed_etfs(tier)
        and collar_capital_required(c) <= investment_amount
        and c.sleeve_max_loss_pct > 0
        and c.sleeve_max_gain_pct > 0
    ]

    max_n = min(max_collars_for_tier(tier), len(eligible))
    possible = []

    for n in range(1, max_n + 1):
        for combo in combinations(eligible, n):
            tickers = [c.ticker for c in combo]
            if len(tickers) != len(set(tickers)):
                continue

            collar_capital = sum(collar_capital_required(c) for c in combo)
            if collar_capital > investment_amount:
                continue

            treasury_amount = investment_amount - collar_capital
            treasury_pct = treasury_amount / investment_amount

            loss_dollars = sum(collar_capital_required(c) * c.sleeve_max_loss_pct for c in combo)
            gain_dollars = sum(collar_capital_required(c) * c.sleeve_max_gain_pct for c in combo)
            gain_dollars += treasury_amount * assumed_treasury_yield * (time_horizon_days / 365)

            actual_loss_pct = loss_dollars / investment_amount
            actual_gain_pct = gain_dollars / investment_amount

            if actual_loss_pct > max_loss_pct:
                continue

            avg_liq = sum(c.liquidity_score for c in combo) / len(combo)

            # Single objective: maximize upside, with minor liquidity penalty/benefit.
            score = actual_gain_pct * 100 * 0.75 + avg_liq * 0.25

            sleeves = []

            for c in combo:
                capital = collar_capital_required(c)

                sleeves.append({
                    "type": "collar",
                    "ticker": c.ticker,
                    "exposure": c.exposure,
                    "allocation_dollars": round(capital, 2),
                    "allocation_pct": round(capital / investment_amount, 4),
                    "minimum_executable_collar_cost": round(capital, 2),

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
                    "option_execution": collar_spread_cost(c),
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
                "sleeve_max_gain_pct": round(assumed_treasury_yield * (time_horizon_days / 365), 4),
            })

            warnings = []

            if any(c.ticker in ["TQQQ", "UPRO"] for c in combo):
                warnings.append(
                    "Portfolio uses leveraged ETFs, which reset daily and may behave differently over longer periods."
                )

            possible.append({
                "score": round(score, 4),
                "account_tier": tier,
                "investment_amount": investment_amount,
                "input_max_loss_pct": max_loss_pct,
                "actual_max_loss_dollars": round(loss_dollars, 2),
                "actual_max_loss_pct": round(actual_loss_pct, 4),
                "estimated_max_gain_dollars": round(gain_dollars, 2),
                "estimated_max_gain_pct": round(actual_gain_pct, 4),
                "time_horizon_days": time_horizon_days,
                "treasury_ticker": treasury_ticker,
                "assumed_treasury_yield": assumed_treasury_yield,
                "sleeves": sleeves,
                "warnings": warnings,
            })

    if not possible:
        return {
            "status": "no_portfolio_found",
            "message": "No executable portfolio met the user's account size and risk tolerance.",
            "investment_amount": investment_amount,
            "max_loss_pct": max_loss_pct,
            "account_tier": tier,
            "eligible_candidate_count": len(eligible),
            "raw_candidate_count": len(collar_candidates),
            "debug": LAST_DEBUG,
        }

    possible.sort(key=lambda x: x["score"], reverse=True)

    return {
        "status": "success",
        "recommended_portfolio": possible[0],
        "alternatives": possible[1:4],
        "debug": LAST_DEBUG,
    }