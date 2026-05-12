# import requests
# url = 'https://api.orats.io/datav2/live/one-minute/strikes/chain?token=c9435b07-b9ed-44c2-a67f-c5e98edcb37d&ticker=XSP'
# payload={}
# headers={}
# response = requests.request("GET", url, headers=headers, data=payload)
# # print(response.text)

# import pandas as pd
# # pd.DataFrame(response.text)
# import pandas as pd
# from io import StringIO

# df = pd.read_csv(StringIO(response.text))


# import pandas as pd
# import numpy as np


# def get_closest_expiry_chains(
#     df,
#     target_dte=365,
#     n_expiries=3,
#     ticker=None
# ):
#     """
#     Return the option chains for the n expiries closest to the user's target DTE.

#     Parameters
#     ----------
#     df : pd.DataFrame
#         Full option chain dataframe.
#     target_dte : int
#         User's desired time horizon in days, e.g. 365.
#     n_expiries : int
#         Number of closest expiries to return.
#     ticker : str or None
#         Optional ticker filter, e.g. "XSP" or "SPX".

#     Returns
#     -------
#     closest_chains : pd.DataFrame
#         Full option chain rows for the closest expiries.
#     expiry_summary : pd.DataFrame
#         Summary showing which expiries were selected.
#     """

#     chain = df.copy()

#     if ticker is not None:
#         chain = chain[chain["ticker"] == ticker].copy()

#     chain["expirDate"] = pd.to_datetime(chain["expirDate"])
#     chain["tradeDate"] = pd.to_datetime(chain["tradeDate"])
#     chain["dte"] = pd.to_numeric(chain["dte"], errors="coerce")

#     expiry_summary = (
#         chain
#         .groupby("expirDate", as_index=False)
#         .agg(
#             dte=("dte", "median"),
#             num_strikes=("strike", "nunique"),
#             spot=("spotPrice", "median"),
#             stock_price=("stockPrice", "median"),
#             total_call_volume=("callVolume", "sum"),
#             total_put_volume=("putVolume", "sum"),
#             total_call_oi=("callOpenInterest", "sum"),
#             total_put_oi=("putOpenInterest", "sum"),
#         )
#     )

#     expiry_summary["dte_diff"] = (expiry_summary["dte"] - target_dte).abs()

#     expiry_summary = expiry_summary.sort_values(
#         by=["dte_diff", "dte"],
#         ascending=[True, True]
#     )

#     selected_expiries = expiry_summary.head(n_expiries)["expirDate"].tolist()

#     closest_chains = chain[chain["expirDate"].isin(selected_expiries)].copy()

#     closest_chains = closest_chains.sort_values(
#         by=["expirDate", "strike"]
#     ).reset_index(drop=True)

#     expiry_summary = expiry_summary.head(n_expiries).reset_index(drop=True)

#     return closest_chains, expiry_summary


# closest_chains, expiry_summary = get_closest_expiry_chains(
#     df,
#     target_dte=90,
#     n_expiries=5,
#     ticker="XSP"
# )

# print(expiry_summary)


# import pandas as pd
# import numpy as np

# MULT = 100


# def find_classic_and_buffered_collars(
#     closest_chains,
#     target_loss_pct=0.1,
#     target_gain_pct=0.2,
#     max_net_cost_bps=100,
#     put_buffer_pct=0.15,
#     call_buffer_pct=0.2,
#     min_buffer_width=1,
#     max_buffer_width=100,
# ):
#     """
#     Compare two structures:

#     1. Classic Collar
#         Long underlying
#         Buy downside put
#         Sell upside call

#         This creates a hard floor.

#     2. Buffered Collar
#         Long underlying
#         Buy ATM put
#         Sell lower put
#         Sell upside call

#         This hedges the first X% of losses.
#         Below the short put, downside resumes.
#     """

#     df = closest_chains.copy()

#     df["expirDate"] = pd.to_datetime(df["expirDate"])
#     df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
#     df["dte"] = pd.to_numeric(df["dte"], errors="coerce")

#     numeric_cols = [
#         "stockPrice",
#         "spotPrice",
#         "callBidPrice",
#         "callAskPrice",
#         "putBidPrice",
#         "putAskPrice",
#         "callVolume",
#         "putVolume",
#         "callOpenInterest",
#         "putOpenInterest",
#         "callBidSize",
#         "callAskSize",
#         "putBidSize",
#         "putAskSize",
#     ]

#     for col in numeric_cols:
#         if col in df.columns:
#             df[col] = pd.to_numeric(df[col], errors="coerce")

#     df["spot"] = df["spotPrice"].fillna(df["stockPrice"])
#     df["callMid"] = (df["callBidPrice"] + df["callAskPrice"]) / 2
#     df["putMid"] = (df["putBidPrice"] + df["putAskPrice"]) / 2

