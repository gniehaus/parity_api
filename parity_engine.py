# parity_engine.py
# Full replacement file

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional
from datetime import datetime, timezone

import pandas as pd
import pulp

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
    bucket: str
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
    assumed_dividend_yield: float = 0.0
    expected_dividend_dollars: float = 0.0
    option_max_gain_dollars: float = 0.0
    option_max_loss_dollars: float = 0.0
    net_option_cost_dollars: float = 0.0
    net_option_cost_bps: float = 0.0
    max_allowed_sleeve_loss_pct: float = 0.0


LAST_DEBUG = []


def annualize_return(total_return: float, dte: float) -> float:
    if dte <= 0:
        return total_return
    return (1 + total_return) ** (365 / dte) - 1


def annualize_loss(total_loss_pct: float, dte: float) -> float:
    """
    Annualizes a positive loss percentage using wealth-multiple compounding.

    Example:
        total_loss_pct = 0.02 over 180 days
        annualized_loss = abs((1 - 0.02) ** (365 / 180) - 1)
                        ≈ 0.040

    Returns a positive number for display and comparison.
    """
    if dte <= 0:
        return total_loss_pct

    loss = max(float(total_loss_pct or 0.0), 0.0)

    if loss >= 1:
        return 1.0

    annualized_return = (1 - loss) ** (365 / dte) - 1
    return abs(annualized_return)


def horizon_return_from_annual(annual_return: float, horizon_days: float) -> float:
    if horizon_days <= 0:
        return annual_return
    return (1 + annual_return) ** (horizon_days / 365) - 1


def normalize_return_to_horizon(total_return: float, source_dte: float, target_horizon_days: float) -> float:
    """
    Converts a return earned over source_dte into a target-horizon return
    using annualized compounding.

    This is used for comparing sleeves with different expirations on the
    same horizon. It assumes the current collar economics can be rolled at
    similar terms, so the UI should label this as horizon-normalized rather
    than guaranteed contractual return.
    """
    annual_return = annualize_return(total_return, source_dte)
    return horizon_return_from_annual(annual_return, target_horizon_days)


def get_dividend_yield(ticker: str, dividend_yields: Optional[dict[str, float]] = None) -> float:
    if not dividend_yields:
        return 0.0

    value = dividend_yields.get(ticker.upper(), 0.0)

    try:
        value = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

    # Accept either decimal form, e.g. 0.025, or percent form, e.g. 2.5.
    if value > 1:
        value = value / 100

    return max(value, 0.0)


def normalize_growth_preference(value: str | None) -> str:
    value = (value or "balanced").lower().strip()

    if value in ["conservative", "protect", "capital_preservation"]:
        return "conservative"
    if value in ["balanced", "default"]:
        return "balanced"
    if value in ["growth", "more_growth"]:
        return "growth"
    if value in ["maximum_growth", "max_growth", "aggressive"]:
        return "maximum_growth"

    return "balanced"


def growth_preference_config(growth_preference: str) -> dict:
    growth_preference = normalize_growth_preference(growth_preference)

    return {
        "conservative": {
            "collar_capital_reward": 0.02,
            "risk_usage_reward": 0.01,
            "sleeve_reward": 75.0,
            "max_lots_per_candidate": 5,
            "min_sleeve_boost": 1,
        },
        "balanced": {
            "collar_capital_reward": 0.08,
            "risk_usage_reward": 0.02,
            "sleeve_reward": 75.0,
            "max_lots_per_candidate": 8,
            "min_sleeve_boost": 1,
        },
        "growth": {
            "collar_capital_reward": 0.15,
            "risk_usage_reward": 0.03,
            "sleeve_reward": 50.0,
            "max_lots_per_candidate": 10,
            "min_sleeve_boost": 0,
        },
        "maximum_growth": {
            "collar_capital_reward": 0.25,
            "risk_usage_reward": 0.04,
            "sleeve_reward": 25.0,
            "max_lots_per_candidate": 12,
            "min_sleeve_boost": 0,
        },
    }[growth_preference]


def max_allowed_sleeve_loss_pct(
    portfolio_max_loss_pct: float,
    growth_preference: str = "balanced",
) -> float:
    """
    Product guardrail for sleeve-level loss.

    Portfolio max loss is a weighted average of sleeve losses, so a sleeve
    may have more loss than the portfolio target. But it should scale with
    the user-selected portfolio floor and should never become a deep-disaster
    hedge like a 40% max-loss collar for a 2% or 5% portfolio.

    Piecewise behavior:
      - conservative: 1.5x portfolio max loss
      - balanced:     2.0x portfolio max loss
      - growth:       2.5x portfolio max loss
      - max growth:   3.0x portfolio max loss

    Always enforce a 7.5% practical minimum and 30% absolute maximum.
    """
    pref = normalize_growth_preference(growth_preference)

    multiplier = {
        "conservative": 1.5,
        "balanced": 2.0,
        "growth": 2.5,
        "maximum_growth": 3.0,
    }.get(pref, 2.0)

    try:
        target = float(portfolio_max_loss_pct or 0.0)
    except (TypeError, ValueError):
        target = 0.0

    return min(0.30, max(0.075, target * multiplier))


