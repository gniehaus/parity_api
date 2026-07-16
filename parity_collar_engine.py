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
    max_dte_overage=180,
    max_dte_underage=45,
):
    expiry_summary = get_expiry_summary(chain)

    if expiry_summary.empty:
        raise ValueError("No expirations available.")

    expiry_summary["dte_diff"] = (expiry_summary["dte"] - target_dte).abs()
    expiry_summary["dte_over_target"] = expiry_summary["dte"] - target_dte

    if prefer_at_or_after:
        # Use expiries in an acceptable window around target.
        eligible = expiry_summary[
            (expiry_summary["dte"] >= target_dte - max_dte_underage)
            & (expiry_summary["dte"] <= target_dte + max_dte_overage)
        ].copy()

        if not eligible.empty:
            # Pick closest to target.
            # If tied, prefer longer.
            selected = eligible.sort_values(
                ["dte_diff", "dte_over_target"],
                ascending=[True, False],
            ).iloc[0]
        else:
            # If no expiry is reasonably close, choose the closest overall.
            # If tied, prefer longer.
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
    max_near_zero_bps=50,
):
    """
    Product: Defined Floor

    Return calculations are based on the investor's actual cash invested:

        invested_amount = underlying notional + net option debit

        floor_return =
            terminal floor value / invested_amount - 1

        cap_return =
            terminal cap value / invested_amount - 1

    Selection hierarchy:
    1. Prefer collars meeting the requested floor within the debit target.
    2. If none exist, return the lowest-debit collar meeting the floor.
    3. If no collar meets the floor, return the closest available floor.
    4. Within the selected pool, maximize upside.
    """

    g = expiry_chain.copy()

    if g.empty:
        return None

    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    underlying_notional = spot * MULT

    expected_dividend_dollars = (
        underlying_notional
        * assumed_dividend_yield
        * (dte / 365.25)
    )
    expected_dividend_per_share = expected_dividend_dollars / MULT

    target_floor_return = -max_loss_pct

    # --------------------------------------------------------
    # Valid option universe
    # --------------------------------------------------------

    valid_puts = g[
        (g["strike"] < spot)
        & (g["putBidPrice"] > 0)
        & (g["putAskPrice"] > 0)
        & np.isfinite(g["putMid"])
        & (g["putMid"] > 0)
    ].copy()

    valid_calls = g[
        (g["strike"] > spot)
        & (g["callBidPrice"] > 0)
        & (g["callAskPrice"] > 0)
        & np.isfinite(g["callMid"])
        & (g["callMid"] > 0)
    ].copy()

    if valid_puts.empty or valid_calls.empty:
        return None

    approximate_required_put = (
        underlying_notional * (1 - max_loss_pct)
        - expected_dividend_dollars
    ) / MULT

    valid_puts = valid_puts[
        valid_puts["strike"] >= approximate_required_put - 10
    ].copy()

    if valid_puts.empty:
        return None

    valid_puts = valid_puts.sort_values("strike").reset_index(drop=True)
    valid_calls = valid_calls.sort_values("strike").reset_index(drop=True)

    # --------------------------------------------------------
    # Convert to arrays
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Build put/call grids
    # --------------------------------------------------------

    net_cost = (
        put_costs[:, None]
        - call_credits[None, :]
    )

    net_cost_bps = (
        net_cost / underlying_notional * 10_000
    )

    # Actual amount the investor must contribute today.
    #
    # Since this product does not accept credits, qualifying candidates
    # will have invested_amount >= underlying_notional.
    invested_amount = (
        underlying_notional
        + net_cost
    )

    # Terminal values do not subtract option cost again.
    # The option cost is already included in invested_amount.
    floor_value = (
        put_strikes[:, None] * MULT
        + expected_dividend_dollars
    )

    cap_value = (
        call_strikes[None, :] * MULT
        + expected_dividend_dollars
    )

    floor_return = (
        floor_value / invested_amount - 1
    )

    cap_return = (
        cap_value / invested_amount - 1
    )

    # Conservative execution estimate:
    # buy put at ask and sell call at bid.
    estimated_execution_cost = (
        put_asks[:, None]
        - call_bids[None, :]
    ) * MULT

    estimated_execution_cost_bps = (
        estimated_execution_cost
        / underlying_notional
        * 10_000
    )

    estimated_execution_investment = (
        underlying_notional
        + estimated_execution_cost
    )

    execution_floor_return = (
        floor_value / estimated_execution_investment - 1
    )

    execution_cap_return = (
        cap_value / estimated_execution_investment - 1
    )

    bid_ask_drag_dollars = (
        estimated_execution_cost - net_cost
    )

    bid_ask_drag_bps = (
        bid_ask_drag_dollars
        / underlying_notional
        * 10_000
    )

    total_volume = (
        put_volumes[:, None]
        + call_volumes[None, :]
    )

    total_oi = (
        put_oi[:, None]
        + call_oi[None, :]
    )

    liquidity = (
        np.log1p(total_volume)
        + np.log1p(total_oi)
    )

    # --------------------------------------------------------
    # Candidate-selection hierarchy
    # --------------------------------------------------------

    finite_mask = (
        np.isfinite(floor_return)
        & np.isfinite(cap_return)
        & np.isfinite(net_cost_bps)
        & np.isfinite(invested_amount)
        & (invested_amount > 0)
    )

    # Do not accept net-credit structures.
    non_credit_mask = (
        finite_mask
        & (net_cost_bps >= 0)
    )

    floor_ok_mask = (
        non_credit_mask
        & (floor_return >= target_floor_return)
    )

    near_zero_mask = (
        floor_ok_mask
        & (net_cost_bps <= max_near_zero_bps)
    )

    minimum_debit_bps = None
    best_available_floor = None

    if np.any(near_zero_mask):
        selection_mask = near_zero_mask
        selection_mode = "near_zero"
        outside_tolerance = False
        exact_floor_match = True
        fallback_reason = None

    elif np.any(floor_ok_mask):
        minimum_debit_bps = float(
            np.min(net_cost_bps[floor_ok_mask])
        )

        selection_mask = (
            floor_ok_mask
            & np.isclose(
                net_cost_bps,
                minimum_debit_bps,
                atol=0.01,
                rtol=0,
            )
        )

        selection_mode = "lowest_debit"
        outside_tolerance = True
        exact_floor_match = True

        fallback_reason = (
            f"No collar met the requested floor within the "
            f"{max_near_zero_bps}-bps debit target. "
            f"Returned the lowest-debit collar meeting the floor "
            f"at {minimum_debit_bps:.1f} bps."
        )

    else:
        if not np.any(non_credit_mask):
            return None

        best_available_floor = float(
            np.max(floor_return[non_credit_mask])
        )

        closest_floor_mask = (
            non_credit_mask
            & np.isclose(
                floor_return,
                best_available_floor,
                atol=1e-10,
                rtol=0,
            )
        )

        minimum_debit_bps = float(
            np.min(net_cost_bps[closest_floor_mask])
        )

        selection_mask = (
            closest_floor_mask
            & np.isclose(
                net_cost_bps,
                minimum_debit_bps,
                atol=0.01,
                rtol=0,
            )
        )

        selection_mode = "closest_floor"
        outside_tolerance = True
        exact_floor_match = False

        fallback_reason = (
            "No available collar met the requested max-loss target. "
            "Returned the closest achievable floor using the "
            f"lowest-debit structure at {minimum_debit_bps:.1f} bps."
        )

    if not np.any(selection_mask):
        return None

    # --------------------------------------------------------
    # Rank inside constrained pool
    # --------------------------------------------------------

    score = (
        cap_return * 1_000_000
        - bid_ask_drag_bps * 10
        + liquidity
    )

    score = np.where(
        selection_mask,
        score,
        -np.inf,
    )

    best_flat_idx = int(np.argmax(score))
    best_put_idx, best_call_idx = np.unravel_index(
        best_flat_idx,
        score.shape,
    )

    best_put = valid_puts.iloc[best_put_idx]
    best_call = valid_calls.iloc[best_call_idx]

    # --------------------------------------------------------
    # Extract result
    # --------------------------------------------------------

    best_net_cost = float(
        net_cost[best_put_idx, best_call_idx]
    )
    best_net_cost_bps = float(
        net_cost_bps[best_put_idx, best_call_idx]
    )

    best_invested_amount = float(
        invested_amount[best_put_idx, best_call_idx]
    )

    best_floor_value = float(
        floor_value[best_put_idx, best_call_idx]
    )
    best_cap_value = float(
        cap_value[best_put_idx, best_call_idx]
    )

    best_floor_return = float(
        floor_return[best_put_idx, best_call_idx]
    )
    best_cap_return = float(
        cap_return[best_put_idx, best_call_idx]
    )

    best_execution_cost = float(
        estimated_execution_cost[best_put_idx, best_call_idx]
    )
    best_execution_cost_bps = float(
        estimated_execution_cost_bps[best_put_idx, best_call_idx]
    )
    best_execution_investment = float(
        estimated_execution_investment[best_put_idx, best_call_idx]
    )

    best_execution_floor_return = float(
        execution_floor_return[best_put_idx, best_call_idx]
    )
    best_execution_cap_return = float(
        execution_cap_return[best_put_idx, best_call_idx]
    )

    best_bid_ask_drag_bps = float(
        bid_ask_drag_bps[best_put_idx, best_call_idx]
    )
    best_total_volume = float(
        total_volume[best_put_idx, best_call_idx]
    )
    best_total_oi = float(
        total_oi[best_put_idx, best_call_idx]
    )
    best_liquidity = float(
        liquidity[best_put_idx, best_call_idx]
    )

    best_near_zero = bool(
        0 <= best_net_cost_bps <= max_near_zero_bps
    )

    if best_near_zero:
        cost_display_label = "approximately $0"
    elif best_net_cost > 0:
        cost_display_label = "debit"
    else:
        cost_display_label = "credit"

    return make_json_safe({
        "product_name": "Defined Floor",
        "strategy": "classic_collar",
        "structure": "collar",
        "backend_structure": (
            "long_underlying_plus_long_put_short_call"
        ),

        "expirDate": g["expirDate"].iloc[0],
        "dte": dte,
        "spot": spot,

        # Keep notional for backward compatibility.
        "notional": underlying_notional,
        "underlying_notional": underlying_notional,

        # Actual initial investment used as the return denominator.
        "invested_amount": best_invested_amount,
        "investment_required_today": best_invested_amount,

        "assumed_dividend_yield": assumed_dividend_yield,
        "expected_dividend_dollars": expected_dividend_dollars,
        "expected_dividend_per_share": expected_dividend_per_share,

        "target_max_loss_pct": max_loss_pct,
        "target_floor_return": target_floor_return,
        "approximate_required_put": approximate_required_put,

        "long_put_strike": float(best_put["strike"]),
        "short_put_strike": None,
        "call_strike": float(best_call["strike"]),

        "put_cost_dollars": float(best_put["putMid"]) * MULT,
        "call_credit_dollars": float(best_call["callMid"]) * MULT,

        "net_cost_dollars": best_net_cost,
        "net_cost_bps": best_net_cost_bps,

        "estimated_execution_cost_dollars": best_execution_cost,
        "estimated_execution_cost_bps": best_execution_cost_bps,
        "estimated_execution_investment": best_execution_investment,

        "near_zero_cost_ok": best_near_zero,
        "cost_limit_met": bool(
            best_net_cost_bps <= max_near_zero_bps
        ),
        "outside_tolerance": outside_tolerance,
        "exact_floor_match": exact_floor_match,
        "requested_floor_met": bool(
            best_floor_return >= target_floor_return
        ),

        "selection_mode": selection_mode,
        "fallback_reason": fallback_reason,
        "minimum_available_debit_bps": minimum_debit_bps,
        "best_available_floor": best_available_floor,
        "max_near_zero_bps": max_near_zero_bps,

        # Terminal dollar outcomes.
        "floor_value": best_floor_value,
        "cap_value": best_cap_value,

        # Returns based on actual initial invested amount.
        "floor_return": best_floor_return,
        "cap_return": best_cap_return,

        # Conservative returns using ask/bid execution cost.
        "execution_floor_return": best_execution_floor_return,
        "execution_cap_return": best_execution_cap_return,

        "max_loss_dollars": (
            best_invested_amount - best_floor_value
        ),
        "max_gain_dollars": (
            best_cap_value - best_invested_amount
        ),

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

            "investment_required_today": best_invested_amount,

            "estimated_max_loss_pct": round_pct(
                best_floor_return
            ),
            "estimated_cap_pct": round_pct(
                best_cap_return
            ),

            "estimated_option_cost_dollars": best_net_cost,
            "estimated_option_cost_bps": best_net_cost_bps,
            "estimated_option_cost_label": cost_display_label,

            "estimated_execution_cost_dollars": best_execution_cost,
            "estimated_execution_cost_bps": best_execution_cost_bps,

            "estimated_execution_floor_pct": round_pct(
                best_execution_floor_return
            ),
            "estimated_execution_cap_pct": round_pct(
                best_execution_cap_return
            ),

            "estimated_dividends_dollars": expected_dividend_dollars,

            "cost_limit_met": bool(
                best_net_cost_bps <= max_near_zero_bps
            ),
            "selection_mode": selection_mode,
            "fallback_reason": fallback_reason,

            "explanation": (
                "Return estimates are calculated using the total amount "
                "required to establish the position, including the net "
                "option debit. Estimated dividends are included in terminal "
                "outcomes."
            ),
        },
    })
    
