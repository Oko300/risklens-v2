"""
tools/eight_k_events.py — RiskLens v2
=======================================
8-K material event analysis — COMING SOON.

8-K filings are real-time disclosures of material events:
  Item 1.01 — Entry into material agreement
  Item 1.02 — Termination of material agreement
  Item 1.03 — Bankruptcy or receivership
  Item 2.01 — Completion of acquisition
  Item 2.02 — Results of operations (earnings)
  Item 4.01 — Changes in auditor
  Item 5.02 — Executive changes (CEO/CFO departure)
  Item 7.01 — Regulation FD disclosure
  Item 8.01 — Other events
  Item 9.01 — Financial statements and exhibits

Architecture is ready — implementation ships in the next release.
The fetcher, extractor, and scorer are all designed to support 8-K.
"""

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from schemas import EightKOutput


def register_eight_k_events(mcp: FastMCP) -> None:

    @mcp.tool()
    async def analyze_8k_events(ticker: str) -> EightKOutput:
        """
        Analyze recent 8-K material event filings for a US public company.

        8-K filings are real-time disclosures companies must file within 4
        business days of a material event. This tool identifies, classifies,
        and scores the risk impact of recent 8-K events including:

          • Executive leadership changes (CEO/CFO departures)
          • Earnings releases and revenue guidance
          • Material contract signings or terminations
          • Bankruptcy or receivership filings
          • Auditor changes
          • Acquisitions and divestitures
          • Regulation FD disclosures

        Coming in the next release. Use compare_filings or
        generate_executive_report for current risk analysis.

        Args:
            ticker: US stock ticker symbol (e.g. AAPL, MSFT, TSLA).
        """
        return EightKOutput(
            ticker=ticker.upper().strip(),
            pipeline_success=False,
            failure_reason=(
                "8-K analysis is coming in the next release. "
                "Use generate_executive_report or compare_filings for current risk analysis."
            ),
            events=[],
            filing_count=0,
            highest_risk_event=None,
            elapsed_seconds=0.0,
        )
