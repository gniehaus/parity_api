APPROVED_ETFS = ["SPY", "QQQ", "SCHD", "XLK", "IWM", "EFA", "VWO", "SGOV"]

TECH_SYMBOLS = {"QQQ", "XLK", "NVDA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "GOOG", "TSLA", "AVGO", "AMD"}
INTERNATIONAL_SYMBOLS = {"EFA", "VWO", "VEA", "VXUS", "IEFA", "IEMG", "EEM"}
CASH_LIKE_SYMBOLS = {"SGOV", "BIL", "SHV", "TFLO", "USFR", "MMDA1", "SWVXX", "SNVXX"}


def portfolio_summary(holdings: list[dict], cash: float = 0.0) -> dict:
    total_holdings = sum(float(h.get("market_value") or 0) for h in holdings)
    total_value = total_holdings + float(cash or 0)

    sorted_holdings = sorted(holdings, key=lambda h: float(h.get("market_value") or 0), reverse=True)
    top_holdings = []
    for h in sorted_holdings[:10]:
        mv = float(h.get("market_value") or 0)
        top_holdings.append({**h, "weight": (mv / total_value if total_value else 0)})

    tech_weight = sum(float(h.get("market_value") or 0) for h in holdings if h.get("symbol") in TECH_SYMBOLS) / total_value if total_value else 0
    international_weight = sum(float(h.get("market_value") or 0) for h in holdings if h.get("symbol") in INTERNATIONAL_SYMBOLS) / total_value if total_value else 0
    cash_like_weight = (float(cash or 0) + sum(float(h.get("market_value") or 0) for h in holdings if h.get("symbol") in CASH_LIKE_SYMBOLS)) / total_value if total_value else 0
    top_holding_weight = top_holdings[0]["weight"] if top_holdings else 0

    return {
        "total_value": round(total_value, 2),
        "cash": round(float(cash or 0), 2),
        "top_holdings": top_holdings,
        "metrics": {
            "tech_weight": round(tech_weight, 4),
            "international_weight": round(international_weight, 4),
            "cash_like_weight": round(cash_like_weight, 4),
            "top_holding_weight": round(top_holding_weight, 4),
        },
    }


def recommend_defined_outcome(holdings: list[dict], cash: float = 0.0, investment_amount: float | None = None, risk_preference: str = "balanced") -> dict:
    summary = portfolio_summary(holdings, cash)
    metrics = summary["metrics"]

    if summary["total_value"] <= 0:
        etf = "SPY"
        reason = "Default broad-market sleeve because no portfolio value was detected."
    elif metrics["cash_like_weight"] > 0.30:
        etf = "SPY"
        reason = "You have a large cash or cash-like position. A protected SPY outcome can add broad market exposure while keeping downside defined."
    elif metrics["top_holding_weight"] > 0.25:
        etf = "SGOV"
        reason = "Your portfolio looks concentrated. A conservative SGOV sleeve can complement the portfolio without adding more equity concentration."
    elif metrics["tech_weight"] > 0.35:
        etf = "SCHD"
        reason = "You already have meaningful tech exposure. SCHD can complement that with dividend/value exposure inside a defined outcome sleeve."
    elif metrics["international_weight"] < 0.10:
        etf = "EFA"
        reason = "Your portfolio appears light on international developed-market exposure. EFA can diversify the sleeve outside the U.S."
    else:
        etf = "SPY"
        reason = "SPY gives broad U.S. market exposure and is the cleanest default for a defined outcome sleeve."

    default_max_loss = 0.10 if risk_preference == "growth" else 0.05 if risk_preference == "conservative" else 0.10
    return {
        "recommended_etf": etf,
        "reason": reason,
        "approved_etfs": APPROVED_ETFS,
        "suggested_outcome_inputs": {
            "ticker": etf,
            "investment_amount": investment_amount,
            "max_loss": default_max_loss,
            "horizon_days": 365,
        },
        "portfolio_summary": summary,
    }
