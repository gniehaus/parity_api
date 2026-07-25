from typing import Annotated, Any
import os
import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from .dividend_data import get_dividend_data
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from parity_collar_engine import (
    build_covered_call,
    build_defined_outcome_recommendations,
    clean_chain,
    fetch_orats_chain,
    select_single_expiry,
)

class ClerkTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        client_id = os.getenv("CLERK_OAUTH_CLIENT_ID")
        client_secret = os.getenv("CLERK_OAUTH_CLIENT_SECRET")

        if not client_id or not client_secret:
            raise RuntimeError("Clerk OAuth credentials are not configured.")

        response = requests.post(
            "https://clerk.parityoutcomes.com/oauth/token_info",
            data={"token": token},
            auth=(client_id, client_secret),
            timeout=10,
        )

        if not response.ok:
            return None

        token_info = response.json()

        if (
            not token_info.get("active")
            or token_info.get("client_id") != client_id
        ):
            return None

        scopes = token_info.get("scope", "").split()

        if "profile" not in scopes:
            return None

        return AccessToken(
            token=token,
            client_id=token_info.get("sub", "parity-user"),
            scopes=scopes,
        )


        
parity_mcp = FastMCP(
    name="Parity Outcomes",
    instructions=(
        "Calculate illustrative Defined Floor, Buffered Growth, and Covered "
        "Call structures from the security, time horizon, downside, upside, "
        "buffer and income parameters entered by the user, together with "
        "market-sourced dividend data. Explain "
        "tradeoffs factually. Do not characterize any result as recommended "
        "or optimal. Do not connect brokerages or submit transactions."
    ),
    stateless_http=True,
    json_response=True,
    token_verifier=ClerkTokenVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl("https://clerk.parityoutcomes.com"),
        resource_server_url=AnyHttpUrl(
            "https://mcp.parityoutcomes.com/mcp/"
        ),
        required_scopes=["profile"],
    ),
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "parity-api-snaptrade.onrender.com",
            "parity-api-snaptrade.onrender.com:*",
            "localhost:*",
            "127.0.0.1:*",
            "mcp.parityoutcomes.com",
            "parity-api-snaptrade.onrender.com",
        ],
        allowed_origins=[
            "https://parity-api-snaptrade.onrender.com",
            "https://chatgpt.com",
            "https://chat.openai.com",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ],
    ),
)


@parity_mcp.tool(
    name="parity_status",
    title="Check Parity status",
    description="Confirm that the Parity MCP server is operational.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def parity_status() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "Parity Outcomes MCP",
        "version": "1.1.0",
    }


@parity_mcp.tool(
    name="model_defined_outcomes",
    title="Model defined outcomes",
    description=(
        "Calculate illustrative Defined Floor, Buffered Growth, and Covered "
        "Call structures for 100 shares using live market data and parameters "
        "entered by the user. Returns available strikes, expiration, downside, "
        "upside, buffer, income, estimated costs, dividends, and liquidity "
        "information. This tool does not recommend or execute a transaction."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def model_defined_outcomes(
    symbol: Annotated[
        str,
        Field(
            min_length=1,
            max_length=12,
            pattern=r"^[A-Za-z][A-Za-z0-9.\-]*$",
            description="Stock, ETF, or index ticker, such as SPY or QQQ.",
        ),
    ],
    target_days: Annotated[
        int,
        Field(
            ge=30,
            le=750,
            description="Requested outcome period in calendar days.",
        ),
    ] = 365,
    max_loss_percent: Annotated[
        float,
        Field(
            ge=0,
            le=100,
            description=(
                "Maximum-loss input for the illustrative Defined Floor, "
                "entered as a percentage. Enter 10 for 10%."
            ),
        ),
    ] = 0.5,
    target_gain_percent: Annotated[
        float,
        Field(
            ge=0,
            le=200,
            description=(
                "Target-gain input for Buffered Growth, entered as a "
                "percentage. Enter 8 for 8%."
            ),
        ),
    ] = 8.0,
    target_buffer_percent: Annotated[
        float,
        Field(
            ge=0,
            le=100,
            description=(
                "Requested first-loss buffer, entered as a percentage. "
                "Enter 10 for 10%."
            ),
        ),
    ] = 10.0,
    target_income_percent: Annotated[
        float,
        Field(
            ge=0,
            le=100,
            description=(
                "Requested total income for the Covered Call, entered as a "
                "percentage. Enter 5 for 5%."
            ),
        ),
    ] = 5.0,
) -> dict[str, Any]:
    ticker = symbol.strip().upper()

    try:
        dividend_data = get_dividend_data(ticker)

        annual_dividend_per_share = float(
            dividend_data.get("annual_dividend_per_share") or 0
        )

        dividend_yield = float(
            dividend_data.get("dividend_yield") or 0
        )

        # Fetch the option chain once for all calculations.
        chain = fetch_orats_chain(ticker=ticker)

        # Calculate Defined Floor and Buffered Growth.
        result = build_defined_outcome_recommendations(
            df=chain,
            ticker=ticker,
            horizon=target_days,
            max_loss_pct=max_loss_percent / 100,
            target_gain_pct=target_gain_percent / 100,
            target_buffer_pct=target_buffer_percent / 100,
            assumed_dividend_yield=dividend_yield,
        )

        # Select the appropriate expiration for the Covered Call.
        cleaned_chain = clean_chain(
            chain,
            ticker=ticker,
        )

        expiry_chain, _, _ = select_single_expiry(
            cleaned_chain,
            target_dte=target_days,
            prefer_at_or_after=True,
            max_dte_overage=200,
            max_dte_underage=30,
        )

        # Calculate the Covered Call using the same market data.
        covered_call = build_covered_call(
            expiry_chain,
            target_income_pct=target_income_percent / 100,
            assumed_dividend_yield=dividend_yield,
        )

        products = result.setdefault("products", {})
        products["covered_call"] = covered_call
        
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    except RuntimeError as exc:
        raise ToolError(
            "Live option-market or dividend data is currently unavailable."
        ) from exc

    except Exception as exc:
        raise ToolError(
            "Parity could not complete the requested calculation."
        ) from exc

    products = result.get("products", {})

    if not products or not any(products.values()):
        raise ToolError(
            "No illustrative structures were available for the requested "
            "security, parameters, and time horizon."
        )

    result["analysis_type"] = (
        "illustrative_defined_outcome_calculation"
    )

    result["requested_parameters"] = {
        "symbol": ticker,
        "target_days": target_days,
        "max_loss_percent": max_loss_percent,
        "target_gain_percent": target_gain_percent,
        "target_buffer_percent": target_buffer_percent,
        "target_income_percent": target_income_percent,
        "share_basis": 100,
    }
    result["dividend_assumptions"] = {
        "ticker": ticker,
        "annual_dividend_per_share": annual_dividend_per_share,
        "annual_dividend_yield_percent": dividend_yield * 100,
        "source": dividend_data.get("source", "Implied market dividend data"),
        "source_updated_at": (
            str(dividend_data.get("source_updated_at"))
            if dividend_data.get("source_updated_at")
            else None
        ),
        "fetched_at": (
            str(dividend_data.get("fetched_at"))
            if dividend_data.get("fetched_at")
            else None
        ),
        "methodology": (
            "The current annual dividend rate is prorated over the "
            "selected option period."
        ),
    }

    result["disclosure"] = (
        "Illustrative analysis based on available market data and the "
        "parameters entered by the user. Options involve risk. Quotes and "
        "available strikes can change, and actual execution prices may differ."
    )

    return result