TRADIER_TOKEN = "q3CIe1Ra9TB1Y2yrnigz8JSK0AiU"
ACCOUNT_ID = "6YB82973"

TRADIER_BASE_URL = "https://api.tradier.com/v1"  # live



import os
import math
import requests
import pandas as pd
import numpy as np
from datetime import date, datetime


NANOS_SYMBOL = "NANOS"
DEFAULT_ETF_SYMBOL = "SPY"

def make_json_safe(obj):
    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [make_json_safe(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def tradier_headers():
    if not TRADIER_TOKEN:
        raise ValueError("Missing TRADIER_TOKEN environment variable")
    return {
        "Authorization": f"Bearer {TRADIER_TOKEN}",
        "Accept": "application/json",
    }


def tradier_get(path, params=None):
    url = f"{TRADIER_BASE_URL}{path}"
    r = requests.get(url, headers=tradier_headers(), params=params or {})
    r.raise_for_status()
    return r.json()


def get_quote(symbol: str) -> dict:
    data = tradier_get(
        "/markets/quotes",
        params={"symbols": symbol, "greeks": "false"},
    )
    quotes = data.get("quotes", {}).get("quote")
    if isinstance(quotes, list):
        return quotes[0]
    return quotes or {}


def get_last_price(symbol: str) -> float:
    quote = get_quote(symbol)
    for field in ["last", "bid", "ask", "close", "prevclose"]:
        value = quote.get(field)
        if value is not None:
            return float(value)
    raise ValueError(f"Could not find price for {symbol}")


def get_nanos_expirations_with_strikes() -> pd.DataFrame:
    data = tradier_get(
        "/markets/options/expirations",
        params={
            "symbol": NANOS_SYMBOL,
            "includeAllRoots": "true",
            "strikes": "true",
            "contractSize": "true",
            "expirationType": "true",
        },
    )

    expirations = data.get("expirations", {}).get("expiration", [])
    if isinstance(expirations, dict):
        expirations = [expirations]

    rows = []
    for exp in expirations:
        strikes = exp.get("strikes", {}).get("strike", [])
        if not isinstance(strikes, list):
            strikes = [strikes]

        for strike in strikes:
            rows.append({
                "expiration_date": exp.get("date"),
                "expiration_type": exp.get("expiration_type"),
                "contract_size": exp.get("contract_size"),
                "strike": float(strike),
            })

    return pd.DataFrame(rows)


def get_nanos_chain(expiration: str) -> pd.DataFrame:
    data = tradier_get(
        "/markets/options/chains",
        params={
            "symbol": NANOS_SYMBOL,
            "expiration": expiration,
            "greeks": "true",
        },
    )

    options_block = data.get("options")
    if not options_block:
        return pd.DataFrame()

    options = options_block.get("option", [])
    if isinstance(options, dict):
        options = [options]

    df = pd.DataFrame(options)
    if df.empty:
        return df

    df["expiration"] = expiration
    return df


def clean_nanos_chain(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    g = df.copy()

    numeric_cols = [
        "strike",
        "bid",
        "ask",
        "last",
        "volume",
        "open_interest",
        "contract_size",
    ]

    for col in numeric_cols:
        if col in g.columns:
            g[col] = pd.to_numeric(g[col], errors="coerce")

    g["has_two_sided_quote"] = (
        g["bid"].notna()
        & g["ask"].notna()
        & (g["bid"] > 0)
        & (g["ask"] > 0)
    )

    g["mid"] = np.nan
    g.loc[g["has_two_sided_quote"], "mid"] = (
        g.loc[g["has_two_sided_quote"], "bid"]
        + g.loc[g["has_two_sided_quote"], "ask"]
    ) / 2

    g["option_type"] = g["option_type"].str.lower()

    return g


def split_calls_puts(chain: pd.DataFrame):
    calls = chain[chain["option_type"] == "call"].copy()
    puts = chain[chain["option_type"] == "put"].copy()
    return calls, puts


def nearest_row(df: pd.DataFrame, target_strike: float):
    if df.empty:
        return None

    g = df.copy()
    g["strike_error"] = (g["strike"] - target_strike).abs()

    return g.sort_values(
        ["strike_error", "open_interest", "volume"],
        ascending=[True, False, False],
    ).iloc[0]


def build_income_defense(
    chain: pd.DataFrame,
    etf_price: float,
    nanos_price: float,
    target_income_pct: float = 0.01,
):
    calls, _ = split_calls_puts(chain)

    calls = calls[
        (calls["strike"] > nanos_price)
        & (calls["bid"] > 0)
        & (calls["ask"] > 0)
        & (calls["mid"] > 0)
    ].copy()

    if calls.empty:
        return None

    calls["income_pct"] = calls["mid"] / nanos_price
    calls["income_error"] = (calls["income_pct"] - target_income_pct).abs()
    calls["price_upside_pct"] = (calls["strike"] - nanos_price) / nanos_price
    calls["max_upside_pct"] = (
        (calls["strike"] - nanos_price) + calls["mid"]
    ) / nanos_price
    calls["breakeven_pct"] = -calls["income_pct"]

    best = calls.sort_values(
        ["income_error", "max_upside_pct", "open_interest", "volume"],
        ascending=[True, False, False, False],
    ).iloc[0]

    return {
        "product_key": "income_defense",
        "title": "Income Defense",
        "subtitle": "Generate income while keeping upside exposure.",
        "strategy": "buy_1_etf_share_sell_1_nanos_call",
        "etf_quantity": 1,
        "nanos_contracts": 1,
        "short_call_symbol": best.get("symbol"),
        "short_call_strike": float(best["strike"]),
        "income_dollars": float(best["mid"]),
        "income_pct": float(best["income_pct"]),
        "price_upside_pct": float(best["price_upside_pct"]),
        "max_upside_pct": float(best["max_upside_pct"]),
        "breakeven_pct": float(best["breakeven_pct"]),
        "max_loss_label": "Substantial downside remains",
        "display": {
            "primary_metric": f"{best['income_pct'] * 100:.1f}%",
            "primary_label": "Income received",
            "secondary_metric": f"{best['max_upside_pct'] * 100:.1f}%",
            "secondary_label": "Total max upside",
            "explanation": "Income is received when the strategy is built. Upside is capped above the selected level.",
        },
    }


def build_insured_defense(
    chain: pd.DataFrame,
    etf_price: float,
    nanos_price: float,
    protection_pct: float = 0.02,
):
    _, puts = split_calls_puts(chain)

    target_put_strike = nanos_price * (1 - protection_pct)

    puts = puts[
        (puts["strike"] < nanos_price)
        & (puts["bid"] > 0)
        & (puts["ask"] > 0)
        & (puts["mid"] > 0)
    ].copy()

    if puts.empty:
        return None

    best = nearest_row(puts, target_put_strike)

    put_cost = float(best["mid"])
    protection_level_pct = (nanos_price - float(best["strike"])) / nanos_price
    worst_case_pct_including_cost = protection_level_pct + (put_cost / nanos_price)

    return {
        "product_key": "insured_defense",
        "title": "Insured Defense",
        "subtitle": "Buy downside insurance while keeping upside open.",
        "strategy": "buy_1_etf_share_buy_1_nanos_put",
        "etf_quantity": 1,
        "nanos_contracts": 1,
        "long_put_symbol": best.get("symbol"),
        "long_put_strike": float(best["strike"]),
        "insurance_cost_dollars": put_cost,
        "insurance_cost_pct": put_cost / nanos_price,
        "protection_level_pct": protection_level_pct,
        "worst_case_pct_including_cost": worst_case_pct_including_cost,
        "max_upside_label": "Unlimited",
        "display": {
            "primary_metric": f"{protection_level_pct * 100:.1f}%",
            "primary_label": "Protection level",
            "secondary_metric": "Unlimited",
            "secondary_label": "Upside",
            "explanation": "You pay for downside insurance while keeping upside exposure open.",
        },
    }


def build_funded_defense(
    chain: pd.DataFrame,
    etf_price: float,
    nanos_price: float,
    max_loss_pct: float = 0.02,
    target_gain_pct=None,
    max_near_zero_bps: float = 50,
):
    calls, puts = split_calls_puts(chain)

    puts = puts[
        (puts["strike"] < nanos_price)
        & (puts["bid"] > 0)
        & (puts["ask"] > 0)
        & (puts["mid"] > 0)
    ].copy()

    calls = calls[
        (calls["strike"] > nanos_price)
        & (calls["bid"] > 0)
        & (calls["ask"] > 0)
        & (calls["mid"] > 0)
    ].copy()

    if puts.empty or calls.empty:
        return None

    target_floor_return = -max_loss_pct
    notional = nanos_price
    target_put_strike = nanos_price * (1 - max_loss_pct)

    puts["put_strike_error"] = (puts["strike"] - target_put_strike).abs()
    puts = puts.sort_values(
        ["put_strike_error", "open_interest", "volume"],
        ascending=[True, False, False],
    ).head(8).copy()

    put_strikes = puts["strike"].to_numpy(dtype=float)
    put_costs = puts["mid"].to_numpy(dtype=float)
    put_asks = puts["ask"].to_numpy(dtype=float)
    put_volumes = puts.get("volume", pd.Series(0, index=puts.index)).fillna(0).to_numpy(dtype=float)
    put_oi = puts.get("open_interest", pd.Series(0, index=puts.index)).fillna(0).to_numpy(dtype=float)

    call_strikes = calls["strike"].to_numpy(dtype=float)
    call_credits = calls["mid"].to_numpy(dtype=float)
    call_bids = calls["bid"].to_numpy(dtype=float)
    call_volumes = calls.get("volume", pd.Series(0, index=calls.index)).fillna(0).to_numpy(dtype=float)
    call_oi = calls.get("open_interest", pd.Series(0, index=calls.index)).fillna(0).to_numpy(dtype=float)

    net_cost = put_costs[:, None] - call_credits[None, :]
    net_cost_bps = net_cost / notional * 10000
    abs_net_cost_bps = np.abs(net_cost_bps)

    floor_value = put_strikes[:, None] - net_cost
    cap_value = call_strikes[None, :] - net_cost

    floor_return = floor_value / notional - 1
    cap_return = cap_value / notional - 1

    requested_floor_met = floor_return >= target_floor_return
    near_zero = abs_net_cost_bps <= max_near_zero_bps

    preferred_mask = requested_floor_met & near_zero

    if np.any(preferred_mask):
        selection_mask = preferred_mask
        outside_tolerance = False
        exact_floor_match = True
    elif np.any(requested_floor_met):
        selection_mask = requested_floor_met
        outside_tolerance = True
        exact_floor_match = True
    else:
        selection_mask = np.isfinite(floor_return) & np.isfinite(cap_return)
        outside_tolerance = True
        exact_floor_match = False

    worst_net_cost = put_asks[:, None] - call_bids[None, :]
    bid_ask_drag_bps = (worst_net_cost - net_cost) / notional * 10000

    total_volume = put_volumes[:, None] + call_volumes[None, :]
    total_oi = put_oi[:, None] + call_oi[None, :]
    liquidity = np.log1p(total_volume) + np.log1p(total_oi)

    if target_gain_pct is not None:
        target_cap_return = target_gain_pct
        cap_error = np.abs(cap_return - target_cap_return)

        score = (
            -cap_error * 1_000_000
            -abs_net_cost_bps * 100
            -bid_ask_drag_bps * 10
            + liquidity
        )
    elif exact_floor_match:
        score = (
            -abs_net_cost_bps * 1_000_000
            + cap_return * 100_000
            -bid_ask_drag_bps * 10
            + liquidity
        )
    else:
        score = (
            floor_return * 1_000_000
            -abs_net_cost_bps * 100
            + cap_return * 10_000
            -bid_ask_drag_bps * 10
            + liquidity
        )

    score = np.where(selection_mask, score, -np.inf)

    best_flat_idx = np.argmax(score)
    best_put_idx, best_call_idx = np.unravel_index(best_flat_idx, score.shape)

    put = puts.iloc[best_put_idx]
    call = calls.iloc[best_call_idx]

    best_net_cost = float(net_cost[best_put_idx, best_call_idx])
    best_net_cost_bps = float(net_cost_bps[best_put_idx, best_call_idx])
    best_floor_return = float(floor_return[best_put_idx, best_call_idx])
    best_cap_return = float(cap_return[best_put_idx, best_call_idx])
    best_bid_ask_drag_bps = float(bid_ask_drag_bps[best_put_idx, best_call_idx])
    best_near_zero = bool(near_zero[best_put_idx, best_call_idx])

    if abs(best_net_cost_bps) <= max_near_zero_bps:
        cost_display_label = "approximately $0"
    elif best_net_cost > 0:
        cost_display_label = "small debit"
    else:
        cost_display_label = "small credit"

    return {
        "product_key": "funded_defense",
        "title": "Funded Defense",
        "subtitle": "Define downside and upside before investing.",
        "strategy": "buy_1_etf_share_buy_1_nanos_put_sell_1_nanos_call",
        "etf_quantity": 1,
        "nanos_contracts": 1,

        "target_max_loss_pct": max_loss_pct,
        "requested_floor_met": bool(best_floor_return >= target_floor_return),
        "near_zero_cost_ok": best_near_zero,
        "outside_tolerance": outside_tolerance,
        "exact_floor_match": exact_floor_match,
        "max_near_zero_bps": max_near_zero_bps,

        "long_put_symbol": put.get("symbol"),
        "long_put_strike": float(put["strike"]),
        "short_call_symbol": call.get("symbol"),
        "short_call_strike": float(call["strike"]),

        "put_cost_dollars": float(put["mid"]),
        "call_credit_dollars": float(call["mid"]),
        "net_cost_dollars": best_net_cost,
        "net_cost_pct": best_net_cost / nanos_price,
        "net_cost_bps": best_net_cost_bps,

        "max_loss_pct": abs(best_floor_return),
        "floor_return": best_floor_return,
        "price_upside_pct": (float(call["strike"]) - nanos_price) / nanos_price,
        "max_upside_pct": best_cap_return,
        "cap_return": best_cap_return,

        "bid_ask_drag_bps": best_bid_ask_drag_bps,

        "display": {
            "primary_metric": f"{abs(best_floor_return) * 100:.1f}%",
            "primary_label": "Maximum loss",
            "secondary_metric": f"{best_cap_return * 100:.1f}%",
            "secondary_label": "Maximum upside",
            "estimated_option_cost_label": cost_display_label,
            "explanation": "Protection is funded by giving up upside above the selected level.",
        },
    }


def days_to_expiration(expiration_date: str) -> int:
    exp = datetime.strptime(expiration_date, "%Y-%m-%d").date()
    return max((exp - date.today()).days, 0)


def format_expiration_label(expiration_date: str) -> str:
    exp = datetime.strptime(expiration_date, "%Y-%m-%d")
    return exp.strftime("%a %b %-d") if os.name != "nt" else exp.strftime("%a %b %#d")


def build_products_for_expiration(
    expiration_date: str,
    etf_price: float,
    nanos_price: float,
    target_income_pct: float = 0.01,
    max_loss_pct: float = 0.02,
    target_gain_pct=None,
):
    raw_chain = get_nanos_chain(expiration_date)
    chain = clean_nanos_chain(raw_chain)

    if chain.empty:
        return None

    return {
        "income_defense": build_income_defense(
            chain=chain,
            etf_price=etf_price,
            nanos_price=nanos_price,
            target_income_pct=target_income_pct,
        ),
        "funded_defense": build_funded_defense(
            chain=chain,
            etf_price=etf_price,
            nanos_price=nanos_price,
            max_loss_pct=max_loss_pct,
            target_gain_pct=target_gain_pct,
        ),
        "insured_defense": build_insured_defense(
            chain=chain,
            etf_price=etf_price,
            nanos_price=nanos_price,
            protection_pct=max_loss_pct,
        ),
    }


def build_weekly_outcomes_payload(
    etf_symbol: str = DEFAULT_ETF_SYMBOL,
    target_income_pct: float = 0.01,
    max_loss_pct: float = 0.02,
    target_gain_pct=None,
    max_expirations: int = 6,
):
    etf_price = get_last_price(etf_symbol)
    nanos_price = get_last_price(NANOS_SYMBOL)

    expirations_df = get_nanos_expirations_with_strikes()

    if expirations_df.empty:
        return {
            "symbol": NANOS_SYMBOL,
            "underlying": etf_symbol,
            "title": "Weekly Outcomes",
            "subtitle": "Small-dollar S&P 500 outcomes.",
            "expirations": [],
        }

    expiration_dates = (
        expirations_df["expiration_date"]
        .drop_duplicates()
        .sort_values()
        .head(max_expirations)
        .tolist()
    )

    expiration_payloads = []

    for exp in expiration_dates:
        products = build_products_for_expiration(
            expiration_date=exp,
            etf_price=etf_price,
            nanos_price=nanos_price,
            target_income_pct=target_income_pct,
            max_loss_pct=max_loss_pct,
            target_gain_pct=target_gain_pct,
        )

        if not products:
            continue

        expiration_payloads.append({
            "expiration_date": exp,
            "label": format_expiration_label(exp),
            "days_to_expiration": days_to_expiration(exp),
            "products": products,
        })

    payload = {
        "symbol": NANOS_SYMBOL,
        "underlying": etf_symbol,
        "option_underlying": NANOS_SYMBOL,
        "title": "Weekly Outcomes",
        "subtitle": "Small-dollar S&P 500 outcomes.",
        "minimum_strategy_size": round(etf_price, 2),
        "etf_price": etf_price,
        "nanos_price": nanos_price,
        "contract_size": 1,
        "assumptions": {
            "etf_quantity": 1,
            "nanos_contracts": 1,
            "note": "Displayed outcomes use the option reference level for payoff math. ETF price is used as the user-facing strategy size reference.",
        },
        "quote_filter": {
            "requires_nonzero_bid": True,
            "requires_nonzero_ask": True,
            "uses_last_price_fallback": False,
        },
        "default_inputs": {
            "target_income_pct": target_income_pct,
            "max_loss_pct": max_loss_pct,
            "target_gain_pct": target_gain_pct,
        },
        "expirations": expiration_payloads,
    }

    return make_json_safe(payload)


if __name__ == "__main__":
    import json

    result = build_weekly_outcomes_payload(
        etf_symbol="SPY",
        target_income_pct=0.01,
        max_loss_pct=0.02,
        target_gain_pct=None,
    )

    print(json.dumps(result, indent=2))