def max_ticker_gain_contribution_pct(
    portfolio_max_loss_pct: float,
    growth_preference: str = "balanced",
) -> float:
    """
    Product guardrail for upside concentration.

    We do not want one sleeve to drive most of the portfolio's max gain.
    This caps each ticker's total max-gain contribution as a percentage
    of the full portfolio value. It scales with the user's selected
    portfolio max loss and growth preference.

    Example:
      10% max-loss + growth => 6% max gain contribution per ticker.

    This keeps the optimizer from making one big directional bet while
    still allowing growth portfolios to have more upside concentration
    than conservative portfolios.
    """
    pref = normalize_growth_preference(growth_preference)

    multiplier = {
        "conservative": 0.40,
        "balanced": 0.50,
        "growth": 0.60,
        "maximum_growth": 0.75,
    }.get(pref, 0.50)

    try:
        target = float(portfolio_max_loss_pct or 0.0)
    except (TypeError, ValueError):
        target = 0.0

    return min(0.08, max(0.025, target * multiplier))


def get_account_tier(amount: float) -> str:
    if amount < 10_000:
        return "below_minimum"
    if amount < 25_000:
        return "tier_1"
    if amount < 100_000:
        return "tier_2"
    if amount < 250_000:
        return "tier_3"
    return "tier_4"


def allowed_etfs(tier: str, include_bitcoin: bool = False) -> list[str]:
    universe = {
        # $10k-$25k
        "tier_1": ["TQQQ"],

        # $25k-$100k
        "tier_2": ["TQQQ", "EEM", "EFA"],

        # $100k-$250k
        "tier_3": ["TQQQ", "IWM", "EEM", "EFA"],

        # $250k+
        # Tier 4 upgrades from TQQQ to QQQ to avoid leveraged ETF reset/path dependency.
        "tier_4": ["QQQ", "IWM", "EEM", "EFA"],
    }.get(tier, []).copy()

    # Bitcoin is always opt-in and starts at Tier 2.
    if include_bitcoin and tier in ["tier_2", "tier_3", "tier_4"]:
        universe.append("IBIT")

    return universe


def exposure_name(ticker: str) -> str:
    return {
        "TQQQ": "U.S. Growth",
        "UPRO": "U.S. Large Cap",
        "EEM": "Emerging Markets",
        "EFA": "Developed International",
        "SPY": "U.S. Large Cap",
        "QQQ": "Technology Growth",
        "IWM": "U.S. Small Cap",
        "IBIT": "Bitcoin",
    }.get(ticker.upper(), ticker.upper())


def correlation_bucket(ticker: str) -> str:
    return {
        "TQQQ": "leveraged_us_equity",
        "UPRO": "leveraged_us_equity",
        "SPXL": "leveraged_us_equity",
        "SPY": "us_equity",
        "QQQ": "us_growth",
        "IWM": "us_small_cap",
        "EEM": "emerging_markets",
        "EFA": "developed_international",
        "IBIT": "bitcoin",
    }.get(ticker.upper(), "other")


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
    """
    Pricing assumption used for modeled portfolio economics.

    We use midpoint pricing for the optimization and displayed modeled
    outcome, while separately reporting conservative executable cost
    using buy-at-ask / sell-at-bid in collar_spread_cost().
    """
    return (c.long_put.mid - c.short_call.mid) * 100 * c.contracts


def collar_executable_cost(c: CollarCandidate) -> float:
    """Conservative executable cost: buy put at ask, sell call at bid."""
    return (c.long_put.ask - c.short_call.bid) * 100 * c.contracts


def collar_capital_required(c: CollarCandidate) -> float:
    return c.stock_value + collar_option_cost(c)


def collar_loss_dollars(c: CollarCandidate) -> float:
    """Gross option-floor loss before portfolio-level income offsets.

    Dividends and Treasury income are treated as a portfolio-level cash
    pool that can offset losses across all sleeves. That means TQQQ can
    benefit from EEM/EFA dividends at the portfolio level, instead of each
    sleeve only benefiting from its own income.
    """
    return collar_capital_required(c) * c.sleeve_max_loss_pct


def collar_loss_dollars_after_dividends(c: CollarCandidate) -> float:
    """Deprecated compatibility wrapper. Use collar_loss_dollars()."""
    return max(0.0, collar_loss_dollars(c))


def collar_gain_dollars(c: CollarCandidate) -> float:
    return collar_capital_required(c) * c.sleeve_max_gain_pct


def collar_annualized_gain_dollars(c: CollarCandidate) -> float:
    return collar_capital_required(c) * annualize_return(c.sleeve_max_gain_pct, c.dte)


def collar_horizon_normalized_gain_dollars(c: CollarCandidate, horizon_days: int) -> float:
    return collar_capital_required(c) * normalize_return_to_horizon(
        c.sleeve_max_gain_pct,
        c.dte,
        horizon_days,
    )


def collar_efficiency(c: CollarCandidate) -> float:
    return collar_annualized_gain_dollars(c) / max(collar_loss_dollars(c), 1)