# def build_zero_cost_dividend_floor_collar(
#     expiry_chain,
#     max_loss_pct=0.005,
#     assumed_dividend_yield=0.01,
#     max_near_zero_bps=50,
# ):
#     """
#     Product: Defined Floor

#     Corrected logic:
#     1. Search put/call combinations together.
#     2. Calculate the final floor after dividends and net option cost.
#     3. Reject any collar that misses the requested max loss.
#     4. Prefer near-zero option cost.
#     5. Within valid/near-zero structures, choose the highest cap.
#     6. Use bid/ask drag and liquidity as tie breakers.

#     This fixes the issue where max_loss=0.0 could still return a negative floor.
#     """

#     g = expiry_chain.copy()

#     if g.empty:
#         return None

#     spot = float(g["spot"].median())
#     dte = float(g["dte"].median())
#     notional = spot * MULT

#     expected_dividend_dollars = (
#         notional * assumed_dividend_yield * (dte / 365.25)
#     )
#     expected_dividend_per_share = expected_dividend_dollars / MULT

#     target_floor_return = -max_loss_pct

#     valid_puts = g[
#         (g["strike"] < spot)
#         & (g["putMid"] > 0)
#     ].copy()

#     valid_calls = g[
#         (g["strike"] > spot)
#         & (g["callMid"] > 0)
#     ].copy()