#     rows = []

#     for expiry, g in df.groupby("expirDate"):
#         g = g.sort_values("strike").copy()

#         spot = float(g["spot"].median())
#         dte = float(g["dte"].median())
#         notional = spot * MULT

#         max_net_cost_dollars = notional * max_net_cost_bps / 10000

#         target_put_strike = spot * (1 - target_loss_pct)
#         target_call_strike = spot * (1 + target_gain_pct)

#         g["atm_distance"] = (g["strike"] - spot).abs()
#         atm_idx = g["atm_distance"].idxmin()
#         atm_strike = float(g.loc[atm_idx, "strike"])

#         atm_row = g.loc[atm_idx]

#         # -----------------------------
#         # Candidate puts/calls
#         # -----------------------------
#         protective_puts = g[
#             (g["strike"] < spot)
#             & (g["strike"] >= target_put_strike * (1 - put_buffer_pct))
#             & (g["strike"] <= target_put_strike * (1 + put_buffer_pct))
#             & (g["putAskPrice"] > g["putBidPrice"])
#             & (g["putMid"] > 0)
#         ].copy()

#         calls = g[
#             (g["strike"] > spot)
#             & (g["strike"] >= target_call_strike * (1 - call_buffer_pct))
#             & (g["strike"] <= target_call_strike * (1 + call_buffer_pct))
#             & (g["callAskPrice"] > g["callBidPrice"])
#             & (g["callMid"] > 0)
#         ].copy()

#         lower_puts = g[
#             (g["strike"] < atm_strike)
#             & (g["putAskPrice"] > g["putBidPrice"])
#             & (g["putMid"] > 0)
#         ].copy()

#         if calls.empty:
#             continue

#         # ==================================================
#         # 1. CLASSIC COLLAR
#         # Long underlying + buy downside put + sell call
#         # ==================================================
#         if not protective_puts.empty:
#             for _, put in protective_puts.iterrows():
#                 for _, call in calls.iterrows():

#                     put_strike = float(put["strike"])
#                     call_strike = float(call["strike"])

#                     put_cost = put["putMid"] * MULT
#                     call_credit = call["callMid"] * MULT

#                     net_cost = put_cost - call_credit
#                     net_cost_bps = net_cost / notional * 10000

#                     if abs(net_cost) > max_net_cost_dollars:
#                         continue

#                     floor_return_from_strike = put_strike / spot - 1
#                     cap_return_from_strike = call_strike / spot - 1

#                     net_cost_return = net_cost / notional

#                     floor_return = floor_return_from_strike - net_cost_return
#                     cap_return = cap_return_from_strike - net_cost_return

#                     floor_value = notional * (1 + floor_return)
#                     cap_value = notional * (1 + cap_return)

#                     max_loss_dollars = notional - floor_value
#                     max_gain_dollars = cap_value - notional

#                     worst_net_cost = (put["putAskPrice"] - call["callBidPrice"]) * MULT
#                     bid_ask_drag_dollars = worst_net_cost - net_cost
#                     bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

#                     total_volume = (
#                         put.get("putVolume", 0)
#                         + call.get("callVolume", 0)
#                     )

#                     total_oi = (
#                         put.get("putOpenInterest", 0)
#                         + call.get("callOpenInterest", 0)
#                     )

#                     floor_error = abs(floor_return - (-target_loss_pct))
#                     cap_error = abs(cap_return - target_gain_pct)
#                     outcome_error = 2.0 * floor_error + cap_error

#                     liquidity_score = np.log1p(total_volume) + np.log1p(total_oi)

#                     rank_score = (
#                         -100.0 * outcome_error
#                         -0.10 * abs(net_cost_bps)
#                         -0.05 * bid_ask_drag_bps
#                         + liquidity_score
#                     )

#                     rows.append({
#                         "strategy": "classic_collar",
#                         "expirDate": expiry,
#                         "dte": dte,
#                         "spot": spot,
#                         "notional": notional,

#                         "long_put_strike": put_strike,
#                         "short_put_strike": np.nan,
#                         "call_strike": call_strike,

#                         "buffer_width_points": np.nan,
#                         "buffer_pct": np.nan,

#                         "put_cost_dollars": put_cost,
#                         "short_put_credit_dollars": 0.0,
#                         "put_spread_net_cost_dollars": put_cost,
#                         "call_credit_dollars": call_credit,
#                         "net_cost_dollars": net_cost,
#                         "net_cost_bps": net_cost_bps,

#                         "floor_return": floor_return,
#                         "cap_return": cap_return,
#                         "floor_value": floor_value,
#                         "cap_value": cap_value,
#                         "max_loss_dollars": max_loss_dollars,
#                         "max_gain_dollars": max_gain_dollars,

