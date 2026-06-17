"""
tools/eight_k_events.py — RiskLens v2
=======================================
Implements the `analyze_8k_events` MCP tool — real-time material event
intelligence from 8-K filings, mapped to the same risk-materiality
framework as Risk Factors and MD&A.
"""

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from core.eight_k import analyze_8k_filings
from schemas import EightKOutput


def register_eight_k_events(mcp: FastMCP) -> None:

    @mcp.tool()
    async def analyze_8k_events(ticker: str, n_filings: int = 5) -> EightKOutput:
        """
        Analyze recent 8-K material event filings for a US public company.

        8-K filings are real-time disclosures companies must file within 4
        business days of a material event. This tool extracts every numbered
        Item section (e.g. Item 5.02 executive departures, Item 1.03
        bankruptcy, Item 2.02 earnings) from the most recent filings and maps
        each to a risk-materiality tier:

          CRITICAL — bankruptcy, change of control, auditor change/non-reliance,
                      delisting notice, acceleration of debt obligations
          HIGH     — executive departures, M&A completion, earnings releases,
                      material contract signings/terminations, impairments
          MODERATE — bylaw amendments, equity sales, Reg FD disclosures
          LOW      — code of ethics updates, exhibit-only filings

        Use this tool when you want to know "what just happened" rather than
        the standing risk disclosures in a 10-K/10-Q. Combine with
        compare_filings for full risk context.

        Args:
            ticker:    US stock ticker symbol (e.g. AAPL, MSFT, TSLA).
            n_filings: Number of recent 8-K filings to scan (1-10). Default 5.

        Returns:
            EightKOutput with every detected Item event, its materiality tier,
            an excerpt, and the single highest-risk event found across the window.
        """
        ticker = ticker.upper().strip()
        if not ticker or not ticker.replace("-", "").replace(".", "").isalpha():
            raise ToolError(f"Invalid ticker: {ticker!r}. Use a US stock symbol like AAPL.")
        n_filings = max(1, min(n_filings, 10))

        result = await analyze_8k_filings(ticker, n_filings=n_filings)
        result.pop("from_cache", None)
        return EightKOutput(**result)