#     if valid_puts.empty or valid_calls.empty:
#         return None

#     rows = []

#     for _, put in valid_puts.iterrows():
#         put_strike = float(put["strike"])
#         put_cost = float(put["putMid"]) * MULT

#         for _, call in valid_calls.iterrows():
#             call_strike = float(call["strike"])
#             call_credit = float(call["callMid"]) * MULT

#             net_cost = put_cost - call_credit
#             net_cost_bps = net_cost / notional * 10000

#             floor_value = (
#                 put_strike * MULT
#                 + expected_dividend_dollars
#                 - net_cost
#             )

#             cap_value = (
#                 call_strike * MULT
#                 + expected_dividend_dollars
#                 - net_cost
#             )

#             floor_return = floor_value / notional - 1
#             cap_return = cap_value / notional - 1

#             # Critical fix:
#             # Do not return a collar that misses the requested floor.
#             # If max_loss_pct = 0.0, floor_return must be >= 0.0.
#             if floor_return < target_floor_return:
#                 continue

#             worst_net_cost = (
#                 float(put.get("putAskPrice", put["putMid"]))
#                 - float(call.get("callBidPrice", call["callMid"]))
#             ) * MULT

#             bid_ask_drag_dollars = worst_net_cost - net_cost
#             bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

#             liq_score, total_volume, total_oi = liquidity_score(put, call)

#             rows.append({
#                 "put": put,
#                 "call": call,
#                 "put_strike": put_strike,
#                 "call_strike": call_strike,
#                 "put_cost": put_cost,
#                 "call_credit": call_credit,
#                 "net_cost": net_cost,
#                 "net_cost_bps": net_cost_bps,
#                 "abs_net_cost_bps": abs(net_cost_bps),
#                 "floor_value": floor_value,
#                 "cap_value": cap_value,
#                 "floor_return": floor_return,
#                 "cap_return": cap_return,
#                 "max_loss_dollars": notional - floor_value,
#                 "max_gain_dollars": cap_value - notional,
#                 "bid_ask_drag_bps": bid_ask_drag_bps,
#                 "total_volume": total_volume,
#                 "total_oi": total_oi,
#                 "liquidity_score": liq_score,
#             })

#     if not rows:
#         return None

#     candidates = pd.DataFrame(rows)

#     candidates["near_zero"] = (
#         candidates["abs_net_cost_bps"] <= max_near_zero_bps
#     )

#     # Preferred pool: collars that satisfy the floor and are near zero cost.
#     near_zero_candidates = candidates[candidates["near_zero"]].copy()

#     if not near_zero_candidates.empty:
#         pool = near_zero_candidates
#         outside_tolerance = False
#     else:
#         # Still return a valid floor if no near-zero collar exists.
#         # But do not ever return one that misses the floor.
#         pool = candidates
#         outside_tolerance = True

#     # Ranking:
#     # 1. Highest cap
#     # 2. Cost closest to zero
#     # 3. Lower bid/ask drag
#     # 4. Better liquidity
#     best = pool.sort_values(
#         [
#             "cap_return",
#             "abs_net_cost_bps",
#             "bid_ask_drag_bps",
#             "total_oi",
#         ],
#         ascending=[False, True, True, False],
#     ).iloc[0]

#     put = best["put"]
#     call = best["call"]

#     net_cost = float(best["net_cost"])
#     net_cost_bps = float(best["net_cost_bps"])

#     if abs(net_cost_bps) <= max_near_zero_bps:
#         cost_display_label = "approximately $0"
#     elif net_cost > 0:
#         cost_display_label = "small debit"
#     else:
#         cost_display_label = "small credit"

#     return {
#         "product_name": "Defined Floor",
#         "strategy": "classic_collar",
#         "structure": "collar",
#         "backend_structure": "long_underlying_plus_long_put_short_call",
#         "expirDate": g["expirDate"].iloc[0],
#         "dte": dte,
#         "spot": spot,
#         "notional": notional,

#         "assumed_dividend_yield": assumed_dividend_yield,
#         "expected_dividend_dollars": expected_dividend_dollars,
#         "expected_dividend_per_share": expected_dividend_per_share,

#         "target_max_loss_pct": max_loss_pct,
#         "target_floor_return": target_floor_return,

#         "long_put_strike": float(best["put_strike"]),
#         "short_put_strike": None,
#         "call_strike": float(best["call_strike"]),

#         "put_cost_dollars": float(best["put_cost"]),
#         "call_credit_dollars": float(best["call_credit"]),
#         "net_cost_dollars": net_cost,
#         "net_cost_bps": net_cost_bps,

#         "near_zero_cost_ok": bool(best["near_zero"]),
#         "outside_tolerance": outside_tolerance,
#         "max_near_zero_bps": max_near_zero_bps,

#         "floor_value": float(best["floor_value"]),
#         "cap_value": float(best["cap_value"]),
#         "floor_return": float(best["floor_return"]),
#         "cap_return": float(best["cap_return"]),
#         "max_loss_dollars": float(best["max_loss_dollars"]),
#         "max_gain_dollars": float(best["max_gain_dollars"]),

#         "buffer_width_points": None,
#         "buffer_pct": None,
#         "protected_zone_value": None,
#         "protected_zone_return": None,

#         "bid_ask_drag_bps": float(best["bid_ask_drag_bps"]),
#         "total_volume": float(best["total_volume"]),
#         "total_oi": float(best["total_oi"]),
#         "liquidity_score": float(best["liquidity_score"]),

#         "display": {
#             "title": "Defined Floor",
#             "subtitle": "Hard-loss target with capped upside",
#             "estimated_max_loss_pct": round_pct(float(best["floor_return"])),
#             "estimated_cap_pct": round_pct(float(best["cap_return"])),
#             "estimated_option_cost_dollars": net_cost,
#             "estimated_option_cost_label": cost_display_label,
#             "estimated_dividends_dollars": expected_dividend_dollars,
#             "explanation": (
#                 "Designed to target a defined floor over the selected outcome period. "
#                 "Upside is capped in exchange for downside protection."
#             ),
#         },
#     }

# ============================================================
# 6. BUFFER PRODUCT
# ============================================================