#                         "market_floor_return": floor_return_from_strike,
#                         "market_cap_return": cap_return_from_strike,

#                         "bid_ask_drag_bps": bid_ask_drag_bps,
#                         "total_volume": total_volume,
#                         "total_oi": total_oi,
#                         "outcome_error": outcome_error,
#                         "rank_score": rank_score,
#                     })

#         # ==================================================
#         # 2. BUFFERED COLLAR
#         # Long underlying + buy ATM put + sell lower put + sell call
#         #
#         # This hedges the first X% of losses.
#         # ==================================================
#         valid_atm_put = (
#             atm_row["putAskPrice"] > atm_row["putBidPrice"]
#             and atm_row["putMid"] > 0
#         )

#         if valid_atm_put and not lower_puts.empty:
#             for _, short_put in lower_puts.iterrows():
#                 short_put_strike = float(short_put["strike"])

#                 buffer_width_points = atm_strike - short_put_strike

#                 if buffer_width_points < min_buffer_width or buffer_width_points > max_buffer_width:
#                     continue

#                 buffer_pct = buffer_width_points / spot

#                 atm_put_cost = atm_row["putMid"] * MULT
#                 short_put_credit = short_put["putMid"] * MULT

#                 put_spread_net_cost = atm_put_cost - short_put_credit

#                 for _, call in calls.iterrows():
#                     call_strike = float(call["strike"])

#                     call_credit = call["callMid"] * MULT

#                     net_cost = put_spread_net_cost - call_credit
#                     net_cost_bps = net_cost / notional * 10000

#                     if abs(net_cost) > max_net_cost_dollars:
#                         continue

#                     # For a buffered collar:
#                     # The first losses from spot down to short_put are offset by the put spread.
#                     # Below short_put, losses resume.
#                     #
#                     # The lowest protected zone is around:
#                     # underlying value at short_put + max put spread value - net cost.
#                     #
#                     # That equals approximately:
#                     # short_put*100 + (atm - short_put)*100 - net cost
#                     # = atm*100 - net cost
#                     #
#                     # So the buffer protects the first buffer_pct of losses,
#                     # but it is NOT a hard floor if market keeps falling.

#                     max_buffer_value = buffer_width_points * MULT

#                     protected_zone_value = (
#                         short_put_strike * MULT
#                         + max_buffer_value
#                         - net_cost
#                     )

#                     cap_value = call_strike * MULT - net_cost

#                     protected_zone_return = protected_zone_value / notional - 1
#                     cap_return = cap_value / notional - 1

#                     # Not true max loss. This is the account value after the first-loss buffer is fully used.
#                     protected_zone_loss_dollars = notional - protected_zone_value
#                     max_gain_dollars = cap_value - notional

#                     if max_gain_dollars <= 0:
#                         continue

#                     worst_net_cost = (
#                         atm_row["putAskPrice"]
#                         - short_put["putBidPrice"]
#                         - call["callBidPrice"]
#                     ) * MULT

#                     bid_ask_drag_dollars = worst_net_cost - net_cost
#                     bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

#                     total_volume = (
#                         atm_row.get("putVolume", 0)
#                         + short_put.get("putVolume", 0)
#                         + call.get("callVolume", 0)
#                     )

#                     total_oi = (
#                         atm_row.get("putOpenInterest", 0)
#                         + short_put.get("putOpenInterest", 0)
#                         + call.get("callOpenInterest", 0)
#                     )

#                     # For buffered collar, compare buffer_pct to target_loss_pct.
#                     # Cap still compared to target_gain_pct.
#                     buffer_error = abs(buffer_pct - target_loss_pct)
#                     cap_error = abs(cap_return - target_gain_pct)

#                     outcome_error = 2.0 * buffer_error + cap_error

#                     liquidity_score = np.log1p(total_volume) + np.log1p(total_oi)

#                     rank_score = (
#                         -100.0 * outcome_error
#                         -0.10 * abs(net_cost_bps)
#                         -0.05 * bid_ask_drag_bps
#                         + liquidity_score
#                     )

#                     rows.append({
#                         "strategy": "buffered_collar_first_loss",
#                         "expirDate": expiry,
#                         "dte": dte,
#                         "spot": spot,
#                         "notional": notional,

#                         "long_put_strike": atm_strike,
#                         "short_put_strike": short_put_strike,
#                         "call_strike": call_strike,

#                         "buffer_width_points": buffer_width_points,
#                         "buffer_pct": buffer_pct,

#                         "put_cost_dollars": atm_put_cost,
#                         "short_put_credit_dollars": short_put_credit,
#                         "put_spread_net_cost_dollars": put_spread_net_cost,
#                         "call_credit_dollars": call_credit,
#                         "net_cost_dollars": net_cost,
#                         "net_cost_bps": net_cost_bps,

