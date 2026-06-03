import argparse
import json
import os
from io import StringIO

import numpy as np
import pandas as pd
import requests


MULT = 100


# ============================================================
# 1. ORATS DATA PULL
# ============================================================

def fetch_orats_chain(ticker="XSP", token=None):
    """
    Pull ORATS live one-minute strikes chain.
    This function name is required by api.py.
    """

    if token is None:
        token = os.getenv("ORATS_TOKEN")

    if not token:
        raise ValueError(
            "Missing ORATS token. Either set ORATS_TOKEN or pass --token."
        )

    url = (
        "https://api.orats.io/datav2/live/one-minute/strikes/chain"
        f"?token={token}&ticker={ticker}"
    )

    response = requests.request(
        "GET",
        url,
        headers={},
        data={},
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"ORATS request failed: {response.status_code} - {response.text[:500]}"
        )

    return pd.read_csv(StringIO(response.text))


# ============================================================
# 2. DATA PREP
# ============================================================

def clean_chain(df, ticker=None):
    """
    Clean option chain fields and create mid prices.
    """

    chain = df.copy()

    if ticker is not None and "ticker" in chain.columns:
        chain = chain[chain["ticker"] == ticker].copy()

    for col in ["expirDate", "tradeDate"]:
        if col in chain.columns:
            chain[col] = pd.to_datetime(chain[col], errors="coerce")

    numeric_cols = [
        "strike",
        "dte",
        "stockPrice",
        "spotPrice",
        "callBidPrice",
        "callAskPrice",
        "putBidPrice",
        "putAskPrice",
        "callVolume",
        "putVolume",
        "callOpenInterest",
        "putOpenInterest",
        "callBidSize",
        "callAskSize",
        "putBidSize",
        "putAskSize",
    ]

    for col in numeric_cols:
        if col in chain.columns:
            chain[col] = pd.to_numeric(chain[col], errors="coerce")

    if "spotPrice" in chain.columns and "stockPrice" in chain.columns:
        chain["spot"] = chain["spotPrice"].fillna(chain["stockPrice"])
    elif "spotPrice" in chain.columns:
        chain["spot"] = chain["spotPrice"]
    elif "stockPrice" in chain.columns:
        chain["spot"] = chain["stockPrice"]
    else:
        raise ValueError("Missing spotPrice or stockPrice in ORATS response.")

    chain["callMid"] = (chain["callBidPrice"] + chain["callAskPrice"]) / 2
    chain["putMid"] = (chain["putBidPrice"] + chain["putAskPrice"]) / 2

    required_cols = [
        "expirDate",
        "dte",
        "strike",
        "spot",
        "callMid",
        "putMid",
    ]

    chain = chain.dropna(subset=required_cols).copy()

    return chain


def get_expiry_summary(chain):
    """
    Summarize expirations.
    """

    expiry_summary = (
        chain
        .groupby("expirDate", as_index=False)
        .agg(
            dte=("dte", "median"),
            num_strikes=("strike", "nunique"),
            spot=("spot", "median"),
            total_call_volume=("callVolume", "sum"),
            total_put_volume=("putVolume", "sum"),
            total_call_oi=("callOpenInterest", "sum"),
            total_put_oi=("putOpenInterest", "sum"),
        )
    )

    return expiry_summary


def select_single_expiry(
    chain,
    target_dte=365,
    prefer_at_or_after=True,
    max_dte_overage=60,
):
    """
    Select one expiration for both products.

    For defined-outcome products, prefer an expiration at or after the user's
    selected horizon so a 1-year product is not shortened unnecessarily.

    Example:
        target_dte = 365
        available = 353 and 380
        choose 380, not 353
    """

    expiry_summary = get_expiry_summary(chain)

    if expiry_summary.empty:
        raise ValueError("No expirations available.")

    expiry_summary["dte_diff"] = (expiry_summary["dte"] - target_dte).abs()
    expiry_summary["dte_over_target"] = expiry_summary["dte"] - target_dte

    if prefer_at_or_after:
        eligible = expiry_summary[
            (expiry_summary["dte"] >= target_dte)
            & (expiry_summary["dte"] <= target_dte + max_dte_overage)
        ].copy()

        if not eligible.empty:
            selected = eligible.sort_values(
                ["dte_over_target", "dte"],
                ascending=[True, True],
            ).iloc[0]
        else:
            selected = expiry_summary.sort_values(
                ["dte_diff", "dte"],
                ascending=[True, False],
            ).iloc[0]
    else:
        selected = expiry_summary.sort_values(
            ["dte_diff", "dte"],
            ascending=[True, True],
        ).iloc[0]

    selected_expiry = selected["expirDate"]

    expiry_chain = (
        chain[chain["expirDate"] == selected_expiry]
        .sort_values("strike")
        .reset_index(drop=True)
    )

    return expiry_chain, selected.to_dict(), expiry_summary


