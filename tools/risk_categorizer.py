"""
tools/risk_categorizer.py — RiskLens v2
=========================================
Implements the `categorize_risks` MCP tool.

Fetches the most recent 10-K, 10-Q, or 20-F for a ticker, extracts the
Risk Factors section, and classifies every identified risk into one of
ten standardized risk domains. Results are cached for 3-7 days.
"""

import asyncio
import re
import time
from typing import Literal, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from core.fetcher   import fetch_one_filing, PIPELINE_TIMEOUT as _FETCHER_PIPELINE_TIMEOUT
from core.extractor import extract_sections_cached
from core.cache     import cache_get, cache_set, make_cache_key
from schemas        import (
    CategorizeRisksOutput,
    RiskCategory,
    RiskCategoryDetail,
    RiskCategorizationSummary,
)


# TOOL_TIMEOUT must always exceed the fetcher's own internal PIPELINE_TIMEOUT
# plus headroom for categorization — see executive_report.py for the full
# explanation of why a hardcoded shorter value caused false timeouts on
# large filers regardless of caching.
TOOL_TIMEOUT = int(_FETCHER_PIPELINE_TIMEOUT) + 20

RISK_TAXONOMY: dict[str, dict] = {
    "financial": {
        "label":       "Financial & Liquidity Risk",
        "keywords":    [
            "liquidity", "cash flow", "debt", "leverage", "refinancing",
            "credit facility", "covenant", "default", "insolvency", "going concern",
            "cash burn", "negative cash flow", "working capital", "capital raise",
            "impairment", "write-off", "write-down", "goodwill impairment",
            "material weakness", "restatement", "net loss", "operating loss",
        ],
        "tier": 1,
    },
    "legal_regulatory": {
        "label":       "Legal & Regulatory Risk",
        "keywords":    [
            "litigation", "lawsuit", "class action", "settlement", "injunction",
            "SEC", "DOJ", "regulatory action", "investigation", "criminal",
            "compliance", "GDPR", "CCPA", "antitrust", "FDA", "FTC",
            "enforcement", "penalty", "fine", "sanction",
        ],
        "tier": 1,
    },
    "cybersecurity": {
        "label":       "Cybersecurity & Data Risk",
        "keywords":    [
            "cybersecurity", "data breach", "cyber attack", "ransomware",
            "phishing", "malware", "unauthorized access", "data privacy",
            "personal data", "information security", "incident response",
            "zero-day", "vulnerability", "hacking", "third-party vendor risk",
        ],
        "tier": 1,
    },
    "operational": {
        "label":       "Operational Risk",
        "keywords":    [
            "supply chain", "manufacturing", "disruption", "outage",
            "key personnel", "talent", "workforce reduction", "layoff",
            "restructuring", "systems failure", "technology failure",
            "business continuity", "force majeure", "natural disaster",
            "concentration risk", "single source",
        ],
        "tier": 2,
    },
    "market_competitive": {
        "label":       "Market & Competitive Risk",
        "keywords":    [
            "competition", "market share", "pricing pressure", "commoditization",
            "new entrants", "disruptive technology", "substitution",
            "customer concentration", "churn", "demand decline", "revenue decline",
            "declining revenue", "market downturn",
        ],
        "tier": 2,
    },
    "macro_geopolitical": {
        "label":       "Macroeconomic & Geopolitical Risk",
        "keywords":    [
            "recession", "inflation", "interest rate", "foreign exchange",
            "currency risk", "tariff", "trade restriction", "sanctions",
            "geopolitical", "war", "conflict", "trade war", "export control",
            "macroeconomic", "global economy", "slowdown",
        ],
        "tier": 2,
    },
    "strategic": {
        "label":       "Strategic & Execution Risk",
        "keywords":    [
            "acquisition", "merger", "integration risk", "execution risk",
            "strategic initiative", "transformation", "product launch",
            "expansion", "new market", "joint venture", "partnership risk",
            "organic growth", "pipeline",
        ],
        "tier": 2,
    },
    "technology": {
        "label":       "Technology & Innovation Risk",
        "keywords":    [
            "artificial intelligence", "AI", "machine learning", "cloud",
            "platform risk", "technology risk", "obsolescence", "legacy system",
            "digital transformation", "algorithm", "model risk",
            "intellectual property", "patent", "trade secret",
        ],
        "tier": 2,
    },
    "esg_climate": {
        "label":       "ESG & Climate Risk",
        "keywords":    [
            "climate change", "carbon", "greenhouse gas", "ESG",
            "sustainability", "environmental regulation", "climate risk",
            "physical risk", "transition risk", "net zero", "TCFD",
            "social responsibility", "DEI", "diversity", "human rights",
        ],
        "tier": 3,
    },
    "reputational": {
        "label":       "Reputational & Brand Risk",
        "keywords":    [
            "reputational", "brand", "public perception", "media",
            "social media", "negative publicity", "trust", "credibility",
            "customer confidence", "product recall", "misinformation",
        ],
        "tier": 3,
    },
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_risk_categorizer(mcp: FastMCP) -> None:
    """Register the categorize_risks tool onto a FastMCP instance."""

    @mcp.tool()
    async def categorize_risks(
        ticker: str,
        form_type: Literal["10-Q", "10-K", "20-F"] = "10-K",
    ) -> CategorizeRisksOutput:
        """
        Know exactly which kinds of risk a company is exposed to — ranked, not buried in legal text.

        A 10-K's Risk Factors section can run 30+ pages of dense, repetitive
        legal language. This tool reads it once and sorts every disclosed
        risk into one of ten standardized domains, so you can answer "is
        this a cybersecurity story or a litigation story?" in seconds
        instead of skimming pages of boilerplate:

          1.  Financial & Liquidity Risk
          2.  Legal & Regulatory Risk
          3.  Cybersecurity & Data Risk
          4.  Operational Risk
          5.  Market & Competitive Risk
          6.  Macroeconomic & Geopolitical Risk
          7.  Strategic & Execution Risk
          8.  Technology & Innovation Risk
          9.  ESG & Climate Risk
          10. Reputational & Brand Risk

        For each category you get the matched signals, a direct excerpt
        from the filing as evidence, a signal count, and a severity tier
        (1 = highest). An executive summary ranks categories by density,
        flags Tier 1 risks, and states the dominant risk theme in plain
        English — built for screening a watchlist or sector quickly.

        Built for: sector analysts comparing risk profiles across peers,
        ESG/compliance teams screening for specific exposure types, and
        due-diligence workflows that need a structured risk map rather
        than free text. Cached for 3-7 days.

        Use `compare_filings` if you want to see how risks changed between filings.
        Use `analyze_risk_trends` if you want a multi-year risk trajectory.

        Args:
            ticker:    US stock ticker symbol (e.g. AAPL, MSFT, TSLA, JPM).
            form_type: '10-K' (annual, recommended), '10-Q' (quarterly,
                       may incorporate by reference), or '20-F' (foreign
                       private issuer). Defaults to '10-K'.

        Returns:
            CategorizeRisksOutput with a list of RiskCategoryDetail objects
            ranked by signal count, plus an executive summary.
        """
        start = time.monotonic()

        ticker = ticker.upper().strip()
        if not ticker or not ticker.replace("-", "").replace(".", "").isalpha():
            raise ToolError(f"Invalid ticker: {ticker!r}.")
        if form_type not in ("10-Q", "10-K", "20-F"):
            raise ToolError("form_type must be '10-Q', '10-K', or '20-F'.")

        cache_key = make_cache_key("categorize_risks", ticker, form_type)
        cached = cache_get(cache_key)
        if cached:
            return CategorizeRisksOutput(**cached)

        try:
            result = await asyncio.wait_for(
                _run_categorizer_pipeline(ticker, form_type),
                timeout=TOOL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            return CategorizeRisksOutput(
                ticker=ticker, form_type=form_type,
                pipeline_success=False,
                failure_reason=f"Pipeline timed out after {TOOL_TIMEOUT}s",
                filing_date=None, accession_number=None,
                risk_section_char_count=0,
                categories=[],
                summary=None,
                elapsed_seconds=round(elapsed, 2),
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            return CategorizeRisksOutput(
                ticker=ticker, form_type=form_type,
                pipeline_success=False,
                failure_reason=f"Unexpected error: {exc}",
                filing_date=None, accession_number=None,
                risk_section_char_count=0,
                categories=[],
                summary=None,
                elapsed_seconds=round(elapsed, 2),
            )

        if result.pipeline_success:
            cache_set(cache_key, result.model_dump(), ticker=ticker, form_type=form_type, tool_name="categorize_risks")

        return result


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def _run_categorizer_pipeline(
    ticker: str, form_type: str,
) -> CategorizeRisksOutput:
    start = time.monotonic()

    filing = await fetch_one_filing(ticker, form_type)

    if not filing or not filing.fetch_success or not filing.html:
        return CategorizeRisksOutput(
            ticker=ticker, form_type=form_type,
            pipeline_success=False,
            failure_reason=f"Could not fetch {form_type} filing for {ticker}.",
            filing_date=None, accession_number=None,
            risk_section_char_count=0,
            categories=[],
            summary=None,
            elapsed_seconds=round(time.monotonic() - start, 2),
        )

    extraction = await extract_sections_cached(
        filing.html,
        accession=filing.accession_number,
        filing_date=filing.filing_date,
        form_type=form_type,
        document_url=filing.document_url,
    )

    rf = extraction.risk_factors
    if not rf.extraction_success or not rf.text:
        return CategorizeRisksOutput(
            ticker=ticker, form_type=form_type,
            pipeline_success=False,
            failure_reason=(
                f"Risk Factors section could not be extracted: {rf.failure_reason}"
            ),
            filing_date=filing.filing_date,
            accession_number=filing.accession_number,
            risk_section_char_count=rf.char_count,
            categories=[],
            summary=None,
            elapsed_seconds=round(time.monotonic() - start, 2),
        )

    categories = _categorize(rf.text)
    summary    = _build_exec_summary(ticker, form_type, filing.filing_date, categories, rf.text)

    return CategorizeRisksOutput(
        ticker=ticker, form_type=form_type,
        pipeline_success=True,
        failure_reason=None,
        filing_date=filing.filing_date,
        accession_number=filing.accession_number,
        risk_section_char_count=rf.char_count,
        categories=categories,
        summary=summary,
        elapsed_seconds=round(time.monotonic() - start, 2),
    )


# ---------------------------------------------------------------------------
# Categorization engine
# ---------------------------------------------------------------------------

def _categorize(risk_text: str) -> list[RiskCategoryDetail]:
    text_lower    = risk_text.lower()
    sentences     = _split_sentences(risk_text)
    results: list[RiskCategoryDetail] = []

    for domain_key, spec in RISK_TAXONOMY.items():
        matched_signals: list[str] = []
        excerpts: list[str]        = []
        seen_excerpts              = set()

        for kw in spec["keywords"]:
            if kw.lower() in text_lower:
                matched_signals.append(kw)
                for sent in sentences:
                    if kw.lower() in sent.lower() and sent not in seen_excerpts:
                        excerpts.append(sent[:250])
                        seen_excerpts.add(sent)
                        break

        if not matched_signals:
            continue

        results.append(RiskCategoryDetail(
            category=RiskCategory(domain_key),
            label=spec["label"],
            tier=spec["tier"],
            signal_count=len(matched_signals),
            matched_signals=matched_signals,
            excerpts=excerpts[:5],
        ))

    results.sort(key=lambda r: (r.tier, -r.signal_count))
    return results


def _split_sentences(text: str) -> list[str]:
    raw = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'(])', text)
    return [s.strip() for s in raw if len(s.strip()) >= 20]


# ---------------------------------------------------------------------------
# Executive summary builder
# ---------------------------------------------------------------------------

def _build_exec_summary(
    ticker: str,
    form_type: str,
    filing_date: str,
    categories: list[RiskCategoryDetail],
    full_text: str,
) -> RiskCategorizationSummary:
    total_signals = sum(c.signal_count for c in categories)
    tier1_cats    = [c for c in categories if c.tier == 1]
    top_cats      = categories[:5]

    parts = [
        f"Risk categorization for {ticker} ({form_type}, filed {filing_date}).",
        f"{len(categories)} risk domains identified across {total_signals} signal matches.",
    ]

    if tier1_cats:
        t1_names = ", ".join(c.label for c in tier1_cats)
        parts.append(f"Critical (Tier 1) domains present: {t1_names}.")

    if top_cats:
        ranked = ", ".join(f"{c.label} ({c.signal_count})" for c in top_cats)
        parts.append(f"Top domains by signal density: {ranked}.")

    if categories:
        top = categories[0]
        parts.append(
            f"Dominant risk theme: {top.label} with {top.signal_count} signals "
            f"({', '.join(top.matched_signals[:5])})."
        )

    return RiskCategorizationSummary(
        total_domains_identified=len(categories),
        total_signals=total_signals,
        tier1_domain_count=len(tier1_cats),
        top_domains=[c.category.value for c in top_cats],
        executive_summary=" ".join(parts),
    )