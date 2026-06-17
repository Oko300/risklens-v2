"""
tools/extra_filings.py — RiskLens v2
=======================================
Three specialized tools beyond the core 10-K/10-Q/8-K risk pipeline:

  analyze_proxy()             — DEF 14A governance & compensation risk
  analyze_insider_activity()  — Form 4 insider buying/selling
  analyze_ownership()         — 13D/13G/13F ownership concentration & activism

Each strengthens the core risk-intelligence mission: governance risk,
insider conviction, and ownership concentration are all leading indicators
that complement Risk Factors / MD&A disclosure analysis. All three are
cached for 3-7 days.
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
        Surface governance red flags before they show up in a Risk Factors section.

        Proxy statements are where executive pay, board structure, and
        shareholder fights actually get disclosed — and most companies
        don't restate those concerns in their 10-K Risk Factors until
        they've already become a problem. This tool reads the latest
        DEF 14A and flags the signals that matter to governance-focused
        investors and analysts before they escalate:

          • Executive compensation structure and consultant use
          • Related-party transactions
          • Shareholder proposals and proxy contests / activist/dissident language
          • Golden parachutes, clawback provisions, poison pills
          • Dual-class share structures and staggered boards

        Returns a governance_risk_score and a plain-English summary. Built
        for: governance/ESG analysts, activist-adjacent research desks, and
        anyone screening for entrenchment or compensation red flags ahead
        of a proxy season or shareholder vote.

        Use this alongside compare_filings (Risk Factors/MD&A) and
        analyze_insider_activity for a complete governance + risk picture.
        Cached for 3-7 days.

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
        See whether the people who actually run the company are buying or selling.

        Form 4 filings disclose every transaction by officers, directors,
        and 10%+ owners within 2 business days — making insider activity one
        of the fastest, least-noisy conviction signals available. This tool
        parses the raw filing data (not just a rendered summary) for every
        purchase, sale, award, and option exercise, then aggregates it into
        a clear net buying/selling read: heavy insider buying is a classic
        conviction signal, while cluster selling — especially multiple
        insiders selling around the same time — is a classic concern signal.

        Built for: investors using insider activity as a screening factor,
        analysts checking management conviction before initiating coverage,
        and anyone who wants the raw transaction-level evidence rather than
        a vague "insider sentiment" score. Cached for 3-7 days.

        Args:
            ticker:    US stock ticker symbol (e.g. AAPL, MSFT, TSLA).
            n_filings: Number of recent Form 4 filings to scan (1-25). Default 10.

        Returns:
            InsiderActivityOutput with individual transactions (insider name,
            title, transaction type, shares, price), net shares bought/sold,
            and a plain-English summary of the buy/sell trend.
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
        Know who owns the company and whether an activist is circling.

        Ownership concentration and activist pressure are leading risk
        indicators a company's own Risk Factors section almost never
        discloses in real time. This tool scans 13D, 13G, and 13F-HR
        filings — including amendments, which carry most of the real
        activity — and flags activist-style language so you don't have
        to read every cover page yourself:

          • SC 13D — active investors disclosing >5% stake with intent to influence
            (board seats, strategic alternatives, M&A pressure — classic activist signal)
          • SC 13G — passive investors disclosing >5% stake with no intent to influence
          • 13F-HR — quarterly institutional holdings report (managers with >$100M AUM)

        Flags activist-style language (e.g. "board representation", "strategic
        alternatives", "unlock value") and reports ownership concentration.

        Built for: M&A and event-driven research desks watching for activist
        entry points, analysts assessing float and concentration risk, and
        anyone tracking institutional accumulation/distribution. Cached for
        3-7 days.

        Args:
            ticker:    US stock ticker symbol (e.g. AAPL, MSFT, TSLA).
            n_filings: Number of recent filings to scan per form type (1-15). Default 6.

        Returns:
            OwnershipOutput with each filing's filer, percent ownership
            (where disclosed), activist signal flag, and a summary of
            concentration/activism.
        """
        ticker = ticker.upper().strip()
        if not ticker or not ticker.replace("-", "").replace(".", "").isalpha():
            raise ToolError(f"Invalid ticker: {ticker!r}.")
        n_filings = max(1, min(n_filings, 15))
        result = await analyze_ownership_filings(ticker, n_filings=n_filings)
        result.pop("from_cache", None)
        return OwnershipOutput(**result)
