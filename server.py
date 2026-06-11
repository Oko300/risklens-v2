"""
server.py — RiskLens v2 FastMCP Server
======================================
Entry point for the RiskLens v2 MCP server.

RiskLens v2 gives financial analysts, investors, and developers structured
AI-ready analysis of SEC 10-Q and 10-K filings directly inside Claude,
Cursor, Windsurf, or any MCP-compatible client.

Tools
-----
  compare_filings      — Delta analysis of Risk Factors and MD&A across two filings.
  analyze_risk_trends  — Multi-year risk trend tracking across up to 8 filings.
  categorize_risks     — Automatic categorization of risk factors by domain.

Authentication
--------------
Context Protocol JWT authentication is enforced on all tool calls.
GET /mcp returns a 200 SSE keepalive for Refresh Skills discovery.
"""

import os
import json
import asyncio

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

from tools.compare_filings import register_compare_filings
from tools.risk_trends      import register_risk_trends
from tools.risk_categorizer import register_risk_categorizer
from auth.middleware        import ContextAuthASGI, LifespanBridge


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="RiskLens v2",
)

# Register all tools onto the shared mcp instance
register_compare_filings(mcp)
register_risk_trends(mcp)
register_risk_categorizer(mcp)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    mcp_app  = mcp.http_app(path="/mcp")
    auth_app = ContextAuthASGI(mcp_app)
    app      = LifespanBridge(mcp_app=mcp_app, auth_app=auth_app)

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)