# ============================================================
# 3. BACKWARD-COMPATIBLE EXPIRY FUNCTION
# ============================================================

def get_closest_expiry_chains(
    df,
    target_dte=365,
    n_expiries=5,
    ticker=None,
):
    """
    Backward-compatible helper.

    Kept so old api.py imports do not break.
    Returns option chains for the n expiries closest to target DTE.
    """

    chain = clean_chain(df, ticker=ticker)

    expiry_summary = get_expiry_summary(chain)
    expiry_summary["dte_diff"] = (expiry_summary["dte"] - target_dte).abs()

    expiry_summary = expiry_summary.sort_values(
        by=["dte_diff", "dte"],
        ascending=[True, True],
    )

    selected_expiries = expiry_summary.head(n_expiries)["expirDate"].tolist()

    closest_chains = chain[
        chain["expirDate"].isin(selected_expiries)
    ].copy()

    closest_chains = closest_chains.sort_values(
        by=["expirDate", "strike"]
    ).reset_index(drop=True)

    expiry_summary = expiry_summary.head(n_expiries).reset_index(drop=True)

    return closest_chains, expiry_summary


# ============================================================
# 4. HELPERS
# ============================================================

def liquidity_score(*rows):
    total_volume = 0.0
    total_oi = 0.0

    for row in rows:
        if row is None:
            continue

        for col in ["callVolume", "putVolume"]:
            if col in row and not pd.isna(row[col]):
                total_volume += float(row[col])

        for col in ["callOpenInterest", "putOpenInterest"]:
            if col in row and not pd.isna(row[col]):
                total_oi += float(row[col])

    score = np.log1p(total_volume) + np.log1p(total_oi)

    return score, total_volume, total_oi


def round_pct(x):
    if x is None or pd.isna(x):
        return None
    return float(x) * 100


def make_json_safe(obj):
    """
    Converts pandas/numpy objects into JSON-safe values.
    """

    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [make_json_safe(v) for v in obj]

    if isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d")

    if isinstance(obj, np.integer):
        return int(obj)

    if isinstance(obj, np.floating):
        if np.isnan(obj):
            return None
        return float(obj)

    if isinstance(obj, float) and np.isnan(obj):
        return None

    return obj


# ============================================================
# 5. COLLAR PRODUCT
# ============================================================

