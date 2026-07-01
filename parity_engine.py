# parity_engine.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional
from datetime import datetime, timezone

import pandas as pd

from parity_collar_engine import fetch_orats_chain, clean_chain


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
    expiration: str
    dte: float
    long_put: OptionLeg
    short_call: OptionLeg
    sleeve_max_loss_pct: float
    sleeve_max_gain_pct: float
    liquidity_score: float
    quote_timestamp: str


LAST_DEBUG = []


def annualize_return(total_return: float, dte: float) -> float:
    if dte <= 0:
        return total_return
    return (1 + total_return) ** (365 / dte) - 1


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


def _strike_col(df: pd.DataFrame) -> str:
    c = _col(df, ["strike", "strikePrice", "strike_price"])
    if not c:
        raise ValueError(f"Could not find strike column. Columns: {list(df.columns)}")
    return c


def _dte_col(df: pd.DataFrame) -> str:
    c = _col(df, ["dte", "daysToExpiration", "days_to_expiration"])
    if not c:
        raise ValueError(f"Could not find dte column. Columns: {list(df.columns)}")
    return c


def _expiry_col(df: pd.DataFrame) -> str:
    c = _col(df, ["expirDate", "expiration", "expirationDate", "expDate", "expiry"])
    if not c:
        raise ValueError(f"Could not find expiration column. Columns: {list(df.columns)}")
    return c


def _stock_price(df: pd.DataFrame) -> float:
    c = _col(df, [
        "stockPrice", "stock_price", "underlyingPrice", "underlying_price",
        "spot", "spotPrice", "price", "last"
    ])

    if c:
        vals = pd.to_numeric(df[c], errors="coerce").dropna()
        if not vals.empty:
            return float(vals.iloc[0])

    strike_col = _strike_col(df)
    vals = pd.to_numeric(df[strike_col], errors="coerce").dropna()
    if not vals.empty:
        return float(vals.median())

    raise ValueError("Could not infer stock price.")


def _bid_ask_mid(row, option_type: str) -> tuple[float, float, float]:
    if option_type == "put":
        bid = _num(row, ["putBidPrice", "putBid", "pBid", "put_bid", "bid"])
        ask = _num(row, ["putAskPrice", "putAsk", "pAsk", "put_ask", "ask"])
        mid = _num(row, ["putValue", "putMid", "pMid", "put_mid", "mid"], (bid + ask) / 2 if ask > 0 else bid)
    else:
        bid = _num(row, ["callBidPrice", "callBid", "cBid", "call_bid", "bid"])
        ask = _num(row, ["callAskPrice", "callAsk", "cAsk", "call_ask", "ask"])
        mid = _num(row, ["callValue", "callMid", "cMid", "call_mid", "mid"], (bid + ask) / 2 if ask > 0 else bid)

    if mid <= 0 and bid > 0 and ask > 0:
        mid = (bid + ask) / 2

    return bid, ask, mid


def _volume_oi(row, option_type: str) -> tuple[int, int]:
    if option_type == "put":
        volume = _num(row, ["putVolume", "pVolu", "pVolume", "put_volume", "volume"], 0)
        oi = _num(row, ["putOpenInterest", "pOpenInterest", "pOI", "put_open_interest", "open_interest"], 0)
    else:
        volume = _num(row, ["callVolume", "cVolu", "cVolume", "call_volume", "volume"], 0)
        oi = _num(row, ["callOpenInterest", "cOpenInterest", "cOI", "call_open_interest", "open_interest"], 0)

    return int(volume or 0), int(oi or 0)


def _leg(row, option_type: str, side: str, strike: float, expiration: str) -> OptionLeg:
    bid, ask, mid = _bid_ask_mid(row, option_type)
    volume, oi = _volume_oi(row, option_type)

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