# def build_zero_cost_target_cap_buffer(
#     expiry_chain,
#     target_gain_pct=0.08,
#     assumed_dividend_yield=0.01,
#     max_buffer_pct=0.20,
#     max_near_zero_bps=25,
# ):
#     """
#     Product: Buffered Growth

#     POC logic:
#     Always try to return a buffer.

#     1. Look at calls near the user's target gain.
#     2. Look at near-ATM long puts.
#     3. Look at lower short puts.
#     4. Do NOT filter out credits or debits.
#     5. Rank by:
#         - cap close to target
#         - option cost close to zero
#         - larger buffer
#         - lower bid/ask drag
#         - better open interest
#     6. Return the best available buffer.

#     Important:
#     This may return a small credit or debit.
#     The frontend should display near-zero option costs as "approximately $0".
#     """

#     g = expiry_chain.copy()

#     if g.empty:
#         return None

#     spot = float(g["spot"].median())
#     dte = float(g["dte"].median())
#     notional = spot * MULT

#     expected_dividend_dollars = (
#         notional * assumed_dividend_yield * (dte / 365.25)
#     )
#     expected_dividend_per_share = expected_dividend_dollars / MULT

#     target_cap_value = notional * (1 + target_gain_pct)

#     required_call_strike = (
#         target_cap_value - expected_dividend_dollars
#     ) / MULT

#     # Loosened call filter.
#     valid_calls = g[
#         (g["strike"] > spot)
#         & (g["callMid"] > 0)
#     ].copy()

#     if valid_calls.empty:
#         return None

#     valid_calls["target_distance"] = (
#         valid_calls["strike"] - required_call_strike
#     ).abs()

#     # Look at more calls near the target, not just one.
#     nearby_calls = valid_calls.sort_values(
#         ["target_distance", "strike"],
#         ascending=[True, True],
#     ).head(15)

#     # Loosened long put filter.
#     candidate_long_puts = g[
#         (g["strike"] <= spot)
#         & (g["putMid"] > 0)
#     ].copy()

#     if candidate_long_puts.empty:
#         return None

#     candidate_long_puts["atm_distance"] = (
#         candidate_long_puts["strike"] - spot
#     ).abs()

#     # Look at several near-ATM long puts.
#     nearby_long_puts = candidate_long_puts.sort_values(
#         ["atm_distance", "strike"],
#         ascending=[True, False],
#     ).head(10)

#     rows = []

#     for _, call in nearby_calls.iterrows():
#         call_strike = float(call["strike"])
#         call_credit = float(call["callMid"]) * MULT

#         for _, long_put in nearby_long_puts.iterrows():
#             long_put_strike = float(long_put["strike"])
#             long_put_cost = float(long_put["putMid"]) * MULT

#             # Loosened short put filter.
#             short_puts = g[
#                 (g["strike"] < long_put_strike)
#                 & (g["putMid"] > 0)
#             ].copy()

#             if short_puts.empty:
#                 continue

#             for _, short_put in short_puts.iterrows():
#                 short_put_strike = float(short_put["strike"])
#                 short_put_credit = float(short_put["putMid"]) * MULT

#                 put_spread_cost = long_put_cost - short_put_credit
#                 net_cost = put_spread_cost - call_credit
#                 net_cost_bps = net_cost / notional * 10000

#                 buffer_width_points = long_put_strike - short_put_strike
#                 buffer_pct = buffer_width_points / spot

#                 # Keep the buffer in a reasonable demo range.
#                 if buffer_pct <= 0 or buffer_pct > max_buffer_pct:
#                     continue

#                 max_buffer_value = buffer_width_points * MULT

#                 protected_zone_value = (
#                     short_put_strike * MULT
#                     + max_buffer_value
#                     + expected_dividend_dollars
#                     - net_cost
#                 )

#                 cap_value = (
#                     call_strike * MULT
#                     + expected_dividend_dollars
#                     - net_cost
#                 )

#                 protected_zone_return = protected_zone_value / notional - 1
#                 cap_return = cap_value / notional - 1

#                 # Use safe bid/ask drag calculation.
#                 long_put_ask = float(long_put.get("putAskPrice", long_put["putMid"]))
#                 short_put_bid = float(short_put.get("putBidPrice", short_put["putMid"]))
#                 call_bid = float(call.get("callBidPrice", call["callMid"]))

#                 if pd.isna(long_put_ask) or long_put_ask <= 0:
#                     long_put_ask = float(long_put["putMid"])

#                 if pd.isna(short_put_bid) or short_put_bid < 0:
#                     short_put_bid = float(short_put["putMid"])

#                 if pd.isna(call_bid) or call_bid < 0:
#                     call_bid = float(call["callMid"])

#                 worst_net_cost = (
#                     long_put_ask
#                     - short_put_bid
#                     - call_bid
#                 ) * MULT

#                 bid_ask_drag_dollars = worst_net_cost - net_cost
#                 bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

#                 liq_score, total_volume, total_oi = liquidity_score(
#                     long_put,
#                     short_put,
#                     call,
#                 )

#                 rows.append({
#                     "call_strike": call_strike,
#                     "call_credit": call_credit,
#                     "long_put_strike": long_put_strike,
#                     "long_put_cost": long_put_cost,
#                     "short_put_strike": short_put_strike,
#                     "short_put_credit": short_put_credit,
#                     "put_spread_cost": put_spread_cost,
#                     "net_cost": net_cost,
#                     "net_cost_bps": net_cost_bps,
#                     "abs_net_cost": abs(net_cost),
#                     "abs_net_cost_bps": abs(net_cost_bps),
#                     "buffer_width_points": buffer_width_points,
#                     "buffer_pct": buffer_pct,
#                     "protected_zone_value": protected_zone_value,
#                     "protected_zone_return": protected_zone_return,
#                     "cap_value": cap_value,
#                     "cap_return": cap_return,
#                     "cap_error": abs(cap_return - target_gain_pct),
#                     "bid_ask_drag_bps": bid_ask_drag_bps,
#                     "total_volume": total_volume,
#                     "total_oi": total_oi,
#                     "liquidity_score": liq_score,
#                 })

#     if not rows:
#         return None

#     candidates = pd.DataFrame(rows)

#     candidates["near_zero"] = (
#         candidates["abs_net_cost_bps"] <= max_near_zero_bps
#     )

#     # Prefer near-zero structures, but do not require them.
#     # This forces the API to return a buffer instead of null.
#     best = candidates.sort_values(
#         [
#             "near_zero",
#             "cap_error",
#             "abs_net_cost_bps",
#             "buffer_pct",
#             "bid_ask_drag_bps",
#             "total_oi",
#         ],
#         ascending=[False, True, True, False, True, False],
#     ).iloc[0]

#     net_cost = float(best["net_cost"])
#     net_cost_bps = float(best["net_cost_bps"])
#     abs_net_cost_bps = abs(net_cost_bps)

#     if abs_net_cost_bps <= max_near_zero_bps:
#         cost_display_label = "approximately $0"
#     elif net_cost > 0:
#         cost_display_label = "small debit"
#     else:
#         cost_display_label = "small credit"