#                         # For buffered collar, these are not hard floor values.
#                         "protected_zone_return": protected_zone_return,
#                         "cap_return": cap_return,
#                         "protected_zone_value": protected_zone_value,
#                         "cap_value": cap_value,
#                         "protected_zone_loss_dollars": protected_zone_loss_dollars,
#                         "max_gain_dollars": max_gain_dollars,

#                         "market_buffer_start_return": atm_strike / spot - 1,
#                         "market_buffer_end_return": short_put_strike / spot - 1,
#                         "market_cap_return": call_strike / spot - 1,

#                         "bid_ask_drag_bps": bid_ask_drag_bps,
#                         "total_volume": total_volume,
#                         "total_oi": total_oi,
#                         "outcome_error": outcome_error,
#                         "rank_score": rank_score,
#                     })

#     if not rows:
#         return pd.DataFrame()

#     out = pd.DataFrame(rows)

#     out = out.sort_values(
#         [
#             "strategy",
#             "outcome_error",
#             "net_cost_bps",
#             "bid_ask_drag_bps",
#             "total_oi",
#         ],
#         ascending=[True, True, True, True, False],
#     ).reset_index(drop=True)

#     return out


# def view_classic_vs_buffered(collar_scenarios, n=25):
#     view = collar_scenarios.copy()

#     pct_cols = [
#         "floor_return",
#         "cap_return",
#         "market_floor_return",
#         "market_cap_return",
#         "buffer_pct",
#         "protected_zone_return",
#         "market_buffer_start_return",
#         "market_buffer_end_return",
#     ]

#     for col in pct_cols:
#         if col in view.columns:
#             view[col + "_pct"] = view[col] * 100

#     cols = [
#         "strategy",
#         "expirDate",
#         "dte",
#         "spot",
#         "notional",

#         "long_put_strike",
#         "short_put_strike",
#         "call_strike",

#         "buffer_width_points",
#         "buffer_pct_pct",

#         "put_cost_dollars",
#         "short_put_credit_dollars",
#         "put_spread_net_cost_dollars",
#         "call_credit_dollars",
#         "net_cost_dollars",
#         "net_cost_bps",

#         "floor_return_pct",
#         "cap_return_pct",
#         "protected_zone_return_pct",

#         "max_loss_dollars",
#         "protected_zone_loss_dollars",
#         "max_gain_dollars",

#         "market_floor_return_pct",
#         "market_buffer_start_return_pct",
#         "market_buffer_end_return_pct",
#         "market_cap_return_pct",

#         "bid_ask_drag_bps",
#         "total_volume",
#         "total_oi",
#         "outcome_error",
#         "rank_score",
#     ]

#     cols = [c for c in cols if c in view.columns]

#     classic = view[view["strategy"] == "classic_collar"][cols].head(n)
#     buffered = view[view["strategy"] == "buffered_collar_first_loss"][cols].head(n)

#     return classic, buffered


# collar_scenarios = find_classic_and_buffered_collars(
#     closest_chains,
#     target_loss_pct=0.1,
#     target_gain_pct=0.15,
#     max_net_cost_bps=100,
#     put_buffer_pct=0.06,
#     call_buffer_pct=0.08,
#     min_buffer_width=1,
#     max_buffer_width=100,
# )

# classic, buffered = view_classic_vs_buffered(collar_scenarios, n=25)



# # buffered
# def view_classic_core(collar_scenarios, n=25):
#     view = collar_scenarios.copy()

#     for col in ["floor_return", "cap_return", "market_floor_return", "market_cap_return"]:
#         if col in view.columns:
#             view[col + "_pct"] = view[col] * 100

#     cols = [
#         "expirDate",
#         "dte",
#         "spot",

#         "long_put_strike",
#         "call_strike",

#         "net_cost_dollars",
#         "net_cost_bps",

#         "floor_return_pct",
#         "cap_return_pct",

#         "max_loss_dollars",
#         "max_gain_dollars",

#         "bid_ask_drag_bps",
#         "total_oi",
#         "outcome_error",
#     ]

#     cols = [c for c in cols if c in view.columns]

#     return (
#         view[view["strategy"] == "classic_collar"]
#         .sort_values(["outcome_error", "bid_ask_drag_bps", "total_oi"], ascending=[True, True, False])
#         [cols]
#         .head(n)
#     )


# def view_buffered_core(collar_scenarios, n=25):
#     view = collar_scenarios.copy()

#     for col in [
#         "buffer_pct",
#         "cap_return",
#         "protected_zone_return",
#         "market_buffer_start_return",
#         "market_buffer_end_return",
#         "market_cap_return",
#     ]:
#         if col in view.columns:
#             view[col + "_pct"] = view[col] * 100

