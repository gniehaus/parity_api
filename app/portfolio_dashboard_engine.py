import os
import math
from datetime import date, timedelta
from io import StringIO
from typing import List, Dict, Optional

import numpy as np
import pandas as pd
import requests


POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
ORATS_TOKEN = os.getenv("ORATS_TOKEN")

BENCHMARK = "SPY"
TRADING_DAYS = 252
CASH_SYMBOLS = {"CASH", "USD", "SWEEP", "MONEY_MARKET", "CORE", "BUYING_POWER"}


# =========================
# NORMALIZATION
# =========================

def normalize_holdings(raw_holdings: List[Dict]) -> List[Dict]:
    holdings = []

    for h in raw_holdings:
        symbol = str(h.get("symbol", "")).upper().strip()
        asset_type = str(h.get("asset_type", h.get("asset_class", "equity"))).lower()

        if symbol in CASH_SYMBOLS or asset_type == "cash":
            symbol = "CASH"
            asset_type = "cash"

        if not symbol:
            continue

        market_value = h.get("market_value") or h.get("marketValue") or h.get("value")

        if market_value is None:
            quantity = float(h.get("quantity", 0) or 0)
            price = float(h.get("price", h.get("last_price", 0)) or 0)
            market_value = quantity * price

        market_value = float(market_value or 0)

        if market_value <= 0:
            continue

        holdings.append({
            "symbol": symbol,
            "market_value": market_value,
            "quantity": h.get("quantity"),
            "price": h.get("price") or h.get("last_price"),
            "asset_type": asset_type,
        })

    return holdings


# =========================
# POLYGON PRICE DATA
# =========================

def fetch_polygon_daily_prices(symbol: str, start: str, end: str) -> pd.DataFrame:
    if not POLYGON_API_KEY:
        raise RuntimeError("Missing POLYGON_API_KEY environment variable")

    url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"

    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": POLYGON_API_KEY,
    }

    response = requests.get(url, params=params, timeout=30)

    if response.status_code != 200:
        raise RuntimeError(
            f"Polygon error for {symbol}: {response.status_code} {response.text[:500]}"
        )

    results = response.json().get("results", [])

    if not results:
        raise RuntimeError(f"No Polygon data returned for {symbol}")

    df = pd.DataFrame(results)
    df["date"] = pd.to_datetime(df["t"], unit="ms").dt.date
    df = df.rename(columns={"c": "close", "o": "open", "h": "high", "l": "low", "v": "volume"})
    df = df[["date", "open", "high", "low", "close", "volume"]]
    df["symbol"] = symbol
    df["daily_return"] = df["close"].pct_change()

    return df.dropna(subset=["daily_return"])


def build_returns_matrix(symbols: List[str], start: str, end: str) -> pd.DataFrame:
    frames = []

    for symbol in symbols:
        if symbol == "CASH":
            continue

        try:
            px = fetch_polygon_daily_prices(symbol, start, end)
            frames.append(px[["date", "symbol", "daily_return"]])
        except Exception as e:
            print(f"Skipping {symbol}: {e}")

    if not frames:
        raise RuntimeError("No return data fetched")

    all_returns = pd.concat(frames, ignore_index=True)

    matrix = all_returns.pivot(
        index="date",
        columns="symbol",
        values="daily_return",
    ).sort_index()

    return matrix


# =========================
# ORATS IMPLIED VOL
# =========================

def fetch_orats_chain(ticker: str) -> pd.DataFrame:
    if not ORATS_TOKEN:
        raise RuntimeError("Missing ORATS_TOKEN environment variable")

    url = (
        "https://api.orats.io/datav2/live/one-minute/strikes/chain"
        f"?token={ORATS_TOKEN}&ticker={ticker}"
    )

    response = requests.get(url, timeout=30)

    if response.status_code != 200:
        raise RuntimeError(
            f"ORATS error for {ticker}: {response.status_code} {response.text[:500]}"
        )

    return pd.read_csv(StringIO(response.text))