#     return {
#         "product_name": "Buffered Growth",
#         "strategy": "buffered_collar_first_loss",
#         "structure": "buffer",
#         "backend_structure": "long_underlying_plus_long_put_short_put_short_call",
#         "expirDate": g["expirDate"].iloc[0],
#         "dte": dte,
#         "spot": spot,
#         "notional": notional,

#         "assumed_dividend_yield": assumed_dividend_yield,
#         "expected_dividend_dollars": expected_dividend_dollars,
#         "expected_dividend_per_share": expected_dividend_per_share,

#         "target_gain_pct": target_gain_pct,
#         "required_call_strike": required_call_strike,

#         "long_put_strike": float(best["long_put_strike"]),
#         "short_put_strike": float(best["short_put_strike"]),
#         "call_strike": float(best["call_strike"]),

#         "long_put_cost_dollars": float(best["long_put_cost"]),
#         "short_put_credit_dollars": float(best["short_put_credit"]),
#         "put_spread_cost_dollars": float(best["put_spread_cost"]),
#         "call_credit_dollars": float(best["call_credit"]),
#         "net_cost_dollars": net_cost,
#         "net_cost_bps": net_cost_bps,

#         "near_zero_cost_ok": bool(best["near_zero"]),
#         "max_near_zero_bps": max_near_zero_bps,

#         "buffer_width_points": float(best["buffer_width_points"]),
#         "buffer_pct": float(best["buffer_pct"]),

#         "protected_zone_value": float(best["protected_zone_value"]),
#         "protected_zone_return": float(best["protected_zone_return"]),
#         "cap_value": float(best["cap_value"]),
#         "cap_return": float(best["cap_return"]),

#         "floor_value": None,
#         "floor_return": None,
#         "max_loss_dollars": None,
#         "max_gain_dollars": float(best["cap_value"]) - notional,

#         "bid_ask_drag_bps": float(best["bid_ask_drag_bps"]),
#         "total_volume": float(best["total_volume"]),
#         "total_oi": float(best["total_oi"]),
#         "liquidity_score": float(best["liquidity_score"]),

#         "display": {
#             "title": "Buffered Growth",
#             "subtitle": "First-loss protection with more upside potential",
#             "estimated_buffer_pct": round_pct(float(best["buffer_pct"])),
#             "estimated_cap_pct": round_pct(float(best["cap_return"])),
#             "estimated_option_cost_dollars": net_cost,
#             "estimated_option_cost_label": cost_display_label,
#             "estimated_dividends_dollars": expected_dividend_dollars,
#             "explanation": (
#                 "Designed to absorb a defined range of losses first. "
#                 "Losses may continue if the market falls beyond the buffer."
#             ),
#         },
#     }
# def build_zero_cost_target_cap_buffer(
#     expiry_chain,
#     target_gain_pct=0.08,
#     assumed_dividend_yield=0.01,
#     max_buffer_pct=0.20,
#     max_near_zero_bps=25,
# ):
#     """
#     Product: Buffered Growth

#     Correct logic:
#     1. Long put is dividend-adjusted, not ATM.
#     2. Short put is set below long put by the target buffer.
#     3. Call is chosen as the highest OTM call that finances the put spread.
#     4. Rank by highest cap, then near-zero cost, then liquidity.
#     """

#     g = expiry_chain.copy()
#     if g.empty:
#         return None

#     spot = float(g["spot"].median())
#     dte = float(g["dte"].median())
#     notional = spot * MULT

#     expected_dividend_dollars = notional * assumed_dividend_yield * (dte / 365.25)
#     expected_dividend_per_share = expected_dividend_dollars / MULT

#     # Dividend-adjusted long put target
#     long_put_target = spot - expected_dividend_per_share

#     # Short put target based on desired buffer
#     short_put_target = long_put_target - (spot * max_buffer_pct)

#     valid_calls = g[(g["strike"] > spot) & (g["callMid"] > 0)].copy()
#     valid_puts = g[(g["strike"] < spot) & (g["putMid"] > 0)].copy()

#     if valid_calls.empty or valid_puts.empty:
#         return None

#     # Choose candidate long puts near dividend-adjusted floor
#     valid_puts["long_put_distance"] = (valid_puts["strike"] - long_put_target).abs()
#     nearby_long_puts = valid_puts.sort_values(
#         ["long_put_distance", "strike"],
#         ascending=[True, False],
#     ).head(5)

#     rows = []

#     for _, long_put in nearby_long_puts.iterrows():
#         long_put_strike = float(long_put["strike"])
#         long_put_cost = float(long_put["putMid"]) * MULT

#         # Short put should target the buffer below the long put
#         candidate_short_puts = valid_puts[
#             valid_puts["strike"] < long_put_strike
#         ].copy()

#         if candidate_short_puts.empty:
#             continue

#         candidate_short_puts["short_put_distance"] = (
#             candidate_short_puts["strike"] - short_put_target
#         ).abs()

#         nearby_short_puts = candidate_short_puts.sort_values(
#             ["short_put_distance", "strike"],
#             ascending=[True, False],
#         ).head(5)

#         for _, short_put in nearby_short_puts.iterrows():
#             short_put_strike = float(short_put["strike"])
#             short_put_credit = float(short_put["putMid"]) * MULT

#             buffer_width_points = long_put_strike - short_put_strike
#             buffer_pct = buffer_width_points / spot

#             if buffer_pct <= 0 or buffer_pct > max_buffer_pct * 1.05:
#                 continue

#             put_spread_cost = long_put_cost - short_put_credit

#             for _, call in valid_calls.iterrows():
#                 call_strike = float(call["strike"])
#                 call_credit = float(call["callMid"]) * MULT

#                 net_cost = put_spread_cost - call_credit
#                 net_cost_bps = net_cost / notional * 10000

#                 # Do not overfinance by selling a too-low call.
#                 # Keep near-zero structures only.
#                 if abs(net_cost_bps) > max_near_zero_bps:
#                     continue

#                 cap_value = (
#                     call_strike * MULT
#                     + expected_dividend_dollars
#                     - net_cost
#                 )
#                 cap_return = cap_value / notional - 1

#                 protected_zone_value = (
#                     short_put_strike * MULT
#                     + buffer_width_points * MULT
#                     + expected_dividend_dollars
#                     - net_cost
#                 )
#                 protected_zone_return = protected_zone_value / notional - 1

#                 long_put_ask = float(long_put.get("putAskPrice", long_put["putMid"]))
#                 short_put_bid = float(short_put.get("putBidPrice", short_put["putMid"]))
#                 call_bid = float(call.get("callBidPrice", call["callMid"]))

#                 worst_net_cost = (long_put_ask - short_put_bid - call_bid) * MULT
#                 bid_ask_drag_dollars = worst_net_cost - net_cost
#                 bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

#                 liq_score, total_volume, total_oi = liquidity_score(
#                     long_put,
#                     short_put,
#                     call,
#                 )