#     cols = [
#         "expirDate",
#         "dte",
#         "spot",

#         "long_put_strike",
#         "short_put_strike",
#         "call_strike",

#         "buffer_width_points",
#         "buffer_pct_pct",

#         "net_cost_dollars",
#         "net_cost_bps",

#         "protected_zone_return_pct",
#         "cap_return_pct",

#         "protected_zone_loss_dollars",
#         "max_gain_dollars",

#         "market_buffer_end_return_pct",
#         "market_cap_return_pct",

#         "bid_ask_drag_bps",
#         "total_oi",
#         "outcome_error",
#     ]

#     cols = [c for c in cols if c in view.columns]

#     return (
#         view[view["strategy"] == "buffered_collar_first_loss"]
#         .sort_values(["outcome_error", "bid_ask_drag_bps", "total_oi"], ascending=[True, True, False])
#         [cols]
#         .head(n)
#     )


# classic_core = view_classic_core(collar_scenarios, n=25)
# buffered_core = view_buffered_core(collar_scenarios, n=25)

# print(classic_core.head())
# print(buffered_core.head())




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

    Uses the same basic request style as your working notebook:
        requests.request("GET", url, headers={}, data={})
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

    payload = {}
    headers = {}

    response = requests.request(
        "GET",
        url,
        headers=headers,
        data=payload,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"ORATS request failed: {response.status_code} - {response.text[:500]}"
        )

    return pd.read_csv(StringIO(response.text))


# ============================================================
# 2. FIND CLOSEST EXPIRIES
# ============================================================

def get_closest_expiry_chains(
    df,
    target_dte=365,
    n_expiries=5,
    ticker=None,
):
    """
    Return option chains for the n expiries closest to the target DTE.
    """

    chain = df.copy()

    if ticker is not None and "ticker" in chain.columns:
        chain = chain[chain["ticker"] == ticker].copy()

    chain["expirDate"] = pd.to_datetime(chain["expirDate"])
    chain["tradeDate"] = pd.to_datetime(chain["tradeDate"])
    chain["dte"] = pd.to_numeric(chain["dte"], errors="coerce")
    chain["strike"] = pd.to_numeric(chain["strike"], errors="coerce")

    expiry_summary = (
        chain
        .groupby("expirDate", as_index=False)
        .agg(
            dte=("dte", "median"),
            num_strikes=("strike", "nunique"),
            spot=("spotPrice", "median"),
            stock_price=("stockPrice", "median"),
            total_call_volume=("callVolume", "sum"),
            total_put_volume=("putVolume", "sum"),
            total_call_oi=("callOpenInterest", "sum"),
            total_put_oi=("putOpenInterest", "sum"),
        )
    )

    expiry_summary["dte_diff"] = (
        expiry_summary["dte"] - target_dte
    ).abs()

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
# 3. COLLAR ENGINE
# ============================================================