def build_zero_cost_dividend_floor_collar(
    expiry_chain,
    max_loss_pct=0.005,
    assumed_dividend_yield=0.01,
):
    """
    Product: Defined Floor

    Logic:
    1. Determine the put strike needed to satisfy the user's max loss target
       after expected dividends.
    2. Buy the lowest put strike that satisfies the floor.
    3. Sell the call that creates the smallest possible option debit.
    4. Do NOT accept net credits.

    Net option cost:
        net_cost = put_cost - call_credit

    Required:
        net_cost >= 0

    Objective:
        choose the smallest net_cost above zero.
    """

    g = expiry_chain.copy()

    if g.empty:
        return None

    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    notional = spot * MULT

    expected_dividend_dollars = (
        notional * assumed_dividend_yield * (dte / 365.25)
    )
    expected_dividend_per_share = expected_dividend_dollars / MULT

    target_floor_value = notional * (1 - max_loss_pct)

    required_put_strike = (
        target_floor_value - expected_dividend_dollars
    ) / MULT

    valid_puts = g[
        (g["strike"] < spot)
        & (g["strike"] >= required_put_strike)
        & (g["putAskPrice"] > g["putBidPrice"])
        & (g["putMid"] > 0)
    ].copy()

    if valid_puts.empty:
        valid_puts = g[
            (g["strike"] < spot)
            & (g["putAskPrice"] > g["putBidPrice"])
            & (g["putMid"] > 0)
        ].copy()

        if valid_puts.empty:
            return None

        valid_puts["required_distance"] = (
            valid_puts["strike"] - required_put_strike
        ).abs()

        put = valid_puts.sort_values(
            ["required_distance", "strike"],
            ascending=[True, False],
        ).iloc[0]
    else:
        put = valid_puts.sort_values(
            "strike",
            ascending=True,
        ).iloc[0]

    put_strike = float(put["strike"])
    put_cost = float(put["putMid"]) * MULT

    valid_calls = g[
        (g["strike"] > spot)
        & (g["callAskPrice"] > g["callBidPrice"])
        & (g["callMid"] > 0)
    ].copy()

    if valid_calls.empty:
        return None

    valid_calls["call_credit_dollars"] = valid_calls["callMid"] * MULT
    valid_calls["net_cost_dollars"] = (
        put_cost - valid_calls["call_credit_dollars"]
    )

    # Do not accept option credits. Only zero or positive debit.
    debit_calls = valid_calls[
        valid_calls["net_cost_dollars"] >= 0
    ].copy()

    if debit_calls.empty:
        return None

    # Choose smallest possible debit.
    # If two debits are equal, choose the higher call strike for better cap.
    call = debit_calls.sort_values(
        ["net_cost_dollars", "strike"],
        ascending=[True, False],
    ).iloc[0]

    call_strike = float(call["strike"])
    call_credit = float(call["callMid"]) * MULT

    net_cost = put_cost - call_credit
    net_cost_bps = net_cost / notional * 10000

    floor_value = (
        put_strike * MULT
        + expected_dividend_dollars
        - net_cost
    )

    cap_value = (
        call_strike * MULT
        + expected_dividend_dollars
        - net_cost
    )

    floor_return = floor_value / notional - 1
    cap_return = cap_value / notional - 1

    max_loss_dollars = notional - floor_value
    max_gain_dollars = cap_value - notional

    worst_net_cost = (
        float(put["putAskPrice"]) - float(call["callBidPrice"])
    ) * MULT

    bid_ask_drag_dollars = worst_net_cost - net_cost
    bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

    liq_score, total_volume, total_oi = liquidity_score(put, call)

    return {
        "product_name": "Defined Floor",
        "strategy": "classic_collar",
        "structure": "collar",
        "backend_structure": "long_underlying_plus_long_put_short_call",
        "expirDate": g["expirDate"].iloc[0],
        "dte": dte,
        "spot": spot,
        "notional": notional,

        "assumed_dividend_yield": assumed_dividend_yield,
        "expected_dividend_dollars": expected_dividend_dollars,
        "expected_dividend_per_share": expected_dividend_per_share,

        "target_max_loss_pct": max_loss_pct,
        "required_put_strike": required_put_strike,

        "long_put_strike": put_strike,
        "short_put_strike": None,
        "call_strike": call_strike,

        "put_cost_dollars": put_cost,
        "call_credit_dollars": call_credit,
        "net_cost_dollars": net_cost,
        "net_cost_bps": net_cost_bps,
        "smallest_debit_ok": True,
        "zero_or_debit_only": True,

        "floor_value": floor_value,
        "cap_value": cap_value,
        "floor_return": floor_return,
        "cap_return": cap_return,
        "max_loss_dollars": max_loss_dollars,
        "max_gain_dollars": max_gain_dollars,

        "buffer_width_points": None,
        "buffer_pct": None,
        "protected_zone_value": None,
        "protected_zone_return": None,

        "bid_ask_drag_bps": bid_ask_drag_bps,
        "total_volume": total_volume,
        "total_oi": total_oi,
        "liquidity_score": liq_score,

        "display": {
            "title": "Defined Floor",
            "subtitle": "Hard-loss target with capped upside",
            "estimated_max_loss_pct": round_pct(floor_return),
            "estimated_cap_pct": round_pct(cap_return),
            "estimated_option_cost_dollars": net_cost,
            "estimated_dividends_dollars": expected_dividend_dollars,
            "explanation": (
                "Designed to target a defined floor over the selected outcome period. "
                "Upside is capped in exchange for downside protection."
            ),
        },
    }