def estimate_symbol_iv(symbol: str, target_dte: int = 365) -> Optional[float]:
    if symbol == "CASH":
        return 0.0

    try:
        chain = fetch_orats_chain(symbol)
    except Exception as e:
        print(f"Skipping IV for {symbol}: {e}")
        return None

    if chain.empty:
        return None

    for col in ["dte", "strike", "stockPrice", "spotPrice", "callIv", "putIv", "iv"]:
        if col in chain.columns:
            chain[col] = pd.to_numeric(chain[col], errors="coerce")

    if "spotPrice" in chain.columns and "stockPrice" in chain.columns:
        chain["spot"] = chain["spotPrice"].fillna(chain["stockPrice"])
    elif "spotPrice" in chain.columns:
        chain["spot"] = chain["spotPrice"]
    elif "stockPrice" in chain.columns:
        chain["spot"] = chain["stockPrice"]
    else:
        return None

    chain = chain.dropna(subset=["dte", "strike", "spot"]).copy()

    if chain.empty:
        return None

    chain["dte_diff"] = (chain["dte"] - target_dte).abs()
    selected_dte = chain.sort_values("dte_diff")["dte"].iloc[0]

    expiry_chain = chain[chain["dte"] == selected_dte].copy()
    spot = float(expiry_chain["spot"].median())

    expiry_chain["strike_distance"] = (expiry_chain["strike"] - spot).abs()
    atm = expiry_chain.sort_values("strike_distance").iloc[0]

    iv_values = []

    for col in ["callIv", "putIv", "iv"]:
        if col in atm and pd.notnull(atm[col]) and float(atm[col]) > 0:
            iv_values.append(float(atm[col]))

    if not iv_values:
        return None

    return float(np.mean(iv_values))


def estimate_portfolio_implied_vol(weights: Dict[str, float]) -> Dict:
    symbol_ivs = {}

    for symbol, weight in weights.items():
        if symbol == "CASH":
            symbol_ivs[symbol] = 0.0
            continue

        iv = estimate_symbol_iv(symbol)

        if iv is not None:
            symbol_ivs[symbol] = iv

    if not symbol_ivs:
        return {
            "portfolio_implied_volatility": None,
            "symbol_implied_volatility": {},
            "note": "No usable ORATS IV data found.",
        }

    weighted_iv = sum(
        weights.get(symbol, 0) * iv
        for symbol, iv in symbol_ivs.items()
    )

    return {
        "portfolio_implied_volatility": weighted_iv,
        "symbol_implied_volatility": symbol_ivs,
        "note": "Weighted-average implied volatility. Cash IV is treated as 0. Not correlation-adjusted.",
    }


# =========================
# RISK / RETURN METRICS
# =========================

def annualized_return(returns: pd.Series) -> float:
    returns = returns.dropna()

    if len(returns) == 0:
        return 0.0

    cumulative = (1 + returns).prod()
    years = len(returns) / TRADING_DAYS

    if years <= 0:
        return 0.0

    return float(cumulative ** (1 / years) - 1)


def annualized_volatility(returns: pd.Series) -> float:
    return float(returns.dropna().std() * math.sqrt(TRADING_DAYS))


def downside_volatility(returns: pd.Series) -> float:
    downside = returns[returns < 0]

    if len(downside) == 0:
        return 0.0

    return float(downside.std() * math.sqrt(TRADING_DAYS))


def max_drawdown(returns: pd.Series) -> float:
    wealth = (1 + returns.fillna(0)).cumprod()
    peak = wealth.cummax()
    drawdown = wealth / peak - 1
    return float(drawdown.min())


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.04) -> float:
    ann_return = annualized_return(returns)
    ann_vol = annualized_volatility(returns)

    if ann_vol == 0:
        return 0.0

    return float((ann_return - risk_free_rate) / ann_vol)


def sortino_ratio(returns: pd.Series, risk_free_rate: float = 0.04) -> float:
    ann_return = annualized_return(returns)
    downside_vol = downside_volatility(returns)

    if downside_vol == 0:
        return 0.0

    return float((ann_return - risk_free_rate) / downside_vol)