def find_classic_and_buffered_collars(
    closest_chains,
    target_loss_pct=0.02,
    target_gain_pct=0.07,
    max_net_cost_bps=100,
    put_buffer_pct=0.06,
    call_buffer_pct=0.08,
    min_buffer_width=1,
    max_buffer_width=100,
):
    """
    Finds two structures.

    1. Classic Collar:
        Long underlying
        Buy downside put
        Sell upside call

        This creates a hard floor.

    2. Buffered Collar:
        Long underlying
        Buy ATM put
        Sell lower put
        Sell upside call

        This protects the first X% of losses.
        Below the short put, downside resumes.

    Assumes 1 exact option structure:
        1 contract = 100 units of underlying/index exposure.
    """

    df = closest_chains.copy()

    df["expirDate"] = pd.to_datetime(df["expirDate"])
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df["dte"] = pd.to_numeric(df["dte"], errors="coerce")

    numeric_cols = [
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
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["spot"] = df["spotPrice"].fillna(df["stockPrice"])
    df["callMid"] = (df["callBidPrice"] + df["callAskPrice"]) / 2
    df["putMid"] = (df["putBidPrice"] + df["putAskPrice"]) / 2

    rows = []

    for expiry, g in df.groupby("expirDate"):
        g = g.sort_values("strike").copy()

        spot = float(g["spot"].median())
        dte = float(g["dte"].median())
        notional = spot * MULT

        max_net_cost_dollars = notional * max_net_cost_bps / 10000

        target_put_strike = spot * (1 - target_loss_pct)
        target_call_strike = spot * (1 + target_gain_pct)

        g["atm_distance"] = (g["strike"] - spot).abs()
        atm_idx = g["atm_distance"].idxmin()
        atm_strike = float(g.loc[atm_idx, "strike"])
        atm_row = g.loc[atm_idx]

        protective_puts = g[
            (g["strike"] < spot)
            & (g["strike"] >= target_put_strike * (1 - put_buffer_pct))
            & (g["strike"] <= target_put_strike * (1 + put_buffer_pct))
            & (g["putAskPrice"] > g["putBidPrice"])
            & (g["putMid"] > 0)
        ].copy()

        calls = g[
            (g["strike"] > spot)
            & (g["strike"] >= target_call_strike * (1 - call_buffer_pct))
            & (g["strike"] <= target_call_strike * (1 + call_buffer_pct))
            & (g["callAskPrice"] > g["callBidPrice"])
            & (g["callMid"] > 0)
        ].copy()

        lower_puts = g[
            (g["strike"] < atm_strike)
            & (g["putAskPrice"] > g["putBidPrice"])
            & (g["putMid"] > 0)
        ].copy()

        if calls.empty:
            continue

        # ==================================================
        # CLASSIC COLLAR
        # ==================================================

        if not protective_puts.empty:
            for _, put in protective_puts.iterrows():
                for _, call in calls.iterrows():

                    put_strike = float(put["strike"])
                    call_strike = float(call["strike"])

                    put_cost = put["putMid"] * MULT
                    call_credit = call["callMid"] * MULT

                    net_cost = put_cost - call_credit
                    net_cost_bps = net_cost / notional * 10000

                    if abs(net_cost) > max_net_cost_dollars:
                        continue

                    floor_return_from_strike = put_strike / spot - 1
                    cap_return_from_strike = call_strike / spot - 1

                    net_cost_return = net_cost / notional

                    floor_return = floor_return_from_strike - net_cost_return
                    cap_return = cap_return_from_strike - net_cost_return

                    floor_value = notional * (1 + floor_return)
                    cap_value = notional * (1 + cap_return)

                    max_loss_dollars = notional - floor_value
                    max_gain_dollars = cap_value - notional

                    worst_net_cost = (
                        put["putAskPrice"] - call["callBidPrice"]
                    ) * MULT

                    bid_ask_drag_dollars = worst_net_cost - net_cost
                    bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

                    total_volume = (
                        put.get("putVolume", 0)
                        + call.get("callVolume", 0)
                    )

                    total_oi = (
                        put.get("putOpenInterest", 0)
                        + call.get("callOpenInterest", 0)
                    )

                    floor_error = abs(floor_return - (-target_loss_pct))
                    cap_error = abs(cap_return - target_gain_pct)

                    outcome_error = 2.0 * floor_error + cap_error

                    liquidity_score = (
                        np.log1p(total_volume)
                        + np.log1p(total_oi)
                    )

                    rank_score = (
                        -100.0 * outcome_error
                        -0.10 * abs(net_cost_bps)
                        -0.05 * bid_ask_drag_bps
                        + liquidity_score
                    )

                    rows.append({
                        "strategy": "classic_collar",
                        "expirDate": expiry,
                        "dte": dte,
                        "spot": spot,
                        "notional": notional,

                        "long_put_strike": put_strike,
                        "short_put_strike": np.nan,
                        "call_strike": call_strike,

                        "buffer_width_points": np.nan,
                        "buffer_pct": np.nan,

                        "put_cost_dollars": put_cost,
                        "short_put_credit_dollars": 0.0,
                        "put_spread_net_cost_dollars": put_cost,
                        "call_credit_dollars": call_credit,
                        "net_cost_dollars": net_cost,
                        "net_cost_bps": net_cost_bps,

                        "floor_return": floor_return,
                        "cap_return": cap_return,
                        "floor_value": floor_value,
                        "cap_value": cap_value,
                        "max_loss_dollars": max_loss_dollars,
                        "max_gain_dollars": max_gain_dollars,

                        "market_floor_return": floor_return_from_strike,
                        "market_cap_return": cap_return_from_strike,

                        "protected_zone_return": np.nan,
                        "protected_zone_value": np.nan,
                        "protected_zone_loss_dollars": np.nan,
                        "market_buffer_start_return": np.nan,
                        "market_buffer_end_return": np.nan,

                        "bid_ask_drag_bps": bid_ask_drag_bps,
                        "total_volume": total_volume,
                        "total_oi": total_oi,
                        "outcome_error": outcome_error,
                        "rank_score": rank_score,
                    })

        # ==================================================
        # BUFFERED COLLAR
        # ==================================================

        valid_atm_put = (
            atm_row["putAskPrice"] > atm_row["putBidPrice"]
            and atm_row["putMid"] > 0
        )

        if valid_atm_put and not lower_puts.empty:
            for _, short_put in lower_puts.iterrows():

                short_put_strike = float(short_put["strike"])
                buffer_width_points = atm_strike - short_put_strike

                if (
                    buffer_width_points < min_buffer_width
                    or buffer_width_points > max_buffer_width
                ):
                    continue

                buffer_pct = buffer_width_points / spot

                atm_put_cost = atm_row["putMid"] * MULT
                short_put_credit = short_put["putMid"] * MULT
                put_spread_net_cost = atm_put_cost - short_put_credit

                for _, call in calls.iterrows():

                    call_strike = float(call["strike"])
                    call_credit = call["callMid"] * MULT

                    net_cost = put_spread_net_cost - call_credit
                    net_cost_bps = net_cost / notional * 10000

                    if abs(net_cost) > max_net_cost_dollars:
                        continue

                    max_buffer_value = buffer_width_points * MULT

                    protected_zone_value = (
                        short_put_strike * MULT
                        + max_buffer_value
                        - net_cost
                    )

                    cap_value = call_strike * MULT - net_cost

                    protected_zone_return = protected_zone_value / notional - 1
                    cap_return = cap_value / notional - 1

                    protected_zone_loss_dollars = notional - protected_zone_value
                    max_gain_dollars = cap_value - notional

                    if max_gain_dollars <= 0:
                        continue

                    worst_net_cost = (
                        atm_row["putAskPrice"]
                        - short_put["putBidPrice"]
                        - call["callBidPrice"]
                    ) * MULT

                    bid_ask_drag_dollars = worst_net_cost - net_cost
                    bid_ask_drag_bps = bid_ask_drag_dollars / notional * 10000

                    total_volume = (
                        atm_row.get("putVolume", 0)
                        + short_put.get("putVolume", 0)
                        + call.get("callVolume", 0)
                    )

                    total_oi = (
                        atm_row.get("putOpenInterest", 0)
                        + short_put.get("putOpenInterest", 0)
                        + call.get("callOpenInterest", 0)
                    )

                    buffer_error = abs(buffer_pct - target_loss_pct)
                    cap_error = abs(cap_return - target_gain_pct)

                    outcome_error = 2.0 * buffer_error + cap_error

                    liquidity_score = (
                        np.log1p(total_volume)
                        + np.log1p(total_oi)
                    )

                    rank_score = (
                        -100.0 * outcome_error
                        -0.10 * abs(net_cost_bps)
                        -0.05 * bid_ask_drag_bps
                        + liquidity_score
                    )

                    rows.append({
                        "strategy": "buffered_collar_first_loss",
                        "expirDate": expiry,
                        "dte": dte,
                        "spot": spot,
                        "notional": notional,

                        "long_put_strike": atm_strike,
                        "short_put_strike": short_put_strike,
                        "call_strike": call_strike,

                        "buffer_width_points": buffer_width_points,
                        "buffer_pct": buffer_pct,

                        "put_cost_dollars": atm_put_cost,
                        "short_put_credit_dollars": short_put_credit,
                        "put_spread_net_cost_dollars": put_spread_net_cost,
                        "call_credit_dollars": call_credit,
                        "net_cost_dollars": net_cost,
                        "net_cost_bps": net_cost_bps,

                        "floor_return": np.nan,
                        "cap_return": cap_return,
                        "floor_value": np.nan,
                        "cap_value": cap_value,
                        "max_loss_dollars": np.nan,
                        "max_gain_dollars": max_gain_dollars,

                        "market_floor_return": np.nan,
                        "market_cap_return": call_strike / spot - 1,

                        "protected_zone_return": protected_zone_return,
                        "protected_zone_value": protected_zone_value,
                        "protected_zone_loss_dollars": protected_zone_loss_dollars,
                        "market_buffer_start_return": atm_strike / spot - 1,
                        "market_buffer_end_return": short_put_strike / spot - 1,

                        "bid_ask_drag_bps": bid_ask_drag_bps,
                        "total_volume": total_volume,
                        "total_oi": total_oi,
                        "outcome_error": outcome_error,
                        "rank_score": rank_score,
                    })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)

    out = out.sort_values(
        [
            "strategy",
            "outcome_error",
            "bid_ask_drag_bps",
            "total_oi",
            "rank_score",
        ],
        ascending=[True, True, True, False, False],
    ).reset_index(drop=True)

    return out