def get_viable_expiry_groups(
    chain: pd.DataFrame,
    target_dte: int,
    min_dte: int = 120,
    max_dte: int = 750,
    max_expiries: int = 4,
) -> list[tuple[str, float, pd.DataFrame]]:
    expiry_col = _expiry_col(chain)
    dte_col = _dte_col(chain)

    df = chain.copy()
    df[dte_col] = pd.to_numeric(df[dte_col], errors="coerce")
    df = df.dropna(subset=[dte_col, expiry_col])

    grouped = []

    for expiry, g in df.groupby(expiry_col):
        dte_values = pd.to_numeric(g[dte_col], errors="coerce").dropna()
        if dte_values.empty:
            continue

        dte = float(dte_values.iloc[0])

        if min_dte <= dte <= max_dte:
            grouped.append((str(expiry), dte, g.copy()))

    grouped.sort(key=lambda x: abs(x[1] - target_dte))
    return grouped[:max_expiries]


def _liquidity_score(put: OptionLeg, call: OptionLeg) -> float:
    put_spread_pct = (put.ask - put.bid) / put.mid if put.mid > 0 else 1
    call_spread_pct = (call.ask - call.bid) / call.mid if call.mid > 0 else 1
    avg_spread_pct = max((put_spread_pct + call_spread_pct) / 2, 0)
    return round(max(0, 100 - avg_spread_pct * 400), 2)


def collar_option_cost(c: CollarCandidate) -> float:
    return (c.long_put.ask - c.short_call.bid) * 100 * c.contracts


def collar_capital_required(c: CollarCandidate) -> float:
    return c.stock_value + collar_option_cost(c)


def collar_loss_dollars(c: CollarCandidate) -> float:
    return collar_capital_required(c) * c.sleeve_max_loss_pct


def collar_gain_dollars(c: CollarCandidate) -> float:
    return collar_capital_required(c) * c.sleeve_max_gain_pct


def collar_annualized_gain_dollars(c: CollarCandidate) -> float:
    return collar_capital_required(c) * annualize_return(c.sleeve_max_gain_pct, c.dte)


def collar_efficiency(c: CollarCandidate) -> float:
    loss = max(collar_loss_dollars(c), 1)
    return collar_annualized_gain_dollars(c) / loss


def collar_spread_cost(c: CollarCandidate) -> dict:
    put_spread = max(c.long_put.ask - c.long_put.bid, 0)
    call_spread = max(c.short_call.ask - c.short_call.bid, 0)

    total_spread = (put_spread + call_spread) * 100 * c.contracts
    net_mid = (c.long_put.mid - c.short_call.mid) * 100 * c.contracts
    net_conservative = collar_option_cost(c)

    return {
        "put_bid_ask_spread": round(put_spread, 4),
        "call_bid_ask_spread": round(call_spread, 4),
        "total_option_spread_dollars": round(total_spread, 2),
        "net_option_mid_dollars": round(net_mid, 2),
        "net_option_conservative_dollars": round(net_conservative, 2),
        "estimated_slippage_from_mid_dollars": round(net_conservative - net_mid, 2),
    }


def execution_quality_passes(c: CollarCandidate) -> bool:
    spread = collar_spread_cost(c)
    capital = max(collar_capital_required(c), 1)

    if spread["total_option_spread_dollars"] > 1000:
        return False

    if spread["total_option_spread_dollars"] / capital > 0.12:
        return False

    return True


