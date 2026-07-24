from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field


# Replace this import with the function that currently runs
# your Parity option-chain and outcome calculations.
from .outcome_engine import find_matching_outcomes


parity_mcp = FastMCP(
    name="Parity Outcomes",
    instructions=(
        "Model illustrative defined-outcome structures using user-selected "
        "parameters. Present tradeoffs clearly. Do not describe results as "
        "personalized advice, connect brokerages, or submit transactions."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)


class OutcomeCandidate(BaseModel):
    candidate_id: str
    symbol: str
    structure: Literal[
        "collar",
        "buffered_growth",
        "covered_call",
    ]

    expiration: str
    underlying_price: float

    long_put_strike: float | None = None
    short_put_strike: float | None = None
    short_call_strike: float | None = None

    max_loss_pct: float | None = None
    buffer_pct: float | None = None
    upside_cap_pct: float | None = None
    estimated_net_cost: float | None = None

    quote_timestamp: str


class ModelOutcomesResponse(BaseModel):
    symbol: str
    methodology: str
    candidates: list[OutcomeCandidate]
    disclosure: str


@parity_mcp.tool(
    name="model_outcomes",
    title="Model defined outcomes",
    description=(
        "Find illustrative collar, buffered-growth, and covered-call "
        "structures matching a user-selected security, downside limit, "
        "time horizon, and comparison objective. This tool does not place "
        "orders or recommend that the user execute a structure."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    structured_output=True,
)
async def model_outcomes(
    symbol: Annotated[
        str,
        Field(min_length=1, max_length=12),
    ],
    shares: Annotated[
        int,
        Field(ge=100, le=1_000_000),
    ],
    max_loss_pct: Annotated[
        float,
        Field(ge=0, le=100),
    ],
    target_days: Annotated[
        int,
        Field(ge=30, le=750),
    ] = 365,
    objective: Literal[
        "maximize_upside",
        "minimize_cost",
        "maximize_buffer",
    ] = "maximize_upside",
) -> ModelOutcomesResponse:
    ticker = symbol.strip().upper()

    # If your function is synchronous, remove "await".
    results = await find_matching_outcomes(
        symbol=ticker,
        shares=shares,
        max_loss_pct=max_loss_pct,
        target_days=target_days,
        objective=objective,
    )

    return ModelOutcomesResponse(
        symbol=ticker,
        methodology=(
            "Structures are ranked according to the user-selected objective "
            "using available market data."
        ),
        candidates=[
            OutcomeCandidate.model_validate(result)
            for result in results
        ],
        disclosure=(
            "Illustrative analysis based on available market data. "
            "Options involve risk, and actual execution prices may differ."
        ),
    )