#                 rows.append({
#                     "call_strike": call_strike,
#                     "call_credit": call_credit,
#                     "long_put_strike": long_put_strike,
#                     "long_put_cost": long_put_cost,
#                     "short_put_strike": short_put_strike,
#                     "short_put_credit": short_put_credit,
#                     "put_spread_cost": put_spread_cost,
#                     "net_cost": net_cost,
#                     "net_cost_bps": net_cost_bps,
#                     "abs_net_cost_bps": abs(net_cost_bps),
#                     "buffer_width_points": buffer_width_points,
#                     "buffer_pct": buffer_pct,
#                     "protected_zone_value": protected_zone_value,
#                     "protected_zone_return": protected_zone_return,
#                     "cap_value": cap_value,
#                     "cap_return": cap_return,
#                     "bid_ask_drag_bps": bid_ask_drag_bps,
#                     "total_volume": total_volume,
#                     "total_oi": total_oi,
#                     "liquidity_score": liq_score,
#                 })

#     if not rows:
#         return None

#     candidates = pd.DataFrame(rows)

#     # Key fix: maximize cap first.
#     best = candidates.sort_values(
#         [
#             "cap_return",
#             "abs_net_cost_bps",
#             "bid_ask_drag_bps",
#             "total_oi",
#         ],
#         ascending=[False, True, True, False],
#     ).iloc[0]

#     net_cost = float(best["net_cost"])
#     net_cost_bps = float(best["net_cost_bps"])

#     if abs(net_cost_bps) <= max_near_zero_bps:
#         cost_display_label = "approximately $0"
#     elif net_cost > 0:
#         cost_display_label = "small debit"
#     else:
#         cost_display_label = "small credit"

#     return {
#         "product_name": "Buffered Growth",
#         "strategy": "buffered_collar_first_loss",
#         "structure": "buffer",
#         "backend_structure": "long_underlying_plus_long_put_short_put_short_call",
#         "expirDate": g["expirDate"].iloc[0],
#         "dte": dte,
#         "spot": spot,
#         "notional": notional,

#         "assumed_dividend_yield": assumed_dividend_yield,
#         "expected_dividend_dollars": expected_dividend_dollars,
#         "expected_dividend_per_share": expected_dividend_per_share,

#         "target_gain_pct": target_gain_pct,
#         "long_put_target": long_put_target,
#         "short_put_target": short_put_target,

#         "long_put_strike": float(best["long_put_strike"]),
#         "short_put_strike": float(best["short_put_strike"]),
#         "call_strike": float(best["call_strike"]),

#         "long_put_cost_dollars": float(best["long_put_cost"]),
#         "short_put_credit_dollars": float(best["short_put_credit"]),
#         "put_spread_cost_dollars": float(best["put_spread_cost"]),
#         "call_credit_dollars": float(best["call_credit"]),
#         "net_cost_dollars": net_cost,
#         "net_cost_bps": net_cost_bps,

#         "near_zero_cost_ok": abs(net_cost_bps) <= max_near_zero_bps,
#         "max_near_zero_bps": max_near_zero_bps,

#         "buffer_width_points": float(best["buffer_width_points"]),
#         "buffer_pct": float(best["buffer_pct"]),

#         "protected_zone_value": float(best["protected_zone_value"]),
#         "protected_zone_return": float(best["protected_zone_return"]),
#         "cap_value": float(best["cap_value"]),
#         "cap_return": float(best["cap_return"]),

#         "floor_value": None,
#         "floor_return": None,
#         "max_loss_dollars": None,
#         "max_gain_dollars": float(best["cap_value"]) - notional,

#         "bid_ask_drag_bps": float(best["bid_ask_drag_bps"]),
#         "total_volume": float(best["total_volume"]),
#         "total_oi": float(best["total_oi"]),
#         "liquidity_score": float(best["liquidity_score"]),

#         "display": {
#             "title": "Buffered Growth",
#             "subtitle": "First-loss protection with more upside potential",
#             "estimated_buffer_pct": round_pct(float(best["buffer_pct"])),
#             "estimated_cap_pct": round_pct(float(best["cap_return"])),
#             "estimated_option_cost_dollars": net_cost,
#             "estimated_option_cost_label": cost_display_label,
#             "estimated_dividends_dollars": expected_dividend_dollars,
#             "explanation": (
#                 "Designed to absorb a defined range of losses first. "
#                 "Losses may continue if the market falls beyond the buffer."
#             ),
#         },
#     }

def build_zero_cost_target_cap_buffer(
    expiry_chain,
    target_gain_pct=0.08,
    assumed_dividend_yield=0.01,
    target_buffer_pct=0.10,
    max_near_zero_bps=25,
    buffer_tolerance_pct=0.015,
):
    """
    Product: Buffered Growth

    Correct logic:
    1. Pick long put near dividend-adjusted spot.
    2. Pick short put near long_put_strike - target_buffer_pct * spot.
    3. Enforce the requested buffer as the product definition.
    4. Then find the short call that finances that buffer closest to zero cost.
    """

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
    ].copy()
    
    valid_calls = g[
        (g["strike"] > spot)
        & (g["callBidPrice"] > 0)
        & (g["callAskPrice"] > 0)
    ].copy()

    if valid_puts.empty or valid_calls.empty:
        return None

    # 1. Long put near spot
    valid_puts["long_put_distance"] = (
        valid_puts["strike"] - long_put_target
    ).abs()

    long_put = valid_puts.sort_values(
        ["long_put_distance", "strike"],
        ascending=[True, False],
    ).iloc[0]

    long_put_strike = float(long_put["strike"])
    long_put_cost = float(long_put["putMid"]) * MULT

    # 2. Short put near requested buffer
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

    # HARD FILTER: do not allow a 5% buffer when we requested 10%
    buffer_pool = short_put_candidates[
        short_put_candidates["buffer_error"] <= buffer_tolerance_pct
    ].copy()

    if buffer_pool.empty:
        # fallback: closest actual buffer, but mark it as outside tolerance
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

        # 3. Now find the call that finances this buffer
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

    # Correct ranking:
    # 1. Buffer closest to requested target
    # 2. Cost closest to zero
    # 3. Highest cap
    # 4. Better execution / liquidity
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
# 7. PRODUCT RECOMMENDATION PAYLOAD
# ============================================================

