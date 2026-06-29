TRADIER_TOKEN = "q3CIe1Ra9TB1Y2yrnigz8JSK0AiU"
ACCOUNT_ID = "6YB82973"

TRADIER_BASE_URL = "https://api.tradier.com/v1"  # live



import os
import math
import requests
import pandas as pd
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

    g["mid"] = (g["bid"].fillna(0) + g["ask"].fillna(0)) / 2

    # If bid/ask are missing but last exists, fallback to last.
    g.loc[g["mid"] <= 0, "mid"] = g.loc[g["mid"] <= 0, "last"]

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
    target_income_pct: float = 0.01,
):
    calls, _ = split_calls_puts(chain)

    calls = calls[(calls["strike"] > etf_price) & (calls["mid"] > 0)].copy()

    if calls.empty:
        return None

    calls["income_pct"] = calls["mid"] / etf_price
    calls["income_error"] = (calls["income_pct"] - target_income_pct).abs()
    calls["max_upside_pct"] = ((calls["strike"] - etf_price) + calls["mid"]) / etf_price
    calls["price_upside_pct"] = (calls["strike"] - etf_price) / etf_price
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
    protection_pct: float = 0.02,
):
    _, puts = split_calls_puts(chain)

    target_put_strike = etf_price * (1 - protection_pct)

    puts = puts[(puts["strike"] < etf_price) & (puts["mid"] > 0)].copy()

    if puts.empty:
        return None

    best = nearest_row(puts, target_put_strike)

    put_cost = float(best["mid"])
    protection_level_pct = (etf_price - float(best["strike"])) / etf_price
    worst_case_pct_including_cost = protection_level_pct + (put_cost / etf_price)

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
        "insurance_cost_pct": put_cost / etf_price,
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
    max_loss_pct: float = 0.02,
    target_gain_pct: float = 0.05,
):
    calls, puts = split_calls_puts(chain)

    target_put_strike = etf_price * (1 - max_loss_pct)
    target_call_strike = etf_price * (1 + target_gain_pct)

    puts = puts[(puts["strike"] < etf_price) & (puts["mid"] > 0)].copy()
    calls = calls[(calls["strike"] > etf_price) & (calls["mid"] > 0)].copy()

    if puts.empty or calls.empty:
        return None

    put = nearest_row(puts, target_put_strike)
    call = nearest_row(calls, target_call_strike)

    put_cost = float(put["mid"])
    call_credit = float(call["mid"])
    net_cost = put_cost - call_credit

    protection_pct = (etf_price - float(put["strike"])) / etf_price
    price_upside_pct = (float(call["strike"]) - etf_price) / etf_price
    max_upside_pct = (float(call["strike"]) - etf_price - net_cost) / etf_price
    max_loss_pct_actual = protection_pct + (net_cost / etf_price)

    return {
        "product_key": "funded_defense",
        "title": "Funded Defense",
        "subtitle": "Define downside and upside before investing.",
        "strategy": "buy_1_etf_share_buy_1_nanos_put_sell_1_nanos_call",
        "etf_quantity": 1,
        "nanos_contracts": 1,
        "long_put_symbol": put.get("symbol"),
        "long_put_strike": float(put["strike"]),
        "short_call_symbol": call.get("symbol"),
        "short_call_strike": float(call["strike"]),
        "put_cost_dollars": put_cost,
        "call_credit_dollars": call_credit,
        "net_cost_dollars": net_cost,
        "net_cost_pct": net_cost / etf_price,
        "max_loss_pct": max_loss_pct_actual,
        "price_upside_pct": price_upside_pct,
        "max_upside_pct": max_upside_pct,
        "display": {
            "primary_metric": f"{max_loss_pct_actual * 100:.1f}%",
            "primary_label": "Maximum loss",
            "secondary_metric": f"{max_upside_pct * 100:.1f}%",
            "secondary_label": "Maximum upside",
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
    target_income_pct: float = 0.01,
    max_loss_pct: float = 0.02,
    target_gain_pct: float = 0.05,
):
    raw_chain = get_nanos_chain(expiration_date)
    chain = clean_nanos_chain(raw_chain)

    if chain.empty:
        return None

    return {
        "income_defense": build_income_defense(
            chain=chain,
            etf_price=etf_price,
            target_income_pct=target_income_pct,
        ),
        "funded_defense": build_funded_defense(
            chain=chain,
            etf_price=etf_price,
            max_loss_pct=max_loss_pct,
            target_gain_pct=target_gain_pct,
        ),
        "insured_defense": build_insured_defense(
            chain=chain,
            etf_price=etf_price,
            protection_pct=max_loss_pct,
        ),
    }


def build_weekly_outcomes_payload(
    etf_symbol: str = DEFAULT_ETF_SYMBOL,
    target_income_pct: float = 0.01,
    max_loss_pct: float = 0.02,
    target_gain_pct: float = 0.05,
    max_expirations: int = 6,
):
    etf_price = get_last_price(etf_symbol)

    expirations_df = get_nanos_expirations_with_strikes()

    if expirations_df.empty:
        return {
            "symbol": NANOS_SYMBOL,
            "underlying": etf_symbol,
            "title": "Weekly Outcomes",
            "subtitle": "Small-dollar S&P 500 outcomes built with NANOS.",
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
        "title": "Weekly Outcomes",
        "subtitle": "Small-dollar S&P 500 outcomes built with NANOS.",
        "minimum_strategy_size": round(etf_price, 2),
        "etf_price": etf_price,
        "contract_size": 1,
        "assumptions": {
            "etf_quantity": 1,
            "nanos_contracts": 1,
            "note": "ETF and NANOS exposure are intended to move approximately 1-to-1. Actual outcomes may differ due to tracking, settlement, fees, taxes, and execution.",
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
    result = build_weekly_outcomes_payload(
        etf_symbol="SPY",
        target_income_pct=0.01,
        max_loss_pct=0.02,
        target_gain_pct=0.05,
    )

    import json
    print(json.dumps(result, indent=2))