"""
server.py — RiskLens v2 FastMCP Server
========================================
RiskLens v2 gives financial analysts, investors, and developers
structured AI-ready risk intelligence from the major SEC filing types
real investors actually use — inside Claude, Cursor, Windsurf, or any
MCP-compatible client.

Tools
-----
  compare_filings           — Delta analysis of Risk Factors and MD&A (10-K/10-Q/20-F).
  analyze_risk_trends       — Multi-year risk trajectory across up to 8 filings.
  categorize_risks          — Domain-by-domain risk breakdown with executive summary.
  generate_executive_report — Beautiful analyst-grade report ready to share.
  analyze_8k_events         — Real-time material event intelligence (8-K).
  analyze_proxy             — Governance & compensation risk (DEF 14A).
  analyze_insider_activity  — Insider buying/selling trends (Form 4).
  analyze_ownership         — Activist & institutional ownership (13D/13G/13F).

Performance
-----------
SQLite + TTL caching (3-7 days, default 7) on every tool — repeat queries
return instantly. Two-filing fetches (compare_filings, generate_executive_report)
run concurrently rather than sequentially, which is the primary fix for
timeouts on large filers (e.g. JPM, BAC).

Authentication
--------------
None. RiskLens v2 runs privately through Claude / Grok / MCP clients —
the prior Context Protocol JWT middleware has been fully removed.
"""

import os
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

from tools.compare_filings   import register_compare_filings
from tools.risk_trends       import register_risk_trends
from tools.risk_categorizer  import register_risk_categorizer
from tools.executive_report  import register_executive_report
from tools.eight_k_events    import register_eight_k_events
from tools.extra_filings     import register_extra_filings
from auth.middleware         import ContextAuthASGI, LifespanBridge

mcp = FastMCP(name="RiskLens v2")

register_compare_filings(mcp)
register_risk_trends(mcp)
register_risk_categorizer(mcp)
register_executive_report(mcp)
register_eight_k_events(mcp)
register_extra_filings(mcp)

if __name__ == "__main__":
    import uvicorn
    mcp_app  = mcp.http_app(path="/mcp")
    auth_app = ContextAuthASGI(mcp_app)
    app      = LifespanBridge(mcp_app=mcp_app, auth_app=auth_app)
    port     = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
