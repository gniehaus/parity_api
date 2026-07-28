import os
from datetime import datetime, timezone
from typing import Any
import time


from app.db import save_public_scorecard_snapshot
from parity_collar_engine import (
    build_covered_call,
    build_defined_outcome_recommendations,
    clean_chain,
    fetch_orats_chain,
    make_json_safe,
    select_single_expiry,
)


CACHE_KEY = "homepage_v1"
HORIZON_DAYS = 365


BUFFERED_GROWTH_CONFIGS = [
    {"ticker": "NVDA", "asset_name": "NVIDIA"},
    {"ticker": "SPCX", "asset_name": "SpaceX"},
    {"ticker": "MSFT", "asset_name": "Microsoft"},
    {"ticker": "GOOGL", "asset_name": "Google"},
]


COVERED_CALL_CONFIGS = [
    {"ticker": "IBIT", "asset_name": "Bitcoin"},
    {"ticker": "AAPL", "asset_name": "Apple"},
    {"ticker": "MSFT", "asset_name": "Microsoft"},
    {"ticker": "GOOGL", "asset_name": "Google"},
]


DEFINED_FLOOR_CONFIGS = [
    {"ticker": "SPY", "asset_name": "S&P 500"},
    {"ticker": "QQQ", "asset_name": "Nasdaq 100"},
    {"ticker": "IWM", "asset_name": "Russell 2000"},
]

def get_orats_token() -> str:
    token = os.getenv("ORATS_TOKEN")

    if not token:
        raise RuntimeError("Missing ORATS_TOKEN environment variable")

    return token

def fetch_orats_chain_with_retry(
    ticker: str,
    token: str,
    max_attempts: int = 3,
) -> Any:
    """
    Retry transient ORATS authorization, rate-limit, and server failures.
    """

    retryable_statuses = ("403", "429", "500", "502", "503", "504")

    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_orats_chain(
                ticker=ticker,
                token=token,
            )
        except RuntimeError as error:
            message = str(error)
            is_retryable = any(
                status in message
                for status in retryable_statuses
            )

            if not is_retryable or attempt == max_attempts:
                raise

            wait_seconds = attempt * 5

            print(
                f"Transient ORATS failure for {ticker} "
                f"on attempt {attempt}/{max_attempts}. "
                f"Retrying in {wait_seconds} seconds."
            )

            time.sleep(wait_seconds)

    raise RuntimeError(
        f"ORATS retry loop ended unexpectedly for {ticker}"
    )

    
def fetch_unique_orats_chains() -> dict[str, Any]:
    """
    Fetch each homepage ticker from ORATS exactly once during a refresh.
    """

    token = get_orats_token()

    all_configs = (
        BUFFERED_GROWTH_CONFIGS
        + COVERED_CALL_CONFIGS
        + DEFINED_FLOOR_CONFIGS
    )

    unique_tickers = list(
        dict.fromkeys(config["ticker"] for config in all_configs)
    )

    chains: dict[str, Any] = {}

    for ticker in unique_tickers:
        print(f"Fetching ORATS chain for {ticker}")
    
        chains[ticker] = fetch_orats_chain_with_retry(
            ticker=ticker,
            token=token,
        )

    return chains
