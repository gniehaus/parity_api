# parity_engine.py

from dataclasses import dataclass, asdict
from typing import List, Optional
from itertools import combinations


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


def collar_option_cost(c: CollarCandidate) -> float:
    # Buy put at ask, sell call at bid for conservative execution estimate
    return (c.long_put.ask - c.short_call.bid) * 100 * c.contracts


def collar_capital_required(c: CollarCandidate) -> float:
    return c.stock_value + collar_option_cost(c)


def collar_spread_cost(c: CollarCandidate) -> dict:
    put_spread = c.long_put.ask - c.long_put.bid
    call_spread = c.short_call.ask - c.short_call.bid

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
            "message": "Minimum account size is $10,000."
        }

    eligible = [
        c for c in collar_candidates
        if c.ticker in allowed_etfs(tier)
        and c.liquidity_score >= 70
        and collar_capital_required(c) <= investment_amount
    ]

    max_n = min(max_collars_for_tier(tier), len(eligible))
    possible_portfolios = []

    for n in range(1, max_n + 1):
        for combo in combinations(eligible, n):
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

            if objective == "growth":
                score = actual_max_gain_pct * 100 * 0.60 + avg_liquidity * 0.30 - n * 0.50
            elif objective == "balanced":
                score = actual_max_gain_pct * 100 * 0.45 + avg_liquidity * 0.35 + (treasury_amount / investment_amount) * 10
            else:  # income / conservative
                score = actual_max_gain_pct * 100 * 0.30 + avg_liquidity * 0.30 + (treasury_amount / investment_amount) * 25

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
                "allocation_pct": round(treasury_amount / investment_amount, 4),
                "assumed_yield": assumed_treasury_yield,
                "estimated_income_dollars": round(treasury_amount * assumed_treasury_yield * (time_horizon_days / 365), 2),
                "sleeve_max_loss_pct": 0,
                "sleeve_max_gain_pct": round(assumed_treasury_yield * (time_horizon_days / 365), 4),
            })

            warnings = []

            for c in combo:
                spread = collar_spread_cost(c)
                if spread["total_option_spread_dollars"] > 100:
                    warnings.append(f"{c.ticker} collar has a wide combined option spread.")

            if any(c.ticker in ["TQQQ", "UPRO"] for c in combo):
                warnings.append("Portfolio uses leveraged ETFs, which reset daily and may behave differently over longer periods.")

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
        }

    possible_portfolios.sort(key=lambda x: x["score"], reverse=True)

    return {
        "status": "success",
        "recommended_portfolio": possible_portfolios[0],
        "alternatives": possible_portfolios[1:4],
    }