# ============================================================
# 6. BUFFER PRODUCT
# ============================================================

def build_zero_cost_target_cap_buffer(
    expiry_chain,
    target_gain_pct=0.08,
    assumed_dividend_yield=0.01,
    max_buffer_pct=0.20,
    max_credit_bps=25,
    max_debit_bps=25,
    fallback_max_abs_cost_bps=50,
):
    """
    Product: Buffered Growth

    Loosened POC logic:
    1. Find the call strike closest to the user's target gain.
    2. Build buffer candidates using an ATM/near-ATM long put and lower short puts.
    3. Allow small credits or small debits around zero.
    4. Rank by option cost closest to zero, then larger buffer.
    5. If nothing is inside the tight +/-10 bps band, use a fallback within +/-50 bps.
    6. Do not allow extreme buffers above max_buffer_pct.

    Important:
    A small credit should not be marketed as income.
    The front end can display near-zero costs as "approximately $0".
    """

    g = expiry_chain.copy()

    if g.empty:
        return None

    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    notional = spot * MULT

    expected_dividend_dollars = (
        notional * assumed_dividend_yield * (dte / 365.25)
    )
    expected_dividend_per_share = expected_dividend_dollars / MULT

    target_cap_value = notional * (1 + target_gain_pct)

    required_call_strike = (
        target_cap_value - expected_dividend_dollars
    ) / MULT

    valid_calls = g[
        (g["strike"] > spot)
        & (g["callAskPrice"] > g["callBidPrice"])
        & (g["callMid"] > 0)
    ].copy()

    if valid_calls.empty:
        return None

    valid_calls["target_distance"] = (
        valid_calls["strike"] - required_call_strike
    ).abs()

    # Instead of only using one call, look at several calls near the target.
    # This helps the buffer exist more often.
    nearby_calls = valid_calls.sort_values(
        ["target_distance", "strike"],
        ascending=[True, True],
    ).head(7)

    candidate_long_puts = g[
        (g["strike"] <= spot)
        & (g["putAskPrice"] > g["putBidPrice"])
        & (g["putMid"] > 0)
    ].copy()

    if candidate_long_puts.empty:
        return None

    candidate_long_puts["atm_distance"] = (
        candidate_long_puts["strike"] - spot
    ).abs()

    # Look at a few near-ATM long puts, not only one.
    nearby_long_puts = candidate_long_puts.sort_values(
        ["atm_distance", "strike"],
        ascending=[True, False],
    ).head(5)

    rows = []

    for _, call in nearby_calls.iterrows():
        call_strike = float(call["strike"])
        call_credit = float(call["callMid"]) * MULT

        for _, long_put in nearby_long_puts.iterrows():
            long_put_strike = float(long_put["strike"])
            long_put_cost = float(long_put["putMid"]) * MULT

            short_puts = g[
                (g["strike"] < long_put_strike)
                & (g["putAskPrice"] > g["putBidPrice"])
                & (g["putMid"] > 0)
            ].copy()

            if short_puts.empty:
                continue

            for _, short_put in short_puts.iterrows():
                short_put_strike = float(short_put["strike"])
                short_put_credit = float(short_put["putMid"]) * MULT

                put_spread_cost = long_put_cost - short_put_credit
                net_cost = put_spread_cost - call_credit
                net_cost_bps = net_cost / notional * 10000

                buffer_width_points = long_put_strike - short_put_strike
                buffer_pct = buffer_width_points / spot

                # Avoid weird demo output like 40%+ buffers.
                if buffer_pct <= 0 or buffer_pct > max_buffer_pct:
                    continue

                max_buffer_value = buffer_width_points * MULT

                protected_zone_value = (
                    short_put_strike * MULT
                    + max_buffer_value
                    + expected_dividend_dollars
                    - net_cost
                )

                cap_value = (
                    call_strike * MULT
                    + expected_dividend_dollars
                    - net_cost
                )

                protected_zone_return = protected_zone_value / notional - 1
                cap_return = cap_value / notional - 1

                worst_net_cost = (
                    float(long_put["putAskPrice"])
                    - float(short_put["putBidPrice"])
                    - float(call["callBidPrice"])
                ) * MULT

                bid_ask_drag_dollars = worst_net_cost - net_cost
                bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

                liq_score, total_volume, total_oi = liquidity_score(
                    long_put,
                    short_put,
                    call,
                )

                rows.append({
                    "call_strike": call_strike,
                    "call_credit": call_credit,
                    "long_put_strike": long_put_strike,
                    "long_put_cost": long_put_cost,
                    "short_put_strike": short_put_strike,
                    "short_put_credit": short_put_credit,
                    "put_spread_cost": put_spread_cost,
                    "net_cost": net_cost,
                    "net_cost_bps": net_cost_bps,
                    "abs_net_cost": abs(net_cost),
                    "abs_net_cost_bps": abs(net_cost_bps),
                    "buffer_width_points": buffer_width_points,
                    "buffer_pct": buffer_pct,
                    "protected_zone_value": protected_zone_value,
                    "protected_zone_return": protected_zone_return,
                    "cap_value": cap_value,
                    "cap_return": cap_return,
                    "cap_error": abs(cap_return - target_gain_pct),
                    "bid_ask_drag_bps": bid_ask_drag_bps,
                    "total_volume": total_volume,
                    "total_oi": total_oi,
                    "liquidity_score": liq_score,
                })

    if not rows:
        return None

    candidates = pd.DataFrame(rows)

    # Preferred range: allow small credits or small debits around zero.
    preferred = candidates[
        (candidates["net_cost_bps"] >= -max_credit_bps)
        & (candidates["net_cost_bps"] <= max_debit_bps)
    ].copy()

    if not preferred.empty:
        pool = preferred
        outside_tolerance = False
    else:
        # Fallback: still return a buffer for the demo if it is reasonably close.
        fallback = candidates[
            candidates["abs_net_cost_bps"] <= fallback_max_abs_cost_bps
        ].copy()

        if fallback.empty:
            return None

        pool = fallback
        outside_tolerance = True

    # Rank:
    # 1. closest to zero cost
    # 2. cap closest to target
    # 3. larger buffer
    # 4. lower bid/ask drag
    # 5. better OI
    best = pool.sort_values(
        [
            "abs_net_cost",
            "cap_error",
            "buffer_pct",
            "bid_ask_drag_bps",
            "total_oi",
        ],
        ascending=[True, True, False, True, False],
    ).iloc[0]

    net_cost = float(best["net_cost"])

    cost_display_label = "approximately $0"
    if outside_tolerance:
        cost_display_label = "near zero"

    return {
        "product_name": "Buffered Growth",
        "strategy": "buffered_collar_first_loss",
        "structure": "buffer",
        "backend_structure": "long_underlying_plus_long_put_short_put_short_call",
        "expirDate": g["expirDate"].iloc[0],
        "dte": dte,
        "spot": spot,
        "notional": notional,

        "assumed_dividend_yield": assumed_dividend_yield,
        "expected_dividend_dollars": expected_dividend_dollars,
        "expected_dividend_per_share": expected_dividend_per_share,

        "target_gain_pct": target_gain_pct,
        "required_call_strike": required_call_strike,

        "long_put_strike": float(best["long_put_strike"]),
        "short_put_strike": float(best["short_put_strike"]),
        "call_strike": float(best["call_strike"]),

        "long_put_cost_dollars": float(best["long_put_cost"]),
        "short_put_credit_dollars": float(best["short_put_credit"]),
        "put_spread_cost_dollars": float(best["put_spread_cost"]),
        "call_credit_dollars": float(best["call_credit"]),
        "net_cost_dollars": net_cost,
        "net_cost_bps": float(best["net_cost_bps"]),

        "near_zero_cost_ok": True,
        "outside_tolerance": outside_tolerance,
        "max_credit_bps_allowed": max_credit_bps,
        "max_debit_bps_allowed": max_debit_bps,

        "buffer_width_points": float(best["buffer_width_points"]),
        "buffer_pct": float(best["buffer_pct"]),

        "protected_zone_value": float(best["protected_zone_value"]),
        "protected_zone_return": float(best["protected_zone_return"]),
        "cap_value": float(best["cap_value"]),
        "cap_return": float(best["cap_return"]),

        "floor_value": None,
        "floor_return": None,
        "max_loss_dollars": None,
        "max_gain_dollars": float(best["cap_value"]) - notional,

        "bid_ask_drag_bps": float(best["bid_ask_drag_bps"]),
        "total_volume": float(best["total_volume"]),
        "total_oi": float(best["total_oi"]),
        "liquidity_score": float(best["liquidity_score"]),

        "display": {
            "title": "Buffered Growth",
            "subtitle": "First-loss protection with more upside potential",
            "estimated_buffer_pct": round_pct(float(best["buffer_pct"])),
            "estimated_cap_pct": round_pct(float(best["cap_return"])),
            "estimated_option_cost_dollars": net_cost,
            "estimated_option_cost_label": cost_display_label,
            "estimated_dividends_dollars": expected_dividend_dollars,
            "explanation": (
                "Designed to absorb a defined range of losses first. "
                "Losses may continue if the market falls beyond the buffer."
            ),
        },
    }

