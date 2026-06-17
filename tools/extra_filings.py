"""
tools/extra_filings.py — RiskLens v2
=======================================
Three specialized tools beyond the core 10-K/10-Q/8-K risk pipeline:

  analyze_proxy()             — DEF 14A governance & compensation risk
  analyze_insider_activity()  — Form 4 insider buying/selling
  analyze_ownership()         — 13D/13G/13F ownership concentration & activism

Each strengthens the core risk-intelligence mission: governance risk,
insider conviction, and ownership concentration are all leading indicators
that complement Risk Factors / MD&A disclosure analysis.
"""

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from core.proxy import analyze_proxy_filing
from core.insider import analyze_insider_filings
from core.ownership import analyze_ownership_filings
from schemas import ProxyOutput, InsiderActivityOutput, OwnershipOutput


def register_extra_filings(mcp: FastMCP) -> None:

    # -------------------------------------------------------------------
    @mcp.tool()
    async def analyze_proxy(ticker: str) -> ProxyOutput:
        """
        Analyze governance and compensation risk in a company's latest proxy
        statement (DEF 14A).

        Extracts and scores governance risk signals including:
          • Executive compensation structure and consultant use
          • Related-party transactions
          • Shareholder proposals and proxy contests / activist/dissident language
          • Golden parachutes, clawback provisions, poison pills
          • Dual-class share structures and staggered boards

        Returns a governance_risk_score and a plain-English summary. Use this
        alongside compare_filings (Risk Factors/MD&A) and analyze_insider_activity
        for a complete governance + risk picture.

        Args:
            ticker: US stock ticker symbol (e.g. AAPL, MSFT, TSLA).

        Returns:
            ProxyOutput with detected governance signals, a risk score, and summary.
        """
        ticker = ticker.upper().strip()
        if not ticker or not ticker.replace("-", "").replace(".", "").isalpha():
            raise ToolError(f"Invalid ticker: {ticker!r}.")
        result = await analyze_proxy_filing(ticker)
        result.pop("from_cache", None)
        return ProxyOutput(**result)

    # -------------------------------------------------------------------
    @mcp.tool()
    async def analyze_insider_activity(ticker: str, n_filings: int = 10) -> InsiderActivityOutput:
        """
        Track insider buying and selling activity from recent Form 4 filings.

        Form 4 filings disclose every transaction by officers, directors, and
        10%+ owners within 2 business days. This tool aggregates recent
        transactions and computes net insider buying vs selling — a classic
        signal for conviction (heavy insider buying) or concern (cluster selling,
        especially around earnings or before negative news).

        Args:
            ticker:    US stock ticker symbol (e.g. AAPL, MSFT, TSLA).
            n_filings: Number of recent Form 4 filings to scan (1-25). Default 10.

        Returns:
            InsiderActivityOutput with individual transactions, net shares
            bought/sold, and a plain-English summary of the buy/sell trend.
        """
        ticker = ticker.upper().strip()
        if not ticker or not ticker.replace("-", "").replace(".", "").isalpha():
            raise ToolError(f"Invalid ticker: {ticker!r}.")
        n_filings = max(1, min(n_filings, 25))
        result = await analyze_insider_filings(ticker, n_filings=n_filings)
        result.pop("from_cache", None)
        return InsiderActivityOutput(**result)

    # -------------------------------------------------------------------
    @mcp.tool()
    async def analyze_ownership(ticker: str, n_filings: int = 6) -> OwnershipOutput:
        """
        Analyze institutional and activist ownership from 13D, 13G, and 13F filings.

          • SC 13D — active investors disclosing >5% stake with intent to influence
            (board seats, strategic alternatives, M&A pressure — classic activist signal)
          • SC 13G — passive investors disclosing >5% stake with no intent to influence
          • 13F-HR — quarterly institutional holdings report (managers with >$100M AUM)

        Flags activist-style language (e.g. "board representation", "strategic
        alternatives", "unlock value") and reports ownership concentration —
        a leading indicator companies' own Risk Factors sections often
        cannot capture in real time.

        Args:
            ticker:    US stock ticker symbol (e.g. AAPL, MSFT, TSLA).
            n_filings: Number of recent filings to scan per form type (1-15). Default 6.

        Returns:
            OwnershipOutput with each filing's filer, percent ownership,
            activist signal flag, and a summary of concentration/activism.
        """
        ticker = ticker.upper().strip()
        if not ticker or not ticker.replace("-", "").replace(".", "").isalpha():
            raise ToolError(f"Invalid ticker: {ticker!r}.")
        n_filings = max(1, min(n_filings, 15))
        result = await analyze_ownership_filings(ticker, n_filings=n_filings)
        result.pop("from_cache", None)
        return OwnershipOutput(**result)
