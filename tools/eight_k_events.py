"""
tools/eight_k_events.py — RiskLens v2
=======================================
Implements the `analyze_8k_events` MCP tool — real-time material event
intelligence from 8-K filings, mapped to the same risk-materiality
framework as Risk Factors and MD&A.
"""

import asyncio
import time

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from core.eight_k import analyze_8k_filings
from core.fetcher import PIPELINE_TIMEOUT as _FETCHER_PIPELINE_TIMEOUT
from schemas import EightKOutput

# Explicit outer timeout so a hang anywhere downstream (cache, EDGAR fetch,
# parsing) can never block this tool indefinitely — it will always return
# a clear timeout message instead of leaving the caller waiting forever.
TOOL_TIMEOUT = int(_FETCHER_PIPELINE_TIMEOUT) + 30


def register_eight_k_events(mcp: FastMCP) -> None:

    @mcp.tool()
    async def analyze_8k_events(ticker: str, n_filings: int = 5) -> EightKOutput:
        """
        Catch material events the moment they're disclosed — before the next 10-Q catches up.

        10-K and 10-Q filings tell you a company's standing risks, but they
        can be months stale. 8-K filings are how companies disclose material
        events in real time — companies must file within 4 business days of
        a CEO departure, bankruptcy, earnings release, material contract, or
        auditor change. This tool reads every numbered Item section across
        the most recent 8-K filings and maps each to a risk-materiality tier
        so you instantly know how seriously to take it:

          CRITICAL — bankruptcy, change of control, auditor change/non-reliance,
                      delisting notice, acceleration of debt obligations
          HIGH     — executive departures, M&A completion, earnings releases,
                      material contract signings/terminations, impairments
          MODERATE — bylaw amendments, equity sales, Reg FD disclosures
          LOW      — code of ethics updates, exhibit-only filings

        Built for: anyone monitoring a position or watchlist who needs to
        know "did anything material just happen" without manually checking
        EDGAR, and risk teams who need an early-warning layer between
        quarterly filings. Cached for 3-7 days.

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
        start = time.monotonic()
        ticker = ticker.upper().strip()
        if not ticker or not ticker.replace("-", "").replace(".", "").isalpha():
            raise ToolError(f"Invalid ticker: {ticker!r}. Use a US stock symbol like AAPL.")
        n_filings = max(1, min(n_filings, 10))

        try:
            result = await asyncio.wait_for(
                analyze_8k_filings(ticker, n_filings=n_filings),
                timeout=TOOL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            return EightKOutput(
                ticker=ticker, pipeline_success=False,
                failure_reason=f"8-K analysis timed out after {TOOL_TIMEOUT}s. Try again — "
                                f"large filers can occasionally need a second attempt.",
                events=[], filing_count=0, highest_risk_event=None,
                elapsed_seconds=round(elapsed, 2),
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            return EightKOutput(
                ticker=ticker, pipeline_success=False,
                failure_reason=f"Unexpected error: {exc}",
                events=[], filing_count=0, highest_risk_event=None,
                elapsed_seconds=round(elapsed, 2),
            )

        result.pop("from_cache", None)
        return EightKOutput(**result)