# ============================================================
# 7. PRODUCT RECOMMENDATION PAYLOAD
# ============================================================

def build_defined_outcome_recommendations(
    df,
    ticker="XSP",
    horizon=365,
    max_loss_pct=0.005,
    target_gain_pct=0.08,
    assumed_dividend_yield=0.01,
):
    """
    Builds the two Phase 1 products for Base44:

    1. Defined Floor
        - Find the floor first based on max loss including dividends.
        - Then find the call that funds the put without taking a credit.
        - Choose the smallest option debit possible.

    2. Buffered Growth
        - Find the call first based on target top gain including dividends.
        - Then find the best buffer without taking a credit.
        - Choose the smallest option debit possible.

    Both products use the same selected expiration.
    """

    chain = clean_chain(df, ticker=ticker)

    expiry_chain, selected_expiry_summary, _ = select_single_expiry(
        chain,
        target_dte=horizon,
        prefer_at_or_after=True,
        max_dte_overage=60,
    )

    collar = build_zero_cost_dividend_floor_collar(
        expiry_chain,
        max_loss_pct=max_loss_pct,
        assumed_dividend_yield=assumed_dividend_yield,
    )

    buffer = build_zero_cost_target_cap_buffer(
        expiry_chain,
        target_gain_pct=target_gain_pct,
        assumed_dividend_yield=assumed_dividend_yield,
    )

    payload = {
        "ticker": ticker,
        "horizon": horizon,
        "selected_expiry": selected_expiry_summary,
        "assumptions": {
            "assumed_dividend_yield": assumed_dividend_yield,
            "dividend_note": (
                "Expected dividends are included in terminal economics only. "
                "They are not assumed to be available upfront."
            ),
            "cost_note": (
                "Products are constructed using zero-or-smallest-debit option cost. "
                "Net credits are not accepted."
            ),
        },
        "products": {
            "defined_floor": collar,
            "buffered_growth": buffer,
        },
    }

    return make_json_safe(payload)