# def build_defined_outcome_recommendations(
#     df,
#     ticker="XSP",
#     horizon=365,
#     max_loss_pct=0.005,
#     target_gain_pct=0.08,
#     assumed_dividend_yield=0.01,
# ):
def build_defined_outcome_recommendations(
    df,
    ticker="XSP",
    horizon=365,
    max_loss_pct=0.005,
    target_gain_pct=0.08,
    assumed_dividend_yield=0.01,
    target_buffer_pct=0.10,
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
    target_buffer_pct=target_buffer_pct,  # ADD THIS LINE 
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

    # buffer = build_zero_cost_target_cap_buffer(
    #     expiry_chain,
    #     target_gain_pct=target_gain_pct,
    #     assumed_dividend_yield=assumed_dividend_yield,
    #     max_buffer_pct=max_buffer_pct,
    # )
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

def build_married_put(
    expiry_chain,
    max_loss_pct=0.10,
    assumed_dividend_yield=0.0,
):
    """
    Product: Insured Upside
    Long underlying + long put.

    Logic:
    - User chooses a protection level, e.g. 10%.
    - We find the dividend-adjusted put strike near 10% OTM.
    - Put premium is shown separately as insurance cost.
    - Actual worst case includes the insurance cost.
    """

    g = expiry_chain.copy()
    if g.empty:
        return None

    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    notional = spot * MULT

    expected_dividend_dollars = notional * assumed_dividend_yield * (dte / 365.25)
    expected_dividend_per_share = expected_dividend_dollars / MULT

    # Target the actual insurance strike, not the premium-adjusted floor.
    target_put_strike = spot * (1 - max_loss_pct) - expected_dividend_per_share

    puts = g[
        (g["strike"] < spot)
        & (g["putMid"] > 0)
    ].copy()

    if puts.empty:
        return None

    puts["strike_distance"] = (puts["strike"] - target_put_strike).abs()
    puts["put_cost_dollars"] = puts["putMid"] * MULT

    # For execution realism, track worst case at ask too.
    if "putAskPrice" in puts.columns:
        puts["put_ask_dollars"] = puts["putAskPrice"].fillna(puts["putMid"]) * MULT
    else:
        puts["put_ask_dollars"] = puts["put_cost_dollars"]

    if "putBidPrice" in puts.columns:
        puts["put_bid_dollars"] = puts["putBidPrice"].fillna(puts["putMid"]) * MULT
    else:
        puts["put_bid_dollars"] = puts["put_cost_dollars"]

    puts["bid_ask_drag_dollars"] = puts["put_ask_dollars"] - puts["put_cost_dollars"]
    puts["bid_ask_drag_bps"] = puts["bid_ask_drag_dollars"] / notional * 10000

    # Pick nearest strike to requested protection.
    # Tie-breakers: lower bid/ask drag, better OI, better volume.
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

    # Protection level is based on strike.
    protection_floor_value = put_strike * MULT + expected_dividend_dollars
    protection_floor_return = protection_floor_value / notional - 1

    # Actual worst case includes the insurance premium paid.
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
        "put_bid_per_share": float(best.get("putBidPrice", best["putMid"])),
        "put_ask_per_share": float(best.get("putAskPrice", best["putMid"])),

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

def build_covered_call(
    expiry_chain,
    target_income_pct=0.05,
    assumed_dividend_yield=0.0,
):
    """
    Product: Income
    Long underlying + short call.

    Finds the call strike that gets closest to the user's preferred income.
    Shows the tradeoff: known income, capped upside, downside still exposed.
    """

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
    calls["max_gain_dollars"] = calls["cap_value"] - notional

    # Downside is still stock downside, partially offset by call premium/dividends.
    calls["breakeven_value"] = notional - calls["total_income_dollars"]
    calls["breakeven_return"] = calls["breakeven_value"] / notional - 1

    # Pick income closest to target; tie-break by higher cap / better OI.
    best = calls.sort_values(
        ["income_error", "cap_return", "callOpenInterest"],
        ascending=[True, False, False],
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

        "floor_value": None,
        "floor_return": None,
        "cap_value": float(best["cap_value"]),
        "cap_return": float(best["cap_return"]),
        "max_loss_dollars": None,
        "max_gain_dollars": float(best["max_gain_dollars"]),
        "breakeven_value": float(best["breakeven_value"]),
        "breakeven_return": float(best["breakeven_return"]),

        "total_volume": total_volume,
        "total_oi": total_oi,
        "liquidity_score": liq_score,

        "display": {
            "title": "Income",
            "subtitle": "Generate income while keeping limited upside",
            "estimated_income_pct": round_pct(float(best["total_income_pct"])),
            "estimated_cap_pct": round_pct(float(best["cap_return"])),
            "estimated_max_loss_label": "Substantial downside remains",
            "estimated_option_income_dollars": float(best["call_credit_dollars"]),
            "estimated_dividends_dollars": expected_dividend_dollars,
            "explanation": (
                "Designed to generate income by selling upside above the call strike. "
                "Upside is capped and downside is not protected."
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

import numpy as np
import pandas as pd

def build_unlimited_upside_put_spread_ladder(
    expiry_chain,
    protection_levels=(0.05, 0.10, 0.15, 0.20),
    assumed_dividend_yield=0.01,
):
    g = expiry_chain.copy()
    if g.empty:
        return None

    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    notional = spot * MULT

    expected_dividend_dollars = notional * assumed_dividend_yield * (dte / 365.25)
    expected_dividend_per_share = expected_dividend_dollars / MULT

    valid_puts = g[
        (g["strike"] < spot)
        & (g["putBidPrice"] > 0)
        & (g["putAskPrice"] > 0)
        & (g["putMid"] > 0)
    ].copy()

    if valid_puts.empty:
        return None

    rows = []

    long_put_target = spot - expected_dividend_per_share

    valid_puts["long_put_distance"] = (
        valid_puts["strike"] - long_put_target
    ).abs()

    long_put = valid_puts.sort_values(
        ["long_put_distance", "strike"],
        ascending=[True, False],
    ).iloc[0]

    long_put_strike = float(long_put["strike"])
    long_put_cost = float(long_put["putMid"]) * MULT

    for protection_pct in protection_levels:
        short_put_target = long_put_strike - (spot * protection_pct)

        short_put_candidates = valid_puts[
            valid_puts["strike"] < long_put_strike
        ].copy()

        if short_put_candidates.empty:
            continue

        short_put_candidates["short_put_distance"] = (
            short_put_candidates["strike"] - short_put_target
        ).abs()

        short_put = short_put_candidates.sort_values(
            ["short_put_distance", "strike"],
            ascending=[True, False],
        ).iloc[0]

        short_put_strike = float(short_put["strike"])
        short_put_credit = float(short_put["putMid"]) * MULT

        put_spread_cost = long_put_cost - short_put_credit
        put_spread_cost_pct = put_spread_cost / notional

        actual_protection_width = long_put_strike - short_put_strike
        actual_protection_pct = actual_protection_width / spot

        net_cost_after_dividends = put_spread_cost - expected_dividend_dollars
        net_cost_after_dividends_pct = net_cost_after_dividends / notional

        protected_start_return = long_put_strike / spot - 1
        protected_end_return = short_put_strike / spot - 1

        long_put_ask = float(long_put.get("putAskPrice", long_put["putMid"]))
        short_put_bid = float(short_put.get("putBidPrice", short_put["putMid"]))

        executable_cost = (long_put_ask - short_put_bid) * MULT
        bid_ask_drag_bps = (executable_cost - put_spread_cost) / notional * 10000

        liq_score, total_volume, total_oi = liquidity_score(long_put, short_put)

        rows.append({
            "product_name": "Unlimited Upside Buffer",
            "strategy": "long_underlying_plus_put_spread",
            "structure": "put_spread_unlimited_upside",
            "backend_structure": "long_underlying_plus_long_put_short_put",

            "expirDate": g["expirDate"].iloc[0],
            "dte": dte,
            "spot": spot,
            "notional": notional,

            "assumed_dividend_yield": assumed_dividend_yield,
            "expected_dividend_dollars": expected_dividend_dollars,
            "expected_dividend_per_share": expected_dividend_per_share,

            "target_protection_pct": protection_pct,
            "actual_protection_pct": actual_protection_pct,

            "long_put_target": long_put_target,
            "short_put_target": short_put_target,

            "long_put_strike": long_put_strike,
            "short_put_strike": short_put_strike,
            "call_strike": None,

            "long_put_cost_dollars": long_put_cost,
            "short_put_credit_dollars": short_put_credit,
            "put_spread_cost_dollars": put_spread_cost,
            "put_spread_cost_pct": put_spread_cost_pct,

            "expected_dividends_dollars": expected_dividend_dollars,
            "net_cost_after_dividends_dollars": net_cost_after_dividends,
            "net_cost_after_dividends_pct": net_cost_after_dividends_pct,

            "protected_start_return": protected_start_return,
            "protected_end_return": protected_end_return,

            "cap_value": None,
            "cap_return": None,
            "max_gain_dollars": None,
            "max_gain_label": "Unlimited",

            "bid_ask_drag_bps": bid_ask_drag_bps,
            "total_volume": total_volume,
            "total_oi": total_oi,
            "liquidity_score": liq_score,

            "display": {
                "title": f"{round(protection_pct * 100)}% Buffer + Unlimited Upside",
                "subtitle": "First-loss protection without a cap",
                "estimated_buffer_pct": round_pct(actual_protection_pct),
                "estimated_max_gain_label": "Unlimited",
                "put_spread_cost_pct": round_pct(put_spread_cost_pct),
                "estimated_option_cost_dollars": put_spread_cost,
                "estimated_dividends_dollars": expected_dividend_dollars,
                "net_cost_after_dividends_pct": round_pct(net_cost_after_dividends_pct),
                "protected_start_pct": round_pct(protected_start_return),
                "protected_end_pct": round_pct(protected_end_return),
                "explanation": (
                    "Designed to absorb the first part of market losses using a put spread, "
                    "while keeping upside uncapped. The put spread requires an upfront debit."
                ),
            },
        })

    return make_json_safe({
        "product_name": "Unlimited Upside Buffer Ladder",
        "products": rows,
    })
    

def analyze_defined_income_product(
    expiry_chain,
    floor_pct=0.10,
    cap_pct=0.08,
    assumed_dividend_yield=0.01,
):
    """
    Income product:
    Long underlying + long put + short call.

    Uses ORATS cleaned chain format:
    - strike
    - spot
    - dte
    - putAskPrice
    - callBidPrice
    """

    g = expiry_chain.copy()

    if g.empty:
        raise ValueError("Empty expiry chain.")

    required_cols = [
        "strike",
        "spot",
        "dte",
        "putAskPrice",
        "callBidPrice",
    ]

    missing = [col for col in required_cols if col not in g.columns]
    if missing:
        raise ValueError(f"Missing required columns for income product: {missing}")

    spot = float(g["spot"].median())
    dte = float(g["dte"].median())
    notional = spot * MULT

    floor_strike_target = spot * (1 - floor_pct)
    cap_strike_target = spot * (1 + cap_pct)

    puts = g[
        (g["strike"] <= spot)
        & (g["putAskPrice"] > 0)
    ].copy()

    calls = g[
        (g["strike"] >= spot)
        & (g["callBidPrice"] > 0)
    ].copy()

    if puts.empty:
        raise ValueError("No valid puts found for income product.")

    if calls.empty:
        raise ValueError("No valid calls found for income product.")

    puts["floor_distance"] = (puts["strike"] - floor_strike_target).abs()
    calls["cap_distance"] = (calls["strike"] - cap_strike_target).abs()

    put = puts.sort_values(["floor_distance", "strike"], ascending=[True, False]).iloc[0]
    call = calls.sort_values(["cap_distance", "strike"], ascending=[True, True]).iloc[0]

    put_cost_per_share = float(put["putAskPrice"])
    call_credit_per_share = float(call["callBidPrice"])

    option_income_per_share = call_credit_per_share - put_cost_per_share
    option_income_dollars = option_income_per_share * MULT
    option_income_pct = option_income_dollars / notional

    expected_dividend_dollars = notional * assumed_dividend_yield * (dte / 365.25)
    expected_dividend_per_share = expected_dividend_dollars / MULT

    total_income_dollars = option_income_dollars + expected_dividend_dollars
    total_income_pct = total_income_dollars / notional

    annualized_option_income_pct = option_income_pct * (365.25 / dte)
    annualized_total_income_pct = total_income_pct * (365.25 / dte)

    floor_return_before_income = float(put["strike"]) / spot - 1
    cap_return_before_income = float(call["strike"]) / spot - 1

    floor_return_after_income = floor_return_before_income + total_income_pct
    cap_return_after_income = cap_return_before_income + total_income_pct

    liq_score, total_volume, total_oi = liquidity_score(put, call)

    return make_json_safe({
        "product_name": "Defined Income",
        "strategy": "income_collar",
        "structure": "collar",
        "backend_structure": "long_underlying_plus_long_put_short_call",
        "expirDate": g["expirDate"].iloc[0],
        "dte": dte,
        "spot": spot,
        "notional": notional,

        "assumed_dividend_yield": assumed_dividend_yield,

        "floor_pct_requested": floor_pct,
        "cap_pct_requested": cap_pct,
        "floor_strike_target": floor_strike_target,
        "cap_strike_target": cap_strike_target,

        "long_put_strike": float(put["strike"]),
        "call_strike": float(call["strike"]),

        "put_cost_per_share": put_cost_per_share,
        "call_credit_per_share": call_credit_per_share,
        "option_income_per_share": option_income_per_share,
        "option_income_dollars": option_income_dollars,
        "option_income_pct": option_income_pct,
        "annualized_option_income_pct": annualized_option_income_pct,

        "expected_dividend_dollars": expected_dividend_dollars,
        "expected_dividend_per_share": expected_dividend_per_share,
        "total_income_dollars": total_income_dollars,
        "total_income_pct": total_income_pct,
        "annualized_total_income_pct": annualized_total_income_pct,

        "floor_return_before_income": floor_return_before_income,
        "cap_return_before_income": cap_return_before_income,
        "floor_return_after_income": floor_return_after_income,
        "cap_return_after_income": cap_return_after_income,

        "total_volume": total_volume,
        "total_oi": total_oi,
        "liquidity_score": liq_score,

        "display": {
            "title": "Defined Income",
            "subtitle": "Income with defined downside and capped upside",
            "estimated_income_pct": round_pct(total_income_pct),
            "estimated_annualized_income_pct": round_pct(annualized_total_income_pct),
            "estimated_floor_pct": round_pct(floor_return_after_income),
            "estimated_cap_pct": round_pct(cap_return_after_income),
            "explanation": (
                "Designed to generate income by selling capped upside while using a put "
                "to define downside risk over the selected outcome period."
            ),
        },
    })


if __name__ == "__main__":
    main()