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
    if token is None:
        token = os.getenv("ORATS_TOKEN")

    if not token:
        raise ValueError("Missing ORATS token. Either set ORATS_TOKEN or pass --token.")

    url = (
        "https://api.orats.io/datav2/live/one-minute/strikes/chain"
        f"?token={token}&ticker={ticker}"
    )

    response = requests.get(url)

    if response.status_code != 200:
        raise RuntimeError(
            f"ORATS request failed: {response.status_code} - {response.text[:500]}"
        )

    return pd.read_csv(StringIO(response.text))


# ============================================================
# 2. DATA PREP
# ============================================================

def clean_chain(df, ticker=None):
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
    return (
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


def select_single_expiry(
    chain,
    target_dte=365,
    prefer_at_or_after=True,
    max_dte_overage=200,
    max_dte_underage=30,
):
    expiry_summary = get_expiry_summary(chain)

    if expiry_summary.empty:
        raise ValueError("No expirations available.")

    expiry_summary["dte_diff"] = (expiry_summary["dte"] - target_dte).abs()
    expiry_summary["dte_over_target"] = expiry_summary["dte"] - target_dte

    if prefer_at_or_after:
        eligible = expiry_summary[
            (expiry_summary["dte"] >= target_dte - max_dte_underage)
            & (expiry_summary["dte"] <= target_dte + max_dte_overage)
        ].copy()

        if not eligible.empty:
            selected = eligible.sort_values(
                ["dte_diff", "dte_over_target"],
                ascending=[True, False],
            ).iloc[0]
        else:
            selected = expiry_summary.sort_values(
                ["dte_diff", "dte_over_target"],
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


def get_closest_expiry_chains(df, target_dte=365, n_expiries=5, ticker=None):
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
# 3. HELPERS
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


def max_loss_display_pct(floor_return):
    if floor_return is None or pd.isna(floor_return):
        return None
    return max(0.0, -float(floor_return) * 100)


def make_json_safe(obj):
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
# 4. DEFINED FLOOR COLLAR
# ============================================================

def build_zero_cost_dividend_floor_collar(
    expiry_chain,
    max_loss_pct=0.005,
    assumed_dividend_yield=0.01,
    max_near_zero_bps=50,
):
    """
    Product: Defined Floor

    Dividend-funded premium logic:
    - The collar can tolerate a net option debit roughly equal to expected dividends.
    - This allows selling a higher OTM call and improves upside.
    - The user still pays the debit upfront; dividends are expected to offset it over time.

    Terminal economics:
    floor value = put strike + expected dividends - net option cost
    cap value   = call strike + expected dividends - net option cost
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

    target_floor_return = -max_loss_pct

    valid_puts = g[
        (g["strike"] < spot)
        & (g["putBidPrice"] > 0)
        & (g["putAskPrice"] > 0)
        & (g["putMid"] > 0)
    ].copy()

    valid_calls = g[
        (g["strike"] > spot)
        & (g["callBidPrice"] > 0)
        & (g["callAskPrice"] > 0)
        & (g["callMid"] > 0)
    ].copy()

    if valid_puts.empty or valid_calls.empty:
        return None

    approximate_required_put = (
        notional * (1 - max_loss_pct) - expected_dividend_dollars
    ) / MULT

    valid_puts = valid_puts[
        valid_puts["strike"] >= approximate_required_put - 10
    ].copy()

    if valid_puts.empty:
        return None

    put_strikes = valid_puts["strike"].to_numpy(dtype=float)
    put_costs = valid_puts["putMid"].to_numpy(dtype=float) * MULT

    put_asks = valid_puts.get(
        "putAskPrice",
        valid_puts["putMid"],
    ).to_numpy(dtype=float)

    put_volumes = valid_puts.get(
        "putVolume",
        pd.Series(0, index=valid_puts.index),
    ).fillna(0).to_numpy(dtype=float)

    put_oi = valid_puts.get(
        "putOpenInterest",
        pd.Series(0, index=valid_puts.index),
    ).fillna(0).to_numpy(dtype=float)

    call_strikes = valid_calls["strike"].to_numpy(dtype=float)
    call_credits = valid_calls["callMid"].to_numpy(dtype=float) * MULT

    call_bids = valid_calls.get(
        "callBidPrice",
        valid_calls["callMid"],
    ).to_numpy(dtype=float)

    call_volumes = valid_calls.get(
        "callVolume",
        pd.Series(0, index=valid_calls.index),
    ).fillna(0).to_numpy(dtype=float)

    call_oi = valid_calls.get(
        "callOpenInterest",
        pd.Series(0, index=valid_calls.index),
    ).fillna(0).to_numpy(dtype=float)

    net_cost = put_costs[:, None] - call_credits[None, :]
    net_cost_bps = net_cost / notional * 10000
    abs_net_cost_bps = np.abs(net_cost_bps)

    dividend_budget = expected_dividend_dollars
    premium_gap = net_cost - dividend_budget
    premium_gap_bps = premium_gap / notional * 10000
    abs_premium_gap_bps = np.abs(premium_gap_bps)

    floor_value = (
        put_strikes[:, None] * MULT
        + expected_dividend_dollars
        - net_cost
    )

    cap_value = (
        call_strikes[None, :] * MULT
        + expected_dividend_dollars
        - net_cost
    )

    floor_return = floor_value / notional - 1
    cap_return = cap_value / notional - 1

    valid_mask = floor_return >= target_floor_return

    near_dividend_funded = abs_premium_gap_bps <= max_near_zero_bps

    if not np.any(valid_mask):
        selection_mask = np.isfinite(floor_return) & np.isfinite(cap_return)
        outside_tolerance = True
        exact_floor_match = False
    else:
        preferred_mask = valid_mask & near_dividend_funded

        if np.any(preferred_mask):
            selection_mask = preferred_mask
            outside_tolerance = False
        else:
            selection_mask = valid_mask
            outside_tolerance = True

        exact_floor_match = True

    worst_net_cost = (
        put_asks[:, None] - call_bids[None, :]
    ) * MULT

    bid_ask_drag_dollars = worst_net_cost - net_cost
    bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

    total_volume = put_volumes[:, None] + call_volumes[None, :]
    total_oi = put_oi[:, None] + call_oi[None, :]
    liquidity = np.log1p(total_volume) + np.log1p(total_oi)

    if exact_floor_match:
        score = (
            cap_return * 1_000_000
            - abs_premium_gap_bps * 100
            - bid_ask_drag_bps * 10
            + liquidity
        )
    else:
        score = (
            floor_return * 1_000_000
            + cap_return * 10_000
            - abs_premium_gap_bps * 100
            - bid_ask_drag_bps * 10
            + liquidity
        )

    score = np.where(selection_mask, score, -np.inf)

    if not np.isfinite(score).any():
        return None

    best_flat_idx = int(np.argmax(score))
    best_put_idx, best_call_idx = np.unravel_index(best_flat_idx, score.shape)

    best_put = valid_puts.iloc[best_put_idx]
    best_call = valid_calls.iloc[best_call_idx]

    best_net_cost = float(net_cost[best_put_idx, best_call_idx])
    best_net_cost_bps = float(net_cost_bps[best_put_idx, best_call_idx])

    best_premium_gap = float(premium_gap[best_put_idx, best_call_idx])
    best_premium_gap_bps = float(premium_gap_bps[best_put_idx, best_call_idx])

    best_floor_value = float(floor_value[best_put_idx, best_call_idx])
    best_cap_value = float(cap_value[best_put_idx, best_call_idx])
    best_floor_return = float(floor_return[best_put_idx, best_call_idx])
    best_cap_return = float(cap_return[best_put_idx, best_call_idx])
    best_bid_ask_drag_bps = float(bid_ask_drag_bps[best_put_idx, best_call_idx])
    best_total_volume = float(total_volume[best_put_idx, best_call_idx])
    best_total_oi = float(total_oi[best_put_idx, best_call_idx])
    best_liquidity = float(liquidity[best_put_idx, best_call_idx])
    best_near_dividend_funded = bool(
        near_dividend_funded[best_put_idx, best_call_idx]
    )

    if abs(best_premium_gap_bps) <= max_near_zero_bps:
        cost_display_label = "dividend-funded"
    elif best_premium_gap > 0:
        cost_display_label = "above expected dividends"
    else:
        cost_display_label = "below expected dividends"

    return make_json_safe({
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

        "dividend_budget_dollars": expected_dividend_dollars,
        "premium_gap_dollars": best_premium_gap,
        "premium_gap_bps": best_premium_gap_bps,

        "target_max_loss_pct": max_loss_pct,
        "target_floor_return": target_floor_return,

        "long_put_strike": float(best_put["strike"]),
        "short_put_strike": None,
        "call_strike": float(best_call["strike"]),

        "put_cost_dollars": float(best_put["putMid"]) * MULT,
        "call_credit_dollars": float(best_call["callMid"]) * MULT,
        "net_cost_dollars": best_net_cost,
        "net_cost_bps": best_net_cost_bps,

        "near_zero_cost_ok": best_near_dividend_funded,
        "dividend_funded_ok": best_near_dividend_funded,
        "outside_tolerance": outside_tolerance,
        "exact_floor_match": exact_floor_match,
        "requested_floor_met": bool(best_floor_return >= target_floor_return),
        "fallback_reason": None if exact_floor_match else (
            "No available collar met the requested max-loss target. "
            "Returned the closest available defined-floor collar."
        ),
        "max_near_zero_bps": max_near_zero_bps,

        "floor_value": best_floor_value,
        "cap_value": best_cap_value,
        "floor_return": best_floor_return,
        "cap_return": best_cap_return,
        "max_loss_dollars": max(0.0, notional - best_floor_value),
        "max_gain_dollars": best_cap_value - notional,

        "buffer_width_points": None,
        "buffer_pct": None,
        "protected_zone_value": None,
        "protected_zone_return": None,

        "bid_ask_drag_bps": best_bid_ask_drag_bps,
        "total_volume": best_total_volume,
        "total_oi": best_total_oi,
        "liquidity_score": best_liquidity,

        "display": {
            "title": "Defined Floor",
            "subtitle": "Hard-loss target with capped upside",
            "estimated_max_loss_pct": max_loss_display_pct(best_floor_return),
            "estimated_cap_pct": round_pct(best_cap_return),
            "estimated_option_cost_dollars": best_net_cost,
            "estimated_option_cost_label": cost_display_label,
            "estimated_dividends_dollars": expected_dividend_dollars,
            "estimated_premium_gap_dollars": best_premium_gap,
            "explanation": (
                "Designed to target a defined floor over the selected outcome period. "
                "Expected dividends are used to offset the option debit over the period, "
                "which may allow a higher upside cap."
            ),
        },
    })


# ============================================================
# 5. BUFFERED GROWTH
# ============================================================

def build_zero_cost_target_cap_buffer(
    expiry_chain,
    target_gain_pct=0.08,
    assumed_dividend_yield=0.01,
    target_buffer_pct=0.10,
    max_near_zero_bps=25,
    buffer_tolerance_pct=0.015,
):
    g = expiry_chain.copy()

    if g.empty:
        return None

    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    notional = spot * MULT

    expected_dividend_dollars = notional * assumed_dividend_yield * (dte / 365.25)
    expected_dividend_per_share = expected_dividend_dollars / MULT

    long_put_target = spot - expected_dividend_per_share

    valid_puts = g[
        (g["strike"] < spot)
        & (g["putBidPrice"] > 0)
        & (g["putAskPrice"] > 0)
        & (g["putMid"] > 0)
    ].copy()

    valid_calls = g[
        (g["strike"] > spot)
        & (g["callBidPrice"] > 0)
        & (g["callAskPrice"] > 0)
        & (g["callMid"] > 0)
    ].copy()

    if valid_puts.empty or valid_calls.empty:
        return None

    valid_puts["long_put_distance"] = (
        valid_puts["strike"] - long_put_target
    ).abs()

    long_put = valid_puts.sort_values(
        ["long_put_distance", "strike"],
        ascending=[True, False],
    ).iloc[0]

    long_put_strike = float(long_put["strike"])
    long_put_cost = float(long_put["putMid"]) * MULT

    short_put_target = long_put_strike - (spot * target_buffer_pct)

    short_put_candidates = valid_puts[
        valid_puts["strike"] < long_put_strike
    ].copy()

    if short_put_candidates.empty:
        return None

    short_put_candidates["short_put_distance"] = (
        short_put_candidates["strike"] - short_put_target
    ).abs()

    short_put_candidates["buffer_width_points"] = (
        long_put_strike - short_put_candidates["strike"]
    )

    short_put_candidates["buffer_pct"] = (
        short_put_candidates["buffer_width_points"] / spot
    )

    short_put_candidates["buffer_error"] = (
        short_put_candidates["buffer_pct"] - target_buffer_pct
    ).abs()

    buffer_pool = short_put_candidates[
        short_put_candidates["buffer_error"] <= buffer_tolerance_pct
    ].copy()

    if buffer_pool.empty:
        buffer_pool = short_put_candidates.sort_values(
            ["buffer_error", "strike"],
            ascending=[True, False],
        ).head(3).copy()
        buffer_exact_match = False
    else:
        buffer_pool = buffer_pool.sort_values(
            ["buffer_error", "strike"],
            ascending=[True, False],
        ).head(3).copy()
        buffer_exact_match = True

    rows = []

    for _, short_put in buffer_pool.iterrows():
        short_put_strike = float(short_put["strike"])
        short_put_credit = float(short_put["putMid"]) * MULT

        buffer_width_points = long_put_strike - short_put_strike
        buffer_pct = buffer_width_points / spot
        buffer_error = abs(buffer_pct - target_buffer_pct)

        put_spread_cost = long_put_cost - short_put_credit

        for _, call in valid_calls.iterrows():
            call_strike = float(call["strike"])
            call_credit = float(call["callMid"]) * MULT

            net_cost = put_spread_cost - call_credit
            net_cost_bps = net_cost / notional * 10000
            abs_net_cost_bps = abs(net_cost_bps)

            cap_value = (
                call_strike * MULT
                + expected_dividend_dollars
                - net_cost
            )
            cap_return = cap_value / notional - 1

            protected_start_return = long_put_strike / spot - 1
            protected_end_return = short_put_strike / spot - 1

            long_put_ask = float(long_put.get("putAskPrice", long_put["putMid"]))
            short_put_bid = float(short_put.get("putBidPrice", short_put["putMid"]))
            call_bid = float(call.get("callBidPrice", call["callMid"]))

            worst_net_cost = (long_put_ask - short_put_bid - call_bid) * MULT
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
                "abs_net_cost_bps": abs_net_cost_bps,
                "buffer_width_points": buffer_width_points,
                "buffer_pct": buffer_pct,
                "buffer_error": buffer_error,
                "protected_start_return": protected_start_return,
                "protected_end_return": protected_end_return,
                "cap_value": cap_value,
                "cap_return": cap_return,
                "bid_ask_drag_bps": bid_ask_drag_bps,
                "total_volume": total_volume,
                "total_oi": total_oi,
                "liquidity_score": liq_score,
            })

    if not rows:
        return None

    candidates = pd.DataFrame(rows)
    candidates["near_zero"] = candidates["abs_net_cost_bps"] <= max_near_zero_bps

    best = candidates.sort_values(
        [
            "buffer_error",
            "abs_net_cost_bps",
            "cap_return",
            "bid_ask_drag_bps",
            "total_oi",
        ],
        ascending=[True, True, False, True, False],
    ).iloc[0]

    net_cost = float(best["net_cost"])
    net_cost_bps = float(best["net_cost_bps"])

    if abs(net_cost_bps) <= max_near_zero_bps:
        cost_display_label = "approximately $0"
    elif net_cost > 0:
        cost_display_label = "small debit"
    else:
        cost_display_label = "small credit"

    return make_json_safe({
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

        "target_buffer_pct": target_buffer_pct,
        "actual_buffer_pct": float(best["buffer_pct"]),
        "buffer_error": float(best["buffer_error"]),
        "buffer_exact_match": bool(buffer_exact_match),

        "long_put_target": long_put_target,
        "short_put_target": short_put_target,

        "long_put_strike": float(best["long_put_strike"]),
        "short_put_strike": float(best["short_put_strike"]),
        "call_strike": float(best["call_strike"]),

        "long_put_cost_dollars": float(best["long_put_cost"]),
        "short_put_credit_dollars": float(best["short_put_credit"]),
        "put_spread_cost_dollars": float(best["put_spread_cost"]),
        "call_credit_dollars": float(best["call_credit"]),
        "net_cost_dollars": net_cost,
        "net_cost_bps": net_cost_bps,

        "near_zero_cost_ok": abs(net_cost_bps) <= max_near_zero_bps,
        "max_near_zero_bps": max_near_zero_bps,

        "buffer_width_points": float(best["buffer_width_points"]),
        "buffer_pct": float(best["buffer_pct"]),

        "protected_start_return": float(best["protected_start_return"]),
        "protected_end_return": float(best["protected_end_return"]),

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
            "subtitle": "First-loss buffer with capped upside",
            "estimated_buffer_pct": round_pct(float(best["buffer_pct"])),
            "estimated_cap_pct": round_pct(float(best["cap_return"])),
            "protected_start_pct": round_pct(float(best["protected_start_return"])),
            "protected_end_pct": round_pct(float(best["protected_end_return"])),
            "estimated_option_cost_dollars": net_cost,
            "estimated_option_cost_label": cost_display_label,
            "estimated_dividends_dollars": expected_dividend_dollars,
            "explanation": (
                "Designed to help offset the first part of market losses using a put spread. "
                "Upside is capped in exchange for defined downside protection."
            ),
        },
    })


# ============================================================
# 6. RECOMMENDATION PAYLOAD
# ============================================================

def build_defined_outcome_recommendations(
    df,
    ticker="XSP",
    horizon=365,
    max_loss_pct=0.005,
    target_gain_pct=0.08,
    assumed_dividend_yield=0.01,
    target_buffer_pct=0.10,
):
    chain = clean_chain(df, ticker=ticker)

    expiry_chain, selected_expiry_summary, _ = select_single_expiry(
        chain,
        target_dte=horizon,
        prefer_at_or_after=True,
        max_dte_overage=200,
        max_dte_underage=30,
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
        target_buffer_pct=target_buffer_pct,
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
                "Defined Floor may use expected dividends to offset the option debit "
                "over the outcome period. Dividends are not assumed to be available upfront. "
                "Buffered Growth still targets a near-zero option cost."
            ),
        },
        "products": {
            "defined_floor": collar,
            "buffered_growth": buffer,
        },
    }

    return make_json_safe(payload)


# ============================================================
# 7. LEGACY / BACKWARD-COMPATIBLE HELPERS
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
        max_near_zero_bps=max_net_cost_bps,
    )

    max_buffer_pct = max_buffer_width / spot if spot > 0 else 0.20
    max_buffer_pct = min(max_buffer_pct, 0.20)

    buffer = build_zero_cost_target_cap_buffer(
        expiry_chain,
        target_gain_pct=target_gain_pct,
        assumed_dividend_yield=assumed_dividend_yield,
        target_buffer_pct=put_buffer_pct,
    )

    rows = []

    if collar is not None:
        rows.append(collar)

    if buffer is not None:
        rows.append(buffer)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def add_percent_columns(df):
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
# 8. MARRIED PUT
# ============================================================

def build_married_put(
    expiry_chain,
    max_loss_pct=0.10,
    assumed_dividend_yield=0.0,
):
    g = expiry_chain.copy()

    if g.empty:
        return None

    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    notional = spot * MULT

    expected_dividend_dollars = notional * assumed_dividend_yield * (dte / 365.25)
    expected_dividend_per_share = expected_dividend_dollars / MULT

    target_put_strike = spot * (1 - max_loss_pct) - expected_dividend_per_share

    puts = g[
        (g["strike"] < spot)
        & (g["putBidPrice"] > 0)
        & (g["putAskPrice"] > 0)
        & (g["putMid"] > 0)
    ].copy()

    if puts.empty:
        return None

    puts["strike_distance"] = (puts["strike"] - target_put_strike).abs()
    puts["put_cost_dollars"] = puts["putMid"] * MULT

    puts["put_ask_dollars"] = puts["putAskPrice"].fillna(puts["putMid"]) * MULT
    puts["put_bid_dollars"] = puts["putBidPrice"].fillna(puts["putMid"]) * MULT

    puts["bid_ask_drag_dollars"] = puts["put_ask_dollars"] - puts["put_cost_dollars"]
    puts["bid_ask_drag_bps"] = puts["bid_ask_drag_dollars"] / notional * 10000

    best = puts.sort_values(
        [
            "strike_distance",
            "bid_ask_drag_bps",
            "putOpenInterest",
            "putVolume",
        ],
        ascending=[True, True, False, False],
    ).iloc[0]

    put_strike = float(best["strike"])
    put_cost_dollars = float(best["put_cost_dollars"])

    protection_floor_value = put_strike * MULT + expected_dividend_dollars
    protection_floor_return = protection_floor_value / notional - 1

    actual_floor_value = protection_floor_value - put_cost_dollars
    actual_floor_return = actual_floor_value / notional - 1

    max_loss_dollars = notional - actual_floor_value
    insurance_cost_pct = put_cost_dollars / notional

    liq_score, total_volume, total_oi = liquidity_score(best)

    return make_json_safe({
        "product_name": "Insured Upside",
        "strategy": "married_put",
        "structure": "married_put",
        "backend_structure": "long_underlying_plus_long_put",

        "expirDate": g["expirDate"].iloc[0],
        "dte": dte,
        "spot": spot,
        "notional": notional,

        "assumed_dividend_yield": assumed_dividend_yield,
        "expected_dividend_dollars": expected_dividend_dollars,
        "expected_dividend_per_share": expected_dividend_per_share,

        "target_protection_pct": max_loss_pct,
        "target_put_strike": target_put_strike,
        "actual_protection_pct": abs(protection_floor_return),
        "strike_distance": float(best["strike_distance"]),

        "long_put_strike": put_strike,
        "short_put_strike": None,
        "call_strike": None,

        "put_cost_dollars": put_cost_dollars,
        "put_cost_per_share": float(best["putMid"]),
        "put_bid_per_share": float(best["putBidPrice"]),
        "put_ask_per_share": float(best["putAskPrice"]),

        "call_credit_dollars": None,
        "net_cost_dollars": put_cost_dollars,
        "net_cost_bps": float(put_cost_dollars / notional * 10000),

        "insurance_cost_pct": insurance_cost_pct,

        "protection_floor_value": protection_floor_value,
        "protection_floor_return": protection_floor_return,

        "floor_value": actual_floor_value,
        "floor_return": actual_floor_return,
        "cap_value": None,
        "cap_return": None,

        "max_loss_dollars": max_loss_dollars,
        "max_loss_pct": abs(actual_floor_return),
        "max_gain_dollars": None,
        "max_gain_label": "Unlimited",

        "bid_ask_drag_bps": float(best["bid_ask_drag_bps"]),
        "total_volume": total_volume,
        "total_oi": total_oi,
        "liquidity_score": liq_score,

        "display": {
            "title": "Insured Upside",
            "subtitle": "Downside insurance with unlimited upside",

            "protection_level_pct": round_pct(abs(protection_floor_return)),
            "insurance_strike": put_strike,
            "insurance_cost_pct": round_pct(insurance_cost_pct),
            "estimated_max_loss_pct": round_pct(abs(actual_floor_return)),
            "estimated_cap_pct": None,
            "estimated_max_gain_label": "Unlimited",

            "estimated_option_cost_dollars": put_cost_dollars,
            "estimated_dividends_dollars": expected_dividend_dollars,

            "explanation": (
                "Designed to target the selected downside protection level by buying a put "
                "near the requested protection strike. The insurance cost is shown separately "
                "and increases the actual worst-case loss."
            ),
        },
    })


# ============================================================
# 9. COVERED CALL
# ============================================================

def build_covered_call(
    expiry_chain,
    target_income_pct=0.05,
    assumed_dividend_yield=0.0,
):
    g = expiry_chain.copy()

    if g.empty:
        return None

    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    notional = spot * MULT

    expected_dividend_dollars = notional * assumed_dividend_yield * (dte / 365.25)
    expected_dividend_per_share = expected_dividend_dollars / MULT

    calls = g[
        (g["strike"] > spot)
        & (g["callBidPrice"] > 0)
        & (g["callAskPrice"] > 0)
        & (g["callMid"] > 0)
    ].copy()

    if calls.empty:
        return None

    calls["call_credit_dollars"] = calls["callMid"] * MULT

    calls["option_income_pct"] = calls["call_credit_dollars"] / notional
    calls["total_income_dollars"] = (
        calls["call_credit_dollars"] + expected_dividend_dollars
    )
    calls["total_income_pct"] = calls["total_income_dollars"] / notional

    calls["income_error"] = (
        calls["total_income_pct"] - target_income_pct
    ).abs()

    calls["cap_value"] = (
        calls["strike"] * MULT
        + calls["call_credit_dollars"]
        + expected_dividend_dollars
    )

    calls["cap_return"] = calls["cap_value"] / notional - 1

    calls["call_bid_dollars"] = calls["callBidPrice"] * MULT
    calls["call_ask_dollars"] = calls["callAskPrice"] * MULT
    calls["bid_ask_drag_dollars"] = calls["call_credit_dollars"] - calls["call_bid_dollars"]
    calls["bid_ask_drag_bps"] = calls["bid_ask_drag_dollars"] / notional * 10000

    best = calls.sort_values(
        [
            "income_error",
            "bid_ask_drag_bps",
            "callOpenInterest",
            "callVolume",
        ],
        ascending=[True, True, False, False],
    ).iloc[0]

    liq_score, total_volume, total_oi = liquidity_score(best)

    return make_json_safe({
        "product_name": "Income",
        "strategy": "covered_call",
        "structure": "covered_call",
        "backend_structure": "long_underlying_plus_short_call",

        "expirDate": g["expirDate"].iloc[0],
        "dte": dte,
        "spot": spot,
        "notional": notional,

        "assumed_dividend_yield": assumed_dividend_yield,
        "expected_dividend_dollars": expected_dividend_dollars,
        "expected_dividend_per_share": expected_dividend_per_share,

        "target_income_pct": target_income_pct,

        "long_put_strike": None,
        "short_put_strike": None,
        "call_strike": float(best["strike"]),

        "put_cost_dollars": None,
        "call_credit_dollars": float(best["call_credit_dollars"]),
        "net_cost_dollars": -float(best["call_credit_dollars"]),
        "net_cost_bps": -float(best["call_credit_dollars"] / notional * 10000),

        "option_income_pct": float(best["option_income_pct"]),
        "total_income_dollars": float(best["total_income_dollars"]),
        "total_income_pct": float(best["total_income_pct"]),
        "income_error": float(best["income_error"]),

        "floor_value": None,
        "floor_return": None,
        "cap_value": float(best["cap_value"]),
        "cap_return": float(best["cap_return"]),

        "max_loss_dollars": None,
        "max_gain_dollars": float(best["cap_value"]) - notional,

        "bid_ask_drag_bps": float(best["bid_ask_drag_bps"]),
        "total_volume": total_volume,
        "total_oi": total_oi,
        "liquidity_score": liq_score,

        "display": {
            "title": "Income",
            "subtitle": "Covered call income with capped upside",
            "estimated_income_pct": round_pct(float(best["total_income_pct"])),
            "estimated_cap_pct": round_pct(float(best["cap_return"])),
            "estimated_option_income_dollars": float(best["call_credit_dollars"]),
            "estimated_dividends_dollars": expected_dividend_dollars,
            "explanation": (
                "Designed to generate option income by selling a call above the current price. "
                "Upside is capped at the call strike while downside remains exposed."
            ),
        },
    })


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

    parser.add_argument(
        "--target-buffer-pct",
        type=float,
        default=0.10,
        help="Target buffer for Buffered Growth. Example: 0.10 = 10.00%",
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
        target_buffer_pct=args.target_buffer_pct,
    )

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()