def build_collar_candidates_for_expiry(
    expiry_chain: pd.DataFrame,
    ticker: str,
    expiration: str,
    dte: float,
) -> list[CollarCandidate]:

    if expiry_chain is None or expiry_chain.empty:
        return []

    chain = expiry_chain.copy()
    strike_col = _strike_col(chain)
    stock_price = _stock_price(chain)
    timestamp = datetime.now(timezone.utc).isoformat()

    chain[strike_col] = pd.to_numeric(chain[strike_col], errors="coerce")
    chain = chain.dropna(subset=[strike_col]).sort_values(strike_col)

    puts = chain[
        (chain[strike_col] >= stock_price * 0.30)
        & (chain[strike_col] <= stock_price * 1.00)
    ].copy()

    calls = chain[
        (chain[strike_col] >= stock_price * 1.01)
        & (chain[strike_col] <= stock_price * 3.00)
    ].copy()

    if puts.empty or calls.empty:
        return []

    # Instead of brute forcing every put/call combination, keep targeted strikes:
    # - low-loss puts near spot
    # - lower-cost puts farther OTM
    # - calls near spot for income
    # - calls farther OTM for upside
    puts["put_moneyness"] = puts[strike_col] / stock_price
    calls["call_moneyness"] = calls[strike_col] / stock_price

    put_targets = [0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.60, 0.50]
    call_targets = [1.05, 1.10, 1.15, 1.25, 1.40, 1.60, 2.00, 2.50]

    selected_puts = []
    selected_calls = []

    for target in put_targets:
        nearest = (
            puts.assign(distance=(puts["put_moneyness"] - target).abs())
            .sort_values("distance")
            .head(2)
        )
        selected_puts.append(nearest)

    for target in call_targets:
        nearest = (
            calls.assign(distance=(calls["call_moneyness"] - target).abs())
            .sort_values("distance")
            .head(2)
        )
        selected_calls.append(nearest)

    puts = (
        pd.concat(selected_puts)
        .drop_duplicates(subset=[strike_col])
        .sort_values(strike_col)
    )

    calls = (
        pd.concat(selected_calls)
        .drop_duplicates(subset=[strike_col])
        .sort_values(strike_col)
    )

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

            candidate = CollarCandidate(
                ticker=ticker.upper(),
                exposure=exposure_name(ticker),
                shares=shares,
                contracts=contracts,
                stock_price=round(stock_price, 4),
                stock_value=round(stock_value, 2),
                expiration=str(expiration),
                dte=float(dte),
                long_put=put,
                short_call=call,
                sleeve_max_loss_pct=round(sleeve_max_loss_pct, 4),
                sleeve_max_gain_pct=round(sleeve_max_gain_pct, 4),
                liquidity_score=_liquidity_score(put, call),
                quote_timestamp=timestamp,
            )

            if execution_quality_passes(candidate):
                candidates.append(candidate)

    return candidates

def pareto_reduce(candidates: list[CollarCandidate], max_per_etf: int = 10) -> list[CollarCandidate]:
    """
    Keep the Pareto frontier:
    no collar should be kept if another collar has lower/equal loss
    and higher/equal annualized gain.
    """
    frontier = []

    sorted_candidates = sorted(
        candidates,
        key=lambda c: (c.sleeve_max_loss_pct, -annualize_return(c.sleeve_max_gain_pct, c.dte)),
    )

    best_gain_so_far = -1.0

    for c in sorted_candidates:
        annual_gain = annualize_return(c.sleeve_max_gain_pct, c.dte)

        if annual_gain > best_gain_so_far:
            frontier.append(c)
            best_gain_so_far = annual_gain

    # Keep a balanced mix: efficient, low-loss, and high-upside.
    by_efficiency = sorted(frontier, key=collar_efficiency, reverse=True)[:max_per_etf]
    by_low_loss = sorted(frontier, key=lambda c: c.sleeve_max_loss_pct)[:max_per_etf]
    by_upside = sorted(
        frontier,
        key=lambda c: annualize_return(c.sleeve_max_gain_pct, c.dte),
        reverse=True,
    )[:max_per_etf]

    deduped = {}
    for c in by_efficiency + by_low_loss + by_upside:
        key = (c.ticker, c.expiration, c.long_put.strike, c.short_call.strike)
        deduped[key] = c

    return list(deduped.values())


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
            "expiry_groups": [],
            "candidate_count_before_pareto": 0,
            "candidate_count_after_pareto": 0,
            "error": None,
        }

        try:
            raw_df = fetch_orats_chain(ticker=ticker, token=token)
            debug["raw_rows"] = len(raw_df)
            debug["raw_columns"] = list(raw_df.columns)

            chain = clean_chain(raw_df, ticker=ticker)
            debug["clean_rows"] = len(chain)
            debug["clean_columns"] = list(chain.columns)

            expiry_groups = get_viable_expiry_groups(
                chain=chain,
                target_dte=time_horizon_days,
                min_dte=max(120, time_horizon_days - 180),
                max_dte=time_horizon_days + 390,
                max_expiries=4,
            )

            ticker_candidates = []

            for expiry, dte, expiry_chain in expiry_groups:
                candidates = build_collar_candidates_for_expiry(
                    expiry_chain=expiry_chain,
                    ticker=ticker,
                    expiration=expiry,
                    dte=dte,
                )

                debug["expiry_groups"].append({
                    "expiration": expiry,
                    "dte": dte,
                    "rows": len(expiry_chain),
                    "candidates": len(candidates),
                })

                ticker_candidates.extend(candidates)

            debug["candidate_count_before_pareto"] = len(ticker_candidates)

            ticker_candidates = pareto_reduce(
                ticker_candidates,
                max_per_etf=10,
            )

            debug["candidate_count_after_pareto"] = len(ticker_candidates)
            debug["stage"] = "complete"

            all_candidates.extend(ticker_candidates)

        except Exception as e:
            debug["stage"] = "error"
            debug["error"] = repr(e)

        LAST_DEBUG.append(debug)

    return all_candidates