# ============================================================
# 4. PAYLOADS FOR FRONT END
# ============================================================

def add_percent_columns(df):
    view = df.copy()

    pct_cols = [
        "floor_return",
        "cap_return",
        "market_floor_return",
        "market_cap_return",
        "buffer_pct",
        "protected_zone_return",
        "market_buffer_start_return",
        "market_buffer_end_return",
    ]

    for col in pct_cols:
        if col in view.columns:
            view[col + "_pct"] = view[col] * 100

    return view


def build_frontend_payload(
    collar_scenarios,
    expiry_summary=None,
    n_classic=5,
    n_buffered=5,
):
    view = add_percent_columns(collar_scenarios)

    classic_cols = [
        "strategy",
        "expirDate",
        "dte",
        "spot",
        "notional",

        "long_put_strike",
        "call_strike",

        "net_cost_dollars",
        "net_cost_bps",

        "floor_return_pct",
        "cap_return_pct",

        "max_loss_dollars",
        "max_gain_dollars",

        "bid_ask_drag_bps",
        "total_oi",
        "outcome_error",
        "rank_score",
    ]

    buffered_cols = [
        "strategy",
        "expirDate",
        "dte",
        "spot",
        "notional",

        "long_put_strike",
        "short_put_strike",
        "call_strike",

        "buffer_width_points",
        "buffer_pct_pct",

        "net_cost_dollars",
        "net_cost_bps",

        "protected_zone_return_pct",
        "cap_return_pct",

        "protected_zone_loss_dollars",
        "max_gain_dollars",

        "market_buffer_end_return_pct",
        "market_cap_return_pct",

        "bid_ask_drag_bps",
        "total_oi",
        "outcome_error",
        "rank_score",
    ]

    classic_cols = [c for c in classic_cols if c in view.columns]
    buffered_cols = [c for c in buffered_cols if c in view.columns]

    classic = (
        view[view["strategy"] == "classic_collar"]
        .sort_values(
            ["outcome_error", "bid_ask_drag_bps", "total_oi"],
            ascending=[True, True, False],
        )
        [classic_cols]
        .head(n_classic)
    )

    buffered = (
        view[view["strategy"] == "buffered_collar_first_loss"]
        .sort_values(
            ["outcome_error", "bid_ask_drag_bps", "total_oi"],
            ascending=[True, True, False],
        )
        [buffered_cols]
        .head(n_buffered)
    )

    payload = {
        "classic_collars": classic.to_dict(orient="records"),
        "recommended_buffers": buffered.to_dict(orient="records"),
    }

    if expiry_summary is not None:
        payload["expiry_summary"] = expiry_summary.to_dict(orient="records")

    return payload


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
# 5. COMMAND LINE ENTRY POINT
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--ticker", type=str, default="XSP")
    parser.add_argument("--token", type=str, default=None)

    parser.add_argument(
        "--loss",
        type=float,
        required=True,
        help="Target loss as decimal. Example: 0.02 for 2 percent",
    )
    
    parser.add_argument(
        "--gain",
        type=float,
        required=True,
        help="Target gain as decimal. Example: 0.07 for 7 percent",
    )

    parser.add_argument(
        "--horizon",
        type=int,
        required=True,
        help="Target time horizon in days. Example: 365",
    )

    parser.add_argument("--n-expiries", type=int, default=5)
    parser.add_argument("--max-net-cost-bps", type=float, default=100)
    parser.add_argument("--put-buffer-pct", type=float, default=0.06)
    parser.add_argument("--call-buffer-pct", type=float, default=0.08)
    parser.add_argument("--min-buffer-width", type=float, default=1)
    parser.add_argument("--max-buffer-width", type=float, default=100)

    parser.add_argument(
        "--output",
        type=str,
        default="json",
        choices=["json", "csv"],
    )

    args = parser.parse_args()

    df = fetch_orats_chain(
        ticker=args.ticker,
        token=args.token,
    )

    closest_chains, expiry_summary = get_closest_expiry_chains(
        df,
        target_dte=args.horizon,
        n_expiries=args.n_expiries,
        ticker=args.ticker,
    )

    collar_scenarios = find_classic_and_buffered_collars(
        closest_chains,
        target_loss_pct=args.loss,
        target_gain_pct=args.gain,
        max_net_cost_bps=args.max_net_cost_bps,
        put_buffer_pct=args.put_buffer_pct,
        call_buffer_pct=args.call_buffer_pct,
        min_buffer_width=args.min_buffer_width,
        max_buffer_width=args.max_buffer_width,
    )

    payload = build_frontend_payload(
        collar_scenarios,
        expiry_summary=expiry_summary,
        n_classic=5,
        n_buffered=5,
    )

    payload = make_json_safe(payload)

    if args.output == "json":
        print(json.dumps(payload, indent=2))

    elif args.output == "csv":
        classic = pd.DataFrame(payload["classic_collars"])
        buffered = pd.DataFrame(payload["recommended_buffers"])
        expiries = pd.DataFrame(payload["expiry_summary"])

        classic.to_csv("classic_collars.csv", index=False)
        buffered.to_csv("recommended_buffers.csv", index=False)
        expiries.to_csv("expiry_summary.csv", index=False)

        print("Wrote classic_collars.csv")
        print("Wrote recommended_buffers.csv")
        print("Wrote expiry_summary.csv")


if __name__ == "__main__":
    main()