def collar_spread_cost(c: CollarCandidate) -> dict:
    put_spread = max(c.long_put.ask - c.long_put.bid, 0)
    call_spread = max(c.short_call.ask - c.short_call.bid, 0)

    total_spread = (put_spread + call_spread) * 100 * c.contracts
    net_mid = (c.long_put.mid - c.short_call.mid) * 100 * c.contracts
    net_conservative = collar_executable_cost(c)

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
    dividend_yields: Optional[dict[str, float]] = None,
    max_net_option_cost_bps: float = 50.0,
    max_sleeve_loss_pct: float = 0.30,
) -> list[CollarCandidate]:

    if expiry_chain is None or expiry_chain.empty:
        return []

    chain = expiry_chain.copy()
    strike_col = _strike_col(chain)
    stock_price = _stock_price(chain)
    timestamp = datetime.now(timezone.utc).isoformat()
    assumed_dividend_yield = get_dividend_yield(ticker, dividend_yields)

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

    puts["put_moneyness"] = puts[strike_col] / stock_price
    calls["call_moneyness"] = calls[strike_col] / stock_price

    # Search fixed put moneyness targets.
    # Do NOT shift put targets for dividends here. Dividends are treated
    # exactly once as a shared portfolio-level income pool in the MILP.
    # Shifting targets here and also netting dividends in the MILP would
    # create inconsistent filtering and/or double counting.
    put_targets = [
        0.99, 0.98, 0.975, 0.95, 0.925, 0.90,
        0.85, 0.80, 0.75, 0.70, 0.60, 0.50, 0.40,
    ]

    call_targets = [1.03, 1.05, 1.10, 1.15, 1.25, 1.40, 1.60, 2.00, 2.50]

    selected_puts = []
    selected_calls = []

    for target in put_targets:
        selected_puts.append(
            puts.assign(distance=(puts["put_moneyness"] - target).abs())
            .sort_values("distance")
            .head(2)
        )

    for target in call_targets:
        selected_calls.append(
            calls.assign(distance=(calls["call_moneyness"] - target).abs())
            .sort_values("distance")
            .head(2)
        )

    puts = pd.concat(selected_puts).drop_duplicates(subset=[strike_col]).sort_values(strike_col)
    calls = pd.concat(selected_calls).drop_duplicates(subset=[strike_col]).sort_values(strike_col)

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

            # Modeled collar cost uses midpoint pricing.
            # Positive = debit paid. Negative = credit received.
            # Conservative executable cost is still reported separately.
            net_option_cost = (put.mid - call.mid) * 100
            net_option_cost_bps = (net_option_cost / stock_value) * 10_000 if stock_value > 0 else 0.0

            # Do not allow expensive debit/credit collars into the optimizer.
            # We are not modeling theta/option decay, so only keep near-zero-cost
            # structures using the modeled midpoint option cost.
            if abs(net_option_cost_bps) > max_net_option_cost_bps:
                continue

            capital = stock_value + net_option_cost

            if capital <= 0:
                continue

            expected_dividend_dollars = (
                stock_value
                * assumed_dividend_yield
                * (float(dte) / 365.25)
            )

            # Gross option floor excludes dividends. Dividends are later
            # pooled at the portfolio level and can offset losses across
            # all sleeves. Max gain still includes expected dividends.
            floor_value = put_strike * 100
            cap_value = call_strike * 100 + expected_dividend_dollars

            max_loss_dollars = max(0, capital - floor_value)
            option_max_gain_dollars = max(0, call_strike * 100 - capital)
            max_gain_dollars = max(0, cap_value - capital)

            sleeve_max_loss_pct = max_loss_dollars / capital
            sleeve_max_gain_pct = max_gain_dollars / capital

            # Product guardrail: zero-cost collars can otherwise select very deep
            # OTM puts. That may be mathematically feasible at the portfolio level,
            # but it creates ugly sleeves (for example, 40% max-loss TQQQ collars).
            # Keep each sleeve's floor reasonably tied to the user's portfolio floor.
            if sleeve_max_loss_pct > max_sleeve_loss_pct:
                continue

            if sleeve_max_loss_pct <= 0 or sleeve_max_gain_pct <= 0:
                continue

            candidate = CollarCandidate(
                ticker=ticker.upper(),
                exposure=exposure_name(ticker),
                bucket=correlation_bucket(ticker),
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
                assumed_dividend_yield=round(assumed_dividend_yield, 6),
                expected_dividend_dollars=round(expected_dividend_dollars, 2),
                option_max_gain_dollars=round(option_max_gain_dollars, 2),
                option_max_loss_dollars=round(max_loss_dollars, 2),
                net_option_cost_dollars=round(net_option_cost, 2),
                net_option_cost_bps=round(net_option_cost_bps, 2),
                max_allowed_sleeve_loss_pct=round(max_sleeve_loss_pct, 4),
            )

            if execution_quality_passes(candidate):
                candidates.append(candidate)

    return candidates