def make_treasury_only_portfolio(
    investment_amount: float,
    max_loss_pct: float,
    time_horizon_days: int,
    treasury_ticker: str,
    assumed_treasury_yield: float,
    tier: str,
    reason: str,
) -> dict:
    income = investment_amount * assumed_treasury_yield * (time_horizon_days / 365)
    total_return = income / investment_amount

    return {
        "status": "success",
        "portfolio": {
            "portfolio_type": "treasury_only_fallback",
            "account_tier": tier,
            "investment_amount": investment_amount,
            "input_max_loss_pct": max_loss_pct,
            "actual_max_loss_dollars": 0,
            "actual_max_loss_pct": 0,
            "estimated_max_gain_dollars": round(income, 2),
            "estimated_max_gain_pct": round(total_return, 4),
            "estimated_max_gain_annualized_pct": assumed_treasury_yield,
            "time_horizon_days": time_horizon_days,
            "treasury_ticker": treasury_ticker,
            "assumed_treasury_yield": assumed_treasury_yield,
            "sleeves": [
                {
                    "type": "treasury",
                    "ticker": treasury_ticker,
                    "exposure": "Treasury Sleeve",
                    "allocation_dollars": round(investment_amount, 2),
                    "allocation_pct": 1.0,
                    "assumed_yield": assumed_treasury_yield,
                    "estimated_income_dollars": round(income, 2),
                    "sleeve_max_loss_pct": 0,
                    "sleeve_max_gain_pct": round(total_return, 4),
                    "sleeve_max_gain_annualized_pct": assumed_treasury_yield,
                }
            ],
            "warnings": [reason],
            "portfolio_summary": {
                "collar_allocation_dollars": 0,
                "treasury_allocation_dollars": round(investment_amount, 2),
                "collar_allocation_pct": 0,
                "treasury_allocation_pct": 1.0,
                "expected_floor_pct": 0,
                "expected_cap_pct": round(total_return, 4),
                "expected_cap_annualized_pct": assumed_treasury_yield,
            },
        },
        "debug": LAST_DEBUG,
    }


def build_portfolio_summary(
    sleeves: list[dict],
    investment_amount: float,
    actual_max_loss_pct: float,
    estimated_max_gain_pct: float,
    estimated_max_gain_annualized_pct: float,
) -> dict:
    collar_amount = sum(s["allocation_dollars"] for s in sleeves if s["type"] == "collar")
    treasury_amount = sum(s["allocation_dollars"] for s in sleeves if s["type"] == "treasury")

    return {
        "collar_allocation_dollars": round(collar_amount, 2),
        "treasury_allocation_dollars": round(treasury_amount, 2),
        "collar_allocation_pct": round(collar_amount / investment_amount, 4),
        "treasury_allocation_pct": round(treasury_amount / investment_amount, 4),
        "expected_floor_pct": round(-actual_max_loss_pct, 4),
        "expected_cap_pct": round(estimated_max_gain_pct, 4),
        "expected_cap_annualized_pct": round(estimated_max_gain_annualized_pct, 4),
    }