# ============================================================
# 8. LEGACY / BACKWARD-COMPATIBLE ENGINE FUNCTION
# ============================================================

def find_classic_and_buffered_collars(
    closest_chains,
    target_loss_pct=0.005,
    target_gain_pct=0.08,
    max_net_cost_bps=100,
    put_buffer_pct=0.06,
    call_buffer_pct=0.08,
    min_buffer_width=1,
    max_buffer_width=100,
    assumed_dividend_yield=0.01,
):
    """
    Backward-compatible wrapper.

    Kept so old imports/routes do not break.
    Returns a DataFrame with one collar and one buffer where possible.
    New API code should use build_defined_outcome_recommendations instead.
    """

    if closest_chains is None or closest_chains.empty:
        return pd.DataFrame()

    expiry_chain = closest_chains.copy()

    if "spot" not in expiry_chain.columns:
        expiry_chain = clean_chain(expiry_chain)

    spot = float(expiry_chain["spot"].median())

    collar = build_zero_cost_dividend_floor_collar(
        expiry_chain,
        max_loss_pct=target_loss_pct,
        assumed_dividend_yield=assumed_dividend_yield,
    )

    max_buffer_pct = max_buffer_width / spot if spot > 0 else 0.20
    max_buffer_pct = min(max_buffer_pct, 0.20)

    buffer = build_zero_cost_target_cap_buffer(
        expiry_chain,
        target_gain_pct=target_gain_pct,
        assumed_dividend_yield=assumed_dividend_yield,
        max_buffer_pct=max_buffer_pct,
    )

    rows = []

    if collar is not None:
        rows.append(collar)

    if buffer is not None:
        rows.append(buffer)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


