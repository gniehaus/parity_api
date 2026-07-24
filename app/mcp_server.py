from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings

parity_mcp = FastMCP(
    name="Parity Outcomes",
    instructions=(
        "Provide illustrative defined-outcome analytics calculated from "
        "user-entered parameters. Do not rank, select, recommend, connect "
        "brokerages, or submit transactions."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "parity-api-snaptrade.onrender.com",
            "parity-api-snaptrade.onrender.com:*",
            "localhost:*",
            "127.0.0.1:*",
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
        "version": "1.0.0",
    }