def beta_vs_benchmark(portfolio_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    joined = pd.concat(
        [
            portfolio_returns.rename("portfolio"),
            benchmark_returns.rename("benchmark"),
        ],
        axis=1,
    ).dropna()

    if len(joined) < 30:
        return 0.0

    benchmark_var = joined["benchmark"].var()

    if benchmark_var == 0:
        return 0.0

    return float(joined["portfolio"].cov(joined["benchmark"]) / benchmark_var)


def correlation_vs_benchmark(portfolio_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    joined = pd.concat(
        [
            portfolio_returns.rename("portfolio"),
            benchmark_returns.rename("benchmark"),
        ],
        axis=1,
    ).dropna()

    if len(joined) < 30:
        return 0.0

    return float(joined.corr().iloc[0, 1])


def concentration_metrics(weights: Dict[str, float]) -> Dict:
    sorted_weights = sorted(weights.items(), key=lambda x: x[1], reverse=True)

    largest = sorted_weights[0][1] if sorted_weights else 0.0
    top_3 = sum(w for _, w in sorted_weights[:3])
    hhi = sum(w ** 2 for w in weights.values())

    non_cash_weights = {
        symbol: weight
        for symbol, weight in weights.items()
        if symbol != "CASH"
    }

    sorted_non_cash = sorted(non_cash_weights.items(), key=lambda x: x[1], reverse=True)
    largest_non_cash = sorted_non_cash[0][1] if sorted_non_cash else 0.0

    return {
        "largest_position_weight": largest,
        "largest_non_cash_position_weight": largest_non_cash,
        "top_3_weight": top_3,
        "hhi_concentration": hhi,
        "cash_weight": weights.get("CASH", 0.0),
    }


def risk_score(metrics: Dict) -> Dict:
    vol = metrics.get("annualized_volatility", 0)
    drawdown = abs(metrics.get("max_drawdown", 0))
    beta = metrics.get("beta_vs_spy", 0)
    concentration = metrics.get("largest_non_cash_position_weight", 0)

    score = 0
    score += min(vol / 0.35, 1) * 30
    score += min(drawdown / 0.35, 1) * 30
    score += min(beta / 1.50, 1) * 20
    score += min(concentration / 0.30, 1) * 20

    score = round(float(score), 1)

    if score < 35:
        label = "Low"
    elif score < 65:
        label = "Moderate"
    else:
        label = "High"

    return {
        "score": score,
        "label": label,
    }


# =========================
# PORTFOLIO DASHBOARD
# =========================

def calculate_portfolio_dashboard(
    raw_holdings: List[Dict],
    years_back: int = 1,
    risk_free_rate: float = 0.04,
    include_implied_vol: bool = True,
) -> Dict:
    holdings = normalize_holdings(raw_holdings)

    if not holdings:
        raise RuntimeError("No valid holdings supplied")

    total_market_value = sum(h["market_value"] for h in holdings)

    if total_market_value <= 0:
        raise RuntimeError("Portfolio market value must be greater than zero")

    weights = {
        h["symbol"]: h["market_value"] / total_market_value
        for h in holdings
    }

    symbols = sorted(weights.keys())
    cash_weight = weights.get("CASH", 0.0)

    price_symbols = [s for s in symbols if s != "CASH"]
    all_symbols = sorted(set(price_symbols + [BENCHMARK]))

    end_date = date.today()
    start_date = end_date - timedelta(days=365 * years_back)

    returns_matrix = build_returns_matrix(
        all_symbols,
        start_date.isoformat(),
        end_date.isoformat(),
    )

    available_symbols = [s for s in price_symbols if s in returns_matrix.columns]
    missing_symbols = [s for s in price_symbols if s not in available_symbols]

    if not available_symbols and cash_weight <= 0:
        raise RuntimeError("No usable price data for portfolio holdings")

    if len(returns_matrix.index) > 0:
        portfolio_returns = pd.Series(0.0, index=returns_matrix.index)
    else:
        raise RuntimeError("No usable return index")

    for symbol in available_symbols:
        portfolio_returns += returns_matrix[symbol].fillna(0) * weights.get(symbol, 0)

    if cash_weight > 0:
        daily_cash_return = (1 + risk_free_rate) ** (1 / TRADING_DAYS) - 1
        portfolio_returns += daily_cash_return * cash_weight

    benchmark_returns = (
        returns_matrix[BENCHMARK]
        if BENCHMARK in returns_matrix.columns
        else None
    )

    conc = concentration_metrics(weights)

    metrics = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "years_back": years_back,

        "total_market_value": total_market_value,
        "holdings": holdings,
        "weights": weights,
        "cash_weight": cash_weight,
        "symbols_used": available_symbols + (["CASH"] if cash_weight > 0 else []),
        "symbols_missing": missing_symbols,

        "annualized_return": annualized_return(portfolio_returns),
        "annualized_volatility": annualized_volatility(portfolio_returns),
        "downside_volatility": downside_volatility(portfolio_returns),
        "max_drawdown": max_drawdown(portfolio_returns),
        "sharpe_ratio": sharpe_ratio(portfolio_returns, risk_free_rate),
        "sortino_ratio": sortino_ratio(portfolio_returns, risk_free_rate),

        **conc,
    }

    if benchmark_returns is not None:
        metrics["beta_vs_spy"] = beta_vs_benchmark(portfolio_returns, benchmark_returns)
        metrics["correlation_vs_spy"] = correlation_vs_benchmark(
            portfolio_returns,
            benchmark_returns,
        )
    else:
        metrics["beta_vs_spy"] = 0.0
        metrics["correlation_vs_spy"] = 0.0

    metrics["risk_score"] = risk_score(metrics)

    if include_implied_vol:
        metrics["implied_volatility"] = estimate_portfolio_implied_vol(weights)

    dashboard = {
        "portfolio": {
            "total_market_value": total_market_value,
            "holdings": holdings,
            "weights": weights,
            "cash_weight": cash_weight,
        },
        "risk_return": metrics,
        "chart_data": {
            "portfolio_returns": [
                {"date": str(idx), "daily_return": float(value)}
                for idx, value in portfolio_returns.dropna().items()
            ],
            "portfolio_growth": [
                {"date": str(idx), "value": float(value)}
                for idx, value in (100 * (1 + portfolio_returns.fillna(0)).cumprod()).items()
            ],
        },
    }

    return dashboard