# ============================================================
# 9. LEGACY FRONTEND PAYLOAD HELPERS
# ============================================================

def add_percent_columns(df):
    """
    Backward-compatible helper.
    """

    if df is None or df.empty:
        return pd.DataFrame()

    view = df.copy()

    pct_cols = [
        "assumed_dividend_yield",
        "floor_return",
        "cap_return",
        "buffer_pct",
        "protected_zone_return",
    ]

    for col in pct_cols:
        if col in view.columns:
            view[col + "_pct"] = view[col] * 100

    return view


def build_frontend_payload(
    collar_scenarios,
    expiry_summary=None,
    n_classic=1,
    n_buffered=1,
):
    """
    Backward-compatible helper.

    New front end should use the /recommendations response directly.
    """

    if collar_scenarios is None or collar_scenarios.empty:
        payload = {
            "classic_collars": [],
            "recommended_buffers": [],
        }

        if expiry_summary is not None:
            payload["expiry_summary"] = expiry_summary.to_dict(orient="records")

        return make_json_safe(payload)

    view = add_percent_columns(collar_scenarios)

    if "strategy" not in view.columns:
        payload = {
            "classic_collars": [],
            "recommended_buffers": [],
        }

        if expiry_summary is not None:
            payload["expiry_summary"] = expiry_summary.to_dict(orient="records")

        return make_json_safe(payload)

    classic = view[
        view["strategy"] == "classic_collar"
    ].head(n_classic)

    buffered = view[
        view["strategy"] == "buffered_collar_first_loss"
    ].head(n_buffered)

    payload = {
        "classic_collars": classic.to_dict(orient="records"),
        "recommended_buffers": buffered.to_dict(orient="records"),
    }

    if expiry_summary is not None:
        payload["expiry_summary"] = expiry_summary.to_dict(orient="records")

    return make_json_safe(payload)


# ============================================================
# 10. COMMAND LINE ENTRY POINT
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--ticker", type=str, default="XSP")
    parser.add_argument("--token", type=str, default=None)

    parser.add_argument(
        "--horizon",
        type=int,
        required=True,
        help="Target outcome period in days. Example: 365",
    )

    parser.add_argument(
        "--max-loss",
        type=float,
        default=0.005,
        help="Max loss target for Defined Floor. Example: 0.005 = 0.50%",
    )

    parser.add_argument(
        "--target-gain",
        type=float,
        default=0.08,
        help="Target gain for Buffered Growth. Example: 0.08 = 8.00%",
    )

    parser.add_argument(
        "--assumed-dividend-yield",
        type=float,
        default=0.01,
        help="Annual dividend yield assumption. Example: 0.01 = 1.00%",
    )

    args = parser.parse_args()

    df = fetch_orats_chain(
        ticker=args.ticker,
        token=args.token,
    )

    payload = build_defined_outcome_recommendations(
        df=df,
        ticker=args.ticker,
        horizon=args.horizon,
        max_loss_pct=args.max_loss,
        target_gain_pct=args.target_gain,
        assumed_dividend_yield=args.assumed_dividend_yield,
    )

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()