def optimize_parity_portfolio(
    investment_amount: float,
    max_loss_pct: float,
    time_horizon_days: int,
    collar_candidates: list[CollarCandidate],
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
        and execution_quality_passes(c)
    ]

    if not eligible:
        return make_treasury_only_portfolio(
            investment_amount,
            max_loss_pct,
            time_horizon_days,
            treasury_ticker,
            assumed_treasury_yield,
            tier,
            "No executable collar passed the current option filters, so the portfolio is allocated to the Treasury sleeve.",
        )

    risk_budget = investment_amount * max_loss_pct
    risk_remaining = risk_budget
    capital_remaining = investment_amount

    max_concentration_pct = {
        "tier_1": 1.00,
        "tier_2": 0.50,
        "tier_3": 0.40,
        "tier_4": 0.35,
    }.get(tier, 0.40)

    positions: list[tuple[CollarCandidate, int]] = []

    # Sort by annualized gain per dollar of risk consumed.
    ranked = sorted(eligible, key=collar_efficiency, reverse=True)

    used_tickers = set()

    for c in ranked:
        capital = collar_capital_required(c)
        loss = collar_loss_dollars(c)

        if capital <= 0 or loss <= 0:
            continue

        if capital > capital_remaining:
            continue

        if loss > risk_remaining:
            continue

        current_ticker_capital = sum(
            collar_capital_required(pos) * lots
            for pos, lots in positions
            if pos.ticker == c.ticker
        )

        max_capital_for_ticker = investment_amount * max_concentration_pct
        remaining_ticker_capacity = max_capital_for_ticker - current_ticker_capital

        if remaining_ticker_capacity < capital:
            continue

        max_by_capital = int(capital_remaining // capital)
        max_by_risk = int(risk_remaining // loss)
        max_by_concentration = int(remaining_ticker_capacity // capital)

        max_lots = min(max_by_capital, max_by_risk, max_by_concentration)

        if max_lots <= 0:
            continue

        # MVP: use one lot per ETF first to maintain diversification.
        # Larger accounts can allow more lots per ETF after initial rollout.
        lots = 1

        positions.append((c, lots))
        used_tickers.add(c.ticker)

        capital_remaining -= capital * lots
        risk_remaining -= loss * lots

    if not positions:
        return make_treasury_only_portfolio(
            investment_amount,
            max_loss_pct,
            time_horizon_days,
            treasury_ticker,
            assumed_treasury_yield,
            tier,
            "The selected max loss target is tighter than the minimum collar size allows, so the portfolio is allocated to the Treasury sleeve.",
        )

    treasury_amount = capital_remaining
    collar_capital = sum(collar_capital_required(c) * lots for c, lots in positions)

    loss_dollars = sum(collar_loss_dollars(c) * lots for c, lots in positions)
    gain_dollars = sum(collar_gain_dollars(c) * lots for c, lots in positions)

    gain_dollars += treasury_amount * assumed_treasury_yield * (time_horizon_days / 365)

    actual_loss_pct = loss_dollars / investment_amount
    actual_gain_pct = gain_dollars / investment_amount

    weighted_dte = sum(
        collar_capital_required(c) * lots * c.dte
        for c, lots in positions
    ) / max(collar_capital, 1)

    estimated_max_gain_annualized_pct = annualize_return(actual_gain_pct, weighted_dte)

    sleeves = []

    for c, lots in positions:
        capital = collar_capital_required(c) * lots
        spread = collar_spread_cost(c)

        sleeves.append({
            "type": "collar",
            "ticker": c.ticker,
            "exposure": c.exposure,
            "allocation_dollars": round(capital, 2),
            "allocation_pct": round(capital / investment_amount, 4),
            "minimum_executable_collar_cost": round(collar_capital_required(c), 2),

            "stock_price": c.stock_price,
            "shares": c.shares * lots,
            "contracts": c.contracts * lots,
            "lots": lots,
            "stock_value": round(c.stock_value * lots, 2),

            "expiration": c.expiration,
            "dte": c.dte,

            "sleeve_max_loss_pct": round(c.sleeve_max_loss_pct, 4),
            "sleeve_max_gain_pct": round(c.sleeve_max_gain_pct, 4),
            "sleeve_max_gain_annualized_pct": round(
                annualize_return(c.sleeve_max_gain_pct, c.dte),
                4,
            ),

            "portfolio_max_loss_contribution_dollars": round(collar_loss_dollars(c) * lots, 2),
            "portfolio_max_loss_contribution_pct": round((collar_loss_dollars(c) * lots) / investment_amount, 4),
            "portfolio_max_gain_contribution_dollars": round(collar_gain_dollars(c) * lots, 2),
            "portfolio_max_gain_contribution_pct": round((collar_gain_dollars(c) * lots) / investment_amount, 4),

            "option_legs": {
                "long_put": asdict(c.long_put),
                "short_call": asdict(c.short_call),
            },

            "option_execution": spread,
            "execution_quality_passed": execution_quality_passes(c),
            "liquidity_score": c.liquidity_score,
            "efficiency": round(collar_efficiency(c), 4),
            "quote_timestamp": c.quote_timestamp,
        })

    sleeves.append({
        "type": "treasury",
        "ticker": treasury_ticker,
        "exposure": "Treasury Sleeve",
        "allocation_dollars": round(treasury_amount, 2),
        "allocation_pct": round(treasury_amount / investment_amount, 4),
        "assumed_yield": assumed_treasury_yield,
        "estimated_income_dollars": round(
            treasury_amount * assumed_treasury_yield * (time_horizon_days / 365),
            2,
        ),
        "sleeve_max_loss_pct": 0,
        "sleeve_max_gain_pct": round(assumed_treasury_yield * (time_horizon_days / 365), 4),
        "sleeve_max_gain_annualized_pct": assumed_treasury_yield,
    })

    warnings = []

    if any(c.ticker in ["TQQQ", "UPRO"] for c, _ in positions):
        warnings.append(
            "Portfolio uses leveraged ETFs, which reset daily and may behave differently over longer periods."
        )

    for c, _ in positions:
        spread = collar_spread_cost(c)

        if spread["total_option_spread_dollars"] > 400:
            warnings.append(f"{c.ticker} collar has a wide combined option spread.")

        if abs(c.dte - time_horizon_days) > 120:
            warnings.append(
                f"{c.ticker} uses an expiration {int(c.dte)} days out, which differs from the requested {time_horizon_days}-day horizon."
            )

    portfolio = {
        "portfolio_type": "risk_budget_greedy",
        "account_tier": tier,
        "investment_amount": investment_amount,
        "input_max_loss_pct": max_loss_pct,
        "actual_max_loss_dollars": round(loss_dollars, 2),
        "actual_max_loss_pct": round(actual_loss_pct, 4),
        "estimated_max_gain_dollars": round(gain_dollars, 2),
        "estimated_max_gain_pct": round(actual_gain_pct, 4),
        "estimated_max_gain_annualized_pct": round(estimated_max_gain_annualized_pct, 4),
        "weighted_option_dte": round(weighted_dte, 1),
        "time_horizon_days": time_horizon_days,
        "treasury_ticker": treasury_ticker,
        "assumed_treasury_yield": assumed_treasury_yield,
        "risk_budget_dollars": round(risk_budget, 2),
        "risk_used_dollars": round(loss_dollars, 2),
        "risk_remaining_dollars": round(max(risk_budget - loss_dollars, 0), 2),
        "capital_used_dollars": round(investment_amount - treasury_amount, 2),
        "capital_remaining_dollars": round(treasury_amount, 2),
        "sleeves": sleeves,
        "warnings": warnings,
    }

    portfolio["portfolio_summary"] = build_portfolio_summary(
        sleeves=sleeves,
        investment_amount=investment_amount,
        actual_max_loss_pct=actual_loss_pct,
        estimated_max_gain_pct=actual_gain_pct,
        estimated_max_gain_annualized_pct=estimated_max_gain_annualized_pct,
    )

    return {
        "status": "success",
        "portfolio": portfolio,
        "debug": LAST_DEBUG,
    }