def pareto_reduce(candidates: list[CollarCandidate], max_per_etf: int = 12) -> list[CollarCandidate]:
    frontier = []

    sorted_candidates = sorted(
        candidates,
        key=lambda c: (
            c.sleeve_max_loss_pct,
            -annualize_return(c.sleeve_max_gain_pct, c.dte),
        ),
    )

    best_gain_so_far = -1.0

    for c in sorted_candidates:
        annual_gain = annualize_return(c.sleeve_max_gain_pct, c.dte)
        if annual_gain > best_gain_so_far:
            frontier.append(c)
            best_gain_so_far = annual_gain

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
    include_bitcoin: bool = False,
    dividend_yields: Optional[dict[str, float]] = None,
    max_net_option_cost_bps: float = 50.0,
    growth_preference: str = "balanced",
) -> list[CollarCandidate]:

    global LAST_DEBUG
    LAST_DEBUG = []

    tier = get_account_tier(investment_amount)
    growth_preference = normalize_growth_preference(growth_preference)
    max_sleeve_loss = max_allowed_sleeve_loss_pct(max_loss_pct, growth_preference)

    if tier == "below_minimum":
        return []

    all_candidates = []

    for ticker in allowed_etfs(tier, include_bitcoin=include_bitcoin):
        debug = {
            "ticker": ticker,
            "assumed_dividend_yield": get_dividend_yield(ticker, dividend_yields),
            "max_net_option_cost_bps": max_net_option_cost_bps,
            "growth_preference": growth_preference,
            "max_allowed_sleeve_loss_pct": max_sleeve_loss,
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
                min_dte=time_horizon_days,
                max_dte=time_horizon_days + 365,
                max_expiries=4,
            )

            ticker_candidates = []

            for expiry, dte, expiry_chain in expiry_groups:
                candidates = build_collar_candidates_for_expiry(
                    expiry_chain=expiry_chain,
                    ticker=ticker,
                    expiration=expiry,
                    dte=dte,
                    dividend_yields=dividend_yields,
                    max_net_option_cost_bps=max_net_option_cost_bps,
                    max_sleeve_loss_pct=max_sleeve_loss,
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
                max_per_etf=12,
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
            "estimated_max_loss_annualized_pct": 0,
            "estimated_max_gain_dollars": round(income, 2),
            "estimated_max_gain_pct": round(total_return, 4),
            "estimated_max_gain_annualized_pct": assumed_treasury_yield,
            "time_horizon_days": time_horizon_days,
            "treasury_ticker": treasury_ticker,
            "assumed_treasury_yield": assumed_treasury_yield,
            "risk_budget_dollars": round(investment_amount * max_loss_pct, 2),
            "risk_used_dollars": 0,
            "risk_remaining_dollars": round(investment_amount * max_loss_pct, 2),
            "capital_used_dollars": 0,
            "capital_remaining_dollars": round(investment_amount, 2),
            "sleeves": [
                {
                    "type": "treasury",
                    "ticker": treasury_ticker,
                    "exposure": "Treasury Sleeve",
                    "bucket": "treasury",
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
                "expected_floor_annualized_pct": 0,
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
    estimated_max_loss_annualized_pct: float,
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
        "expected_floor_annualized_pct": round(-estimated_max_loss_annualized_pct, 4),
        "expected_cap_pct": round(estimated_max_gain_pct, 4),
        "expected_cap_annualized_pct": round(estimated_max_gain_annualized_pct, 4),
    }


def min_non_treasury_sleeves_for_tier(tier: str, max_loss_pct: float) -> int:
    if tier == "tier_1":
        return 1
    if max_loss_pct <= 0.03:
        return 2
    if tier == "tier_2":
        return 2
    if tier == "tier_3":
        return 2
    if tier == "tier_4":
        return 3
    return 1


def get_constraint_set(tier: str, max_loss_pct: float, growth_preference: str = "balanced") -> list[dict]:
    growth_preference = normalize_growth_preference(growth_preference)
    pref = growth_preference_config(growth_preference)

    min_sleeves = min_non_treasury_sleeves_for_tier(tier, max_loss_pct)
    min_sleeves = max(1, min_sleeves + pref["min_sleeve_boost"])

    if growth_preference == "conservative":
        ticker_cap = {"tier_1": 1.00, "tier_2": 0.50, "tier_3": 0.40, "tier_4": 0.30}.get(tier, 0.40)
        ticker_risk = {"tier_1": 1.00, "tier_2": 0.60, "tier_3": 0.50, "tier_4": 0.45}.get(tier, 0.50)
        bucket_risk = {"tier_1": 1.00, "tier_2": 0.70, "tier_3": 0.60, "tier_4": 0.55}.get(tier, 0.60)
    elif growth_preference == "balanced":
        ticker_cap = {"tier_1": 1.00, "tier_2": 0.55, "tier_3": 0.45, "tier_4": 0.35}.get(tier, 0.45)
        ticker_risk = {"tier_1": 1.00, "tier_2": 0.70, "tier_3": 0.60, "tier_4": 0.50}.get(tier, 0.60)
        bucket_risk = {"tier_1": 1.00, "tier_2": 0.80, "tier_3": 0.70, "tier_4": 0.60}.get(tier, 0.70)
    elif growth_preference == "growth":
        ticker_cap = {"tier_1": 1.00, "tier_2": 0.65, "tier_3": 0.55, "tier_4": 0.45}.get(tier, 0.55)
        ticker_risk = {"tier_1": 1.00, "tier_2": 0.80, "tier_3": 0.70, "tier_4": 0.60}.get(tier, 0.70)
        bucket_risk = {"tier_1": 1.00, "tier_2": 0.90, "tier_3": 0.80, "tier_4": 0.70}.get(tier, 0.80)
    else:
        ticker_cap = {"tier_1": 1.00, "tier_2": 0.80, "tier_3": 0.70, "tier_4": 0.60}.get(tier, 0.70)
        ticker_risk = 1.00
        bucket_risk = 1.00

    gain_cap = max_ticker_gain_contribution_pct(max_loss_pct, growth_preference)

    return [
        {
            "name": f"{growth_preference}_diversified",
            "growth_preference": growth_preference,
            "min_sleeves": min_sleeves,
            "max_ticker_capital_pct": ticker_cap,
            "max_ticker_risk_pct": ticker_risk,
            "max_bucket_risk_pct": bucket_risk,
            "max_ticker_gain_contribution_pct": gain_cap,
            "max_lots_per_candidate": pref["max_lots_per_candidate"],
            "collar_capital_reward": pref["collar_capital_reward"],
            "risk_usage_reward": pref["risk_usage_reward"],
            "sleeve_reward": pref["sleeve_reward"],
            # Product rule: require every allowed ticker with feasible candidates
            # to be represented with at least one actual lot.
            "require_all_tickers": True,
        },
        {
            "name": f"{growth_preference}_relaxed",
            "growth_preference": growth_preference,
            "min_sleeves": max(1, min_sleeves - 1),
            "max_ticker_capital_pct": min(1.0, ticker_cap + 0.15),
            "max_ticker_risk_pct": min(1.0, ticker_risk + 0.15),
            "max_bucket_risk_pct": min(1.0, bucket_risk + 0.15),
            "max_ticker_gain_contribution_pct": min(0.12, gain_cap * 1.25),
            "max_lots_per_candidate": pref["max_lots_per_candidate"],
            "collar_capital_reward": pref["collar_capital_reward"],
            "risk_usage_reward": pref["risk_usage_reward"],
            "sleeve_reward": pref["sleeve_reward"],
            "require_all_tickers": True,
        },
        {
            "name": "return_maximized",
            "growth_preference": growth_preference,
            "min_sleeves": 1,
            "max_ticker_capital_pct": 1.0,
            "max_ticker_risk_pct": 1.0,
            "max_bucket_risk_pct": 1.0,
            "max_ticker_gain_contribution_pct": 1.0,
            "max_lots_per_candidate": pref["max_lots_per_candidate"],
            "collar_capital_reward": pref["collar_capital_reward"],
            "risk_usage_reward": pref["risk_usage_reward"],
            "sleeve_reward": 0.0,
            "require_all_tickers": True,
        },
    ]


def solve_milp_for_constraints(
    eligible: list[CollarCandidate],
    investment_amount: float,
    max_loss_pct: float,
    time_horizon_days: int,
    assumed_treasury_yield: float,
    constraints: dict,
) -> tuple[str, list[tuple[CollarCandidate, int]]]:

    risk_budget = investment_amount * max_loss_pct
    treasury_return_for_horizon = horizon_return_from_annual(
        assumed_treasury_yield,
        time_horizon_days,
    )
    max_possible_treasury_offset = investment_amount * treasury_return_for_horizon

    model = pulp.LpProblem("Parity_Portfolio", pulp.LpMaximize)

    x = {}
    y = {}

    for i, c in enumerate(eligible):
        cap = collar_capital_required(c)
        # Do not pre-filter lots by sleeve-level risk. Portfolio-level
        # dividends and Treasury income can offset losses across sleeves, so
        # the exact net-risk test belongs in the MILP constraint below.
        max_lots_by_capital = int(investment_amount // cap)
        max_lots = max(0, min(max_lots_by_capital, constraints["max_lots_per_candidate"]))

        x[i] = pulp.LpVariable(f"x_{i}", lowBound=0, upBound=max_lots, cat="Integer")
        y[i] = pulp.LpVariable(f"y_{i}", lowBound=0, upBound=1, cat="Binary")

        model += x[i] <= max_lots * y[i]
        # If y[i] marks a candidate as selected, require at least one lot.
        # Without this, the model can set y=1 and x=0 to satisfy
        # "require all tickers" without actually buying the collar.
        model += x[i] >= y[i]

    collar_capital_expr = pulp.lpSum(
        x[i] * collar_capital_required(c) for i, c in enumerate(eligible)
    )

    gross_collar_loss_expr = pulp.lpSum(
        x[i] * collar_loss_dollars(c)
        for i, c in enumerate(eligible)
    )

    dividend_income_expr_for_loss = pulp.lpSum(
        x[i] * c.expected_dividend_dollars
        for i, c in enumerate(eligible)
    )

    treasury_income_expr_for_loss = (
        investment_amount - collar_capital_expr
    ) * treasury_return_for_horizon

    net_loss_expr = (
        gross_collar_loss_expr
        - dividend_income_expr_for_loss
        - treasury_income_expr_for_loss
    )

    model += collar_capital_expr <= investment_amount
    # Economic max-loss constraint:
    # All expected dividends and Treasury income are treated as one portfolio
    # cash pool. That income offsets gross collar floor losses across all
    # sleeves, including non-dividend payers like TQQQ.
    model += net_loss_expr <= risk_budget

    for ticker in set(c.ticker for c in eligible):
        idxs = [i for i, c in enumerate(eligible) if c.ticker == ticker]

        # At most one strike/expiration structure per ticker.
        # Lots can still be greater than one on the selected structure.
        model += pulp.lpSum(y[i] for i in idxs) <= 1

        # Optional first-pass product constraint:
        # In diversified mode, require each allowed ticker that has feasible
        # candidates to appear at least once. If this makes the solve
        # infeasible, optimize_parity_portfolio falls through to relaxed mode,
        # where this requirement is disabled.
        if constraints.get("require_all_tickers", False):
            model += pulp.lpSum(y[i] for i in idxs) >= 1

        model += (
            pulp.lpSum(x[i] * collar_capital_required(eligible[i]) for i in idxs)
            <= investment_amount * constraints["max_ticker_capital_pct"]
        )

        # Do not apply a hard per-ticker risk constraint here. Downside risk is
        # controlled at the portfolio level after pooling all dividends and
        # Treasury income. A ticker-level risk cap would prevent income from
        # one sleeve from offsetting another sleeve's loss.

        # Do not apply a hard per-ticker upside cap here.
        # For small accounts, requiring every ETF plus a hard upside-contribution
        # cap can make the MILP infeasible because one minimum executable lot can
        # exceed the cap. Upside concentration should be handled in the objective
        # or UI warnings, not as a hard feasibility constraint.

    # Do not apply hard bucket-level risk constraints for the same reason:
    # max loss is now measured at the total portfolio level after the shared
    # income pool offsets gross collar floor losses.

    if constraints["min_sleeves"] > 1:
        model += pulp.lpSum(y[i] for i in range(len(eligible))) >= constraints["min_sleeves"]

    collar_annualized_gain_expr = pulp.lpSum(
        x[i] * collar_annualized_gain_dollars(c) for i, c in enumerate(eligible)
    )

    sgov_income_expr = (
        investment_amount - collar_capital_expr
    ) * treasury_return_for_horizon

    model += (
        collar_annualized_gain_expr
        + sgov_income_expr
        + constraints["collar_capital_reward"] * collar_capital_expr
        + constraints["risk_usage_reward"] * net_loss_expr
        + constraints["sleeve_reward"] * pulp.lpSum(y[i] for i in range(len(eligible)))
    )

    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=5)
    result_status = model.solve(solver)
    status = pulp.LpStatus[result_status]

    selected = []

    if status not in ["Optimal", "Feasible"]:
        return status, selected

    for i, c in enumerate(eligible):
        lots = int(round(x[i].value() or 0))
        if lots > 0:
            selected.append((c, lots))

    if constraints.get("require_all_tickers", False):
        required_tickers = {c.ticker for c in eligible}
        selected_tickers = {c.ticker for c, _ in selected}
        if not required_tickers.issubset(selected_tickers):
            return "MissingRequiredTicker", []

    return status, selected


def optimize_parity_portfolio(
    investment_amount: float,
    max_loss_pct: float,
    time_horizon_days: int,
    collar_candidates: list[CollarCandidate],
    treasury_ticker: str = "SGOV",
    assumed_treasury_yield: float = 0.045,
    growth_preference: str = "balanced",
    include_bitcoin: bool = False,
) -> dict:

    growth_preference = normalize_growth_preference(growth_preference)
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
        if c.ticker in allowed_etfs(tier, include_bitcoin=include_bitcoin)
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

    selected = []
    optimizer_status = "Not Solved"
    constraint_mode_used = None

    for constraint_set in get_constraint_set(tier, max_loss_pct, growth_preference):
        optimizer_status, selected = solve_milp_for_constraints(
            eligible=eligible,
            investment_amount=investment_amount,
            max_loss_pct=max_loss_pct,
            time_horizon_days=time_horizon_days,
            assumed_treasury_yield=assumed_treasury_yield,
            constraints=constraint_set,
        )

        if selected:
            constraint_mode_used = constraint_set["name"]
            break

    if not selected:
        return make_treasury_only_portfolio(
            investment_amount,
            max_loss_pct,
            time_horizon_days,
            treasury_ticker,
            assumed_treasury_yield,
            tier,
            "The selected max loss target is tighter than the minimum executable collar size allows, so the portfolio is allocated to the Treasury sleeve.",
        )

    risk_budget = investment_amount * max_loss_pct

    collar_capital = sum(collar_capital_required(c) * lots for c, lots in selected)
    treasury_amount = investment_amount - collar_capital

    gross_collar_loss_dollars = sum(
        collar_loss_dollars(c) * lots
        for c, lots in selected
    )

    # Current contractual cycle gain = what the selected collars can make
    # through their actual expirations, plus treasury income over the user's horizon.
    current_cycle_collar_gain_dollars = sum(collar_gain_dollars(c) * lots for c, lots in selected)

    # Horizon-normalized gain = converts each selected collar's cap to the
    # requested horizon using annualized compounding. This creates an apples-to-
    # apples risk/return comparison across different option expirations.
    horizon_normalized_collar_gain_dollars = sum(
        collar_horizon_normalized_gain_dollars(c, time_horizon_days) * lots
        for c, lots in selected
    )

    dividend_income_dollars = sum(c.expected_dividend_dollars * lots for c, lots in selected)
    option_gain_dollars = sum(c.option_max_gain_dollars * lots for c, lots in selected)

    treasury_return_for_horizon = horizon_return_from_annual(
        assumed_treasury_yield,
        time_horizon_days,
    )
    treasury_gain_dollars = treasury_amount * treasury_return_for_horizon

    # Portfolio max loss is economically income-adjusted:
    # gross collar floor loss minus the shared portfolio income pool
    # from dividends and Treasury income. This lets EEM/EFA dividends
    # offset losses from TQQQ or any other sleeve.
    loss_dollars = max(
        0.0,
        gross_collar_loss_dollars
        - dividend_income_dollars
        - treasury_gain_dollars,
    )

    current_cycle_gain_dollars = current_cycle_collar_gain_dollars + treasury_gain_dollars
    gain_dollars = horizon_normalized_collar_gain_dollars + treasury_gain_dollars

    actual_loss_pct = loss_dollars / investment_amount
    actual_gain_pct = gain_dollars / investment_amount
    current_cycle_gain_pct = current_cycle_gain_dollars / investment_amount

    weighted_dte = sum(
        collar_capital_required(c) * lots * c.dte
        for c, lots in selected
    ) / max(collar_capital, 1)

    estimated_max_loss_annualized_pct = annualize_loss(actual_loss_pct, time_horizon_days)

    # IMPORTANT:
    # actual_gain_pct is built from horizon_normalized_collar_gain_dollars,
    # which already converts each collar's actual expiration return into the
    # requested user horizon. Annualizing actual_gain_pct again would annualize
    # a horizon-normalized number a second time and can create inflated /
    # nonsensical API values for short horizons.
    #
    # For display/API purposes, use the current contractual cycle annualized
    # return below, which is based on the actual collar cycle economics rather
    # than the already-normalized horizon return.
    current_cycle_max_loss_annualized_pct = annualize_loss(actual_loss_pct, weighted_dte)
    current_cycle_max_gain_annualized_pct = annualize_return(current_cycle_gain_pct, weighted_dte)
    estimated_max_gain_annualized_pct = current_cycle_max_gain_annualized_pct

    sleeves = []

    for c, lots in selected:
        capital = collar_capital_required(c) * lots

        sleeves.append({
            "type": "collar",
            "ticker": c.ticker,
            "exposure": c.exposure,
            "bucket": c.bucket,
            "allocation_dollars": round(capital, 2),
            "allocation_pct": round(capital / investment_amount, 4),
            "minimum_executable_collar_cost": round(collar_capital_required(c), 2),

            "stock_price": c.stock_price,
            "shares": c.shares * lots,
            "contracts": c.contracts * lots,
            "lots": lots,
            "stock_value": round(c.stock_value * lots, 2),
            "assumed_dividend_yield": round(c.assumed_dividend_yield, 6),
            "expected_dividend_dollars": round(c.expected_dividend_dollars * lots, 2),
            "net_option_cost_dollars": round(c.net_option_cost_dollars * lots, 2),
            "net_option_cost_bps": round(c.net_option_cost_bps, 2),
            "option_max_gain_dollars": round(c.option_max_gain_dollars * lots, 2),
            "total_max_gain_dollars": round(collar_gain_dollars(c) * lots, 2),

            "expiration": c.expiration,
            "dte": c.dte,

            "sleeve_max_loss_pct": round(c.sleeve_max_loss_pct, 4),
            "sleeve_max_loss_annualized_pct": round(
                annualize_loss(c.sleeve_max_loss_pct, c.dte),
                4,
            ),
            "sleeve_max_gain_pct": round(c.sleeve_max_gain_pct, 4),
            "sleeve_max_gain_annualized_pct": round(
                annualize_return(c.sleeve_max_gain_pct, c.dte),
                4,
            ),
            "sleeve_max_gain_horizon_normalized_pct": round(
                normalize_return_to_horizon(c.sleeve_max_gain_pct, c.dte, time_horizon_days),
                4,
            ),

            "portfolio_max_loss_contribution_dollars": round(collar_loss_dollars(c) * lots, 2),
            "portfolio_max_loss_contribution_pct": round((collar_loss_dollars(c) * lots) / investment_amount, 4),
            "portfolio_max_gain_contribution_dollars": round(collar_gain_dollars(c) * lots, 2),
            "portfolio_max_gain_contribution_pct": round((collar_gain_dollars(c) * lots) / investment_amount, 4),
            "portfolio_max_gain_contribution_horizon_normalized_dollars": round(
                collar_horizon_normalized_gain_dollars(c, time_horizon_days) * lots,
                2,
            ),
            "portfolio_max_gain_contribution_horizon_normalized_pct": round(
                (collar_horizon_normalized_gain_dollars(c, time_horizon_days) * lots) / investment_amount,
                4,
            ),

            "option_legs": {
                "long_put": asdict(c.long_put),
                "short_call": asdict(c.short_call),
            },

            "option_execution": collar_spread_cost(c),
            "execution_quality_passed": execution_quality_passes(c),
            "liquidity_score": c.liquidity_score,
            "efficiency": round(collar_efficiency(c), 4),
            "quote_timestamp": c.quote_timestamp,
        })

    sleeves.append({
        "type": "treasury",
        "ticker": treasury_ticker,
        "exposure": "Treasury Sleeve",
        "bucket": "treasury",
        "allocation_dollars": round(treasury_amount, 2),
        "allocation_pct": round(treasury_amount / investment_amount, 4),
        "assumed_yield": assumed_treasury_yield,
        "estimated_income_dollars": round(
            treasury_gain_dollars,
            2,
        ),
        "sleeve_max_loss_pct": 0,
        "sleeve_max_loss_annualized_pct": 0,
        "sleeve_max_gain_pct": round(treasury_return_for_horizon, 4),
        "sleeve_max_gain_annualized_pct": assumed_treasury_yield,
    })

    warnings = []

    if any(c.ticker in ["TQQQ", "UPRO"] for c, _ in selected):
        warnings.append(
            "Portfolio uses leveraged ETFs, which reset daily and may behave differently over longer periods."
        )

    for c, _ in selected:
        spread = collar_spread_cost(c)

        if spread["total_option_spread_dollars"] > 400:
            warnings.append(f"{c.ticker} collar has a wide combined option spread.")

        if abs(c.dte - time_horizon_days) > 120:
            warnings.append(
                f"{c.ticker} uses an expiration {int(c.dte)} days out, which differs from the requested {time_horizon_days}-day horizon."
            )

    bucket_risk = {}
    for c, lots in selected:
        bucket_risk[c.bucket] = bucket_risk.get(c.bucket, 0) + collar_loss_dollars(c) * lots

    portfolio = {
        "portfolio_type": "milp_diversified_risk_budget_optimizer",
        "optimizer_status": optimizer_status,
        "constraint_mode_used": constraint_mode_used,
        "growth_preference": growth_preference,
        "account_tier": tier,
        "investment_amount": investment_amount,
        "input_max_loss_pct": max_loss_pct,
        "max_allowed_sleeve_loss_pct": round(max_allowed_sleeve_loss_pct(max_loss_pct, growth_preference), 4),
        "actual_max_loss_dollars": round(loss_dollars, 2),
        "actual_max_loss_pct": round(actual_loss_pct, 4),
        "gross_collar_max_loss_dollars": round(gross_collar_loss_dollars, 2),
        "dividend_loss_offset_dollars": round(dividend_income_dollars, 2),
        "treasury_loss_offset_dollars": round(treasury_gain_dollars, 2),
        "estimated_max_loss_annualized_pct": round(estimated_max_loss_annualized_pct, 4),
        "current_cycle_max_loss_annualized_pct": round(current_cycle_max_loss_annualized_pct, 4),
        "estimated_max_gain_dollars": round(gain_dollars, 2),
        "estimated_option_max_gain_dollars": round(option_gain_dollars, 2),
        "estimated_dividend_income_dollars": round(dividend_income_dollars, 2),
        "estimated_max_gain_pct": round(actual_gain_pct, 4),
        "estimated_max_gain_annualized_pct": round(estimated_max_gain_annualized_pct, 4),
        "current_cycle_max_gain_dollars": round(current_cycle_gain_dollars, 2),
        "current_cycle_max_gain_pct": round(current_cycle_gain_pct, 4),
        "current_cycle_max_gain_annualized_pct": round(current_cycle_max_gain_annualized_pct, 4),
        "treasury_return_for_horizon_pct": round(treasury_return_for_horizon, 4),
        "excess_max_gain_over_treasury_pct": round(actual_gain_pct - treasury_return_for_horizon, 4),
        "risk_reward_ratio": round(actual_gain_pct / actual_loss_pct, 4) if actual_loss_pct > 0 else None,
        "weighted_option_dte": round(weighted_dte, 1),
        "time_horizon_days": time_horizon_days,
        "treasury_ticker": treasury_ticker,
        "assumed_treasury_yield": assumed_treasury_yield,
        "risk_budget_dollars": round(risk_budget, 2),
        "risk_used_dollars": round(loss_dollars, 2),
        "risk_remaining_dollars": round(max(risk_budget - loss_dollars, 0), 2),
        "capital_used_dollars": round(collar_capital, 2),
        "capital_remaining_dollars": round(treasury_amount, 2),
        "non_treasury_sleeve_count": len(selected),
        "bucket_risk_dollars": {k: round(v, 2) for k, v in bucket_risk.items()},
        "sleeves": sleeves,
        "warnings": warnings,
    }

    portfolio["portfolio_summary"] = build_portfolio_summary(
        sleeves=sleeves,
        investment_amount=investment_amount,
        actual_max_loss_pct=actual_loss_pct,
        estimated_max_loss_annualized_pct=estimated_max_loss_annualized_pct,
        estimated_max_gain_pct=actual_gain_pct,
        estimated_max_gain_annualized_pct=estimated_max_gain_annualized_pct,
    )

    return {
        "status": "success",
        "portfolio": portfolio,
        "debug": LAST_DEBUG,
    }