def build_buffered_growth_cards(
    chains: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Build the four buffered-growth homepage cards from cached ORATS chains.
    """

    cards: list[dict[str, Any]] = []

    for config in BUFFERED_GROWTH_CONFIGS:
        ticker = config["ticker"]

        payload = build_defined_outcome_recommendations(
            df=chains[ticker],
            ticker=ticker,
            horizon=HORIZON_DAYS,
            max_loss_pct=0.20,
            target_gain_pct=0.08,
            target_buffer_pct=0.20,
        )

        product = payload.get("products", {}).get("buffered_growth")

        if not product:
            raise ValueError(
                f"No buffered-growth outcome returned for {ticker}"
            )

        display = product.get("display", {})

        buffer_percent = display.get("estimated_buffer_pct")
        if buffer_percent is None and product.get("buffer_pct") is not None:
            buffer_percent = float(product["buffer_pct"]) * 100

        upside_percent = display.get("estimated_cap_pct")
        if upside_percent is None and product.get("cap_return") is not None:
            upside_percent = float(product["cap_return"]) * 100

        if buffer_percent is None or upside_percent is None:
            raise ValueError(
                f"Incomplete buffered-growth metrics returned for {ticker}"
            )

        cards.append(
            {
                "card_key": "insured",
                "strategy": "buffered_growth",
                "ticker": ticker,
                "asset_name": config["asset_name"],
                "primary_value": float(buffer_percent),
                "primary_label": "Buffer",
                "secondary_value": float(upside_percent),
                "secondary_label": "Upside Cap",
                "href": f"/outcomes/{ticker}",
            }
        )

    return cards

def build_covered_call_cards(
    chains: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Build the four covered-call homepage cards from cached ORATS chains.
    """

    cards: list[dict[str, Any]] = []

    for config in COVERED_CALL_CONFIGS:
        ticker = config["ticker"]

        cleaned_chain = clean_chain(
            chains[ticker],
            ticker=ticker,
        )

        expiry_chain, selected_expiry_summary, _ = select_single_expiry(
            cleaned_chain,
            target_dte=HORIZON_DAYS,
            prefer_at_or_after=True,
            max_dte_overage=250,
        )

        product = build_covered_call(
            expiry_chain=expiry_chain,
            target_income_pct=0.10,
        )

        if not product:
            raise ValueError(
                f"No covered-call outcome returned for {ticker}"
            )

        income_percent = product.get("total_income_pct")
        upside_percent = product.get("cap_return")

        if income_percent is None or upside_percent is None:
            raise ValueError(
                f"Incomplete covered-call metrics returned for {ticker}"
            )

        cards.append(
            {
                "card_key": "covered",
                "strategy": "covered_call",
                "ticker": ticker,
                "asset_name": config["asset_name"],
                "primary_value": float(income_percent) * 100,
                "primary_label": "Income Generated",
                "secondary_value": float(upside_percent) * 100,
                "secondary_label": "Upside Cap",
                "href": f"/outcomes/{ticker}",
                "selected_expiry": make_json_safe(
                    selected_expiry_summary
                ),
            }
        )

    return cards

def build_defined_floor_cards(
    chains: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Build the three defined-floor homepage cards from cached ORATS chains.
    """

    cards: list[dict[str, Any]] = []

    for config in DEFINED_FLOOR_CONFIGS:
        ticker = config["ticker"]

        payload = build_defined_outcome_recommendations(
            df=chains[ticker],
            ticker=ticker,
            horizon=HORIZON_DAYS,
            max_loss_pct=0.0,
            target_gain_pct=0.15,
        )

        product = payload.get("products", {}).get("defined_floor")

        if not product:
            raise ValueError(
                f"No defined-floor outcome returned for {ticker}"
            )

        maximum_loss = product.get("floor_return")
        maximum_upside = product.get("cap_return")

        if maximum_loss is None or maximum_upside is None:
            raise ValueError(
                f"Incomplete defined-floor metrics returned for {ticker}"
            )

        cards.append(
            {
                "card_key": "funded",
                "strategy": "defined_floor",
                "ticker": ticker,
                "asset_name": config["asset_name"],
                "primary_value": abs(float(maximum_loss) * 100),
                "primary_label": "Maximum Loss",
                "secondary_value": float(maximum_upside) * 100,
                "secondary_label": "Maximum Upside",
                "href": f"/outcomes/{ticker}",
            }
        )

    return cards

def build_homepage_scorecard_payload(
    chains: dict[str, Any],
    market_data_timestamp: datetime,
) -> dict[str, Any]:
    """
    Build one complete, atomic homepage scorecard payload.
    """

    buffered_cards = build_buffered_growth_cards(chains)
    covered_call_cards = build_covered_call_cards(chains)
    defined_floor_cards = build_defined_floor_cards(chains)

    cards = (
        buffered_cards
        + defined_floor_cards
        + covered_call_cards
    )

    expected_card_count = (
        len(BUFFERED_GROWTH_CONFIGS)
        + len(DEFINED_FLOOR_CONFIGS)
        + len(COVERED_CALL_CONFIGS)
    )

    if len(cards) != expected_card_count:
        raise ValueError(
            "Homepage scorecard payload is incomplete: "
            f"expected {expected_card_count} cards, received {len(cards)}"
        )

    return make_json_safe(
        {
            "cache_key": CACHE_KEY,
            "market_data_timestamp": (
                market_data_timestamp.isoformat()
            ),
            "cards": cards,
        }
    )

def refresh_public_scorecards() -> dict[str, Any]:
    """
    Fetch all required ORATS chains, build every homepage card,
    and save one complete snapshot only after all calculations succeed.
    """

    print("Starting public homepage scorecard refresh")

    chains = fetch_unique_orats_chains()

    market_data_timestamp = datetime.now(timezone.utc)

    payload = build_homepage_scorecard_payload(
        chains=chains,
        market_data_timestamp=market_data_timestamp,
    )

    saved_snapshot = save_public_scorecard_snapshot(
        cache_key=CACHE_KEY,
        payload=payload,
        market_data_timestamp=market_data_timestamp,
    )

    print(
        "Public homepage scorecard refresh completed: "
        f"{len(payload['cards'])} cards saved"
    )

    return saved_snapshot

def main() -> None:
    try:
        refresh_public_scorecards()
    except Exception as error:
        print(
            "Public homepage scorecard refresh failed: "
            f"{type(error).__name__}: {error}"
        )
        raise


if __name__ == "__main__":
    main()