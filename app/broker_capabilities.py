from dataclasses import dataclass


@dataclass(frozen=True)
class BrokerExecutionCapabilities:
    brokerage_slug: str
    display_name: str
    supports_execution: bool
    supports_multi_leg_options: bool
    supports_order_preview: bool
    supports_equity_orders: bool
    supports_option_orders: bool


BROKER_CAPABILITIES: dict[str, BrokerExecutionCapabilities] = {
    "ALPACA_PAPER": BrokerExecutionCapabilities(
        brokerage_slug="ALPACA_PAPER",
        display_name="Alpaca Paper",
        supports_execution=True,
        supports_multi_leg_options=True,
        supports_order_preview=False,
        supports_equity_orders=True,
        supports_option_orders=True,
    ),
    "ALPACA": BrokerExecutionCapabilities(
        brokerage_slug="ALPACA",
        display_name="Alpaca",
        supports_execution=True,
        supports_multi_leg_options=True,
        supports_order_preview=False,
        supports_equity_orders=True,
        supports_option_orders=True,
    ),
    "SCHWAB": BrokerExecutionCapabilities(
        brokerage_slug="SCHWAB",
        display_name="Schwab",
        supports_execution=True,
        supports_multi_leg_options=True,
        supports_order_preview=False,
        supports_equity_orders=True,
        supports_option_orders=True,
    ),
    "ETRADE": BrokerExecutionCapabilities(
        brokerage_slug="ETRADE",
        display_name="E*TRADE",
        supports_execution=True,
        supports_multi_leg_options=True,
        supports_order_preview=True,
        supports_equity_orders=True,
        supports_option_orders=True,
    ),
    "TASTYTRADE": BrokerExecutionCapabilities(
        brokerage_slug="TASTYTRADE",
        display_name="tastytrade",
        supports_execution=True,
        supports_multi_leg_options=True,
        supports_order_preview=True,
        supports_equity_orders=True,
        supports_option_orders=True,
    ),
    "WEBULL": BrokerExecutionCapabilities(
        brokerage_slug="WEBULL",
        display_name="Webull",
        supports_execution=True,
        supports_multi_leg_options=True,
        supports_order_preview=True,
        supports_equity_orders=True,
        supports_option_orders=True,
    ),
}


def get_broker_capabilities(
    brokerage_slug: str,
) -> BrokerExecutionCapabilities | None:
    """
    Return capabilities by SnapTrade brokerage slug.

    Use the SnapTrade slug from the connection/account raw data—not a
    frontend display name such as 'Schwab Trading'.
    """
    return BROKER_CAPABILITIES.get(
        brokerage_slug.strip().upper()
    )

def resolve_brokerage_slug(
    institution_name: str | None,
    is_paper: bool = False,
) -> str | None:
    """
    Convert the connected account's SnapTrade display fields into Parity's
    canonical execution key.

    Unknown institutions return None so execution is disabled by default.
    """
    normalized_name = (institution_name or "").strip().lower()

    if normalized_name == "alpaca paper":
        return "ALPACA_PAPER"

    if normalized_name == "alpaca":
        return "ALPACA_PAPER" if is_paper else "ALPACA"

    if normalized_name in {
        "schwab",
        "schwab trading",
    }:
        return "SCHWAB"

    if normalized_name in {
        "etrade",
        "e*trade",
    }:
        return "ETRADE"

    if normalized_name == "tastytrade":
        return "TASTYTRADE"

    if normalized_name == "webull":
        return "WEBULL"

    return None