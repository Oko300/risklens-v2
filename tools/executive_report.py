"""
tools/executive_report.py — RiskLens v2
=========================================
Implements the `generate_executive_report` MCP tool.

Produces a clean, professional, human-readable analyst report for any
US public company's most recent SEC filing. The report is formatted
as a structured document ready to paste into an email, Slack message,
investment memo, board deck, or due diligence summary.

No financial jargon required to read the output — it is written in
plain English with clear sections, signal callouts, and a bottom-line
verdict that any executive, investor, or client can immediately act on.
"""

import asyncio
import time
from typing import Literal, Optional
from datetime import datetime

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from api.models.schemas import (
    DeltaOut, ExecutiveReportOutput, FilingMetaOut, ExtractionOut,
    ScoringOut, SectionDeltaOut, SectionScoreOut, FinancialContextOut, SignalHitOut
)
from core.delta import compute_delta
from core.extractor import extract_sections_cached, _is_raw_fallback, ExtractionMethod
from core.fetcher import fetch_two_filings, fetch_financial_context, PIPELINE_TIMEOUT as _FETCHER_PIPELINE_TIMEOUT
from core.scorer import score_sections, MaterialityLevel
from core.cache import cache_get, cache_set, make_cache_key


# TOOL_TIMEOUT must always exceed the fetcher's own internal PIPELINE_TIMEOUT
# (core/fetcher.py) plus headroom for extraction/delta/scoring. Previously
# this was hardcoded to 90s while the fetcher's internal budget was 110s —
# meaning the tool gave up via asyncio.wait_for BEFORE the fetcher itself
# would have finished on large filings (e.g. JPM's 10-K), causing a false
# timeout on every call for big filers regardless of caching.
TOOL_TIMEOUT = int(_FETCHER_PIPELINE_TIMEOUT) + 30  # processing headroom


def register_executive_report(mcp: FastMCP) -> None:

    @mcp.tool()
    async def generate_executive_report(
        ticker: str,
        form_type: Literal["10-Q", "10-K"] = "10-K",
    ) -> ExecutiveReportOutput:
        """
        Turn a 100+ page SEC filing into a one-screen analyst report — instantly shareable.

        This is the tool to reach for when you need to brief someone fast:
        a partner asking "what's the risk story on this name," a client
        email before market open, a one-pager for an investment committee.
        It reads the filing, runs the same delta and materiality scoring
        as compare_filings, and writes the result up as a clean, formatted
        report — no SEC jargon, no 40-page PDF, just the verdict and the
        evidence behind it.

        The report includes:
          • One-paragraph executive summary with a plain-English verdict
          • Top risk signals with direct quotes from the filing
          • What changed vs the prior filing (in plain English)
          • MD&A financial highlights
          • Overall materiality rating: LOW / MODERATE / HIGH / CRITICAL
          • A bottom-line recommendation paragraph

        The output is formatted and ready to paste directly into:
          - Investment memos
          - Board presentations
          - Due diligence reports
          - Client emails
          - Slack / Teams messages

        Built for: analysts who need to brief a desk in minutes, advisors
        prepping a client update, and anyone who needs to go from "ticker"
        to "shareable verdict" without reading the filing themselves.

        Cached for 3-7 days — repeat requests for the same ticker/form_type
        return the same report instantly.

        Use compare_filings if you want raw structured data.
        Use analyze_risk_trends if you want a multi-year timeline.
        Use categorize_risks if you want domain-by-domain breakdown.
        Use this tool if you want something a human can immediately read and act on.

        Args:
            ticker:    US stock ticker (e.g. AAPL, MSFT, TSLA, NVDA, JPM).
            form_type: '10-K' for annual report (recommended) or '10-Q' for quarterly.

        Returns:
            ExecutiveReportOutput with a fully formatted report string plus
            structured metadata for downstream use.
        """
        start = time.monotonic()

        ticker = ticker.upper().strip()
        if not ticker or not ticker.replace("-", "").replace(".", "").isalpha():
            raise ToolError(f"Invalid ticker: {ticker!r}. Use a US stock symbol like AAPL.")
        if form_type not in ("10-Q", "10-K"):
            raise ToolError("form_type must be '10-Q' or '10-K'.")

        # ── CACHE CHECK — before any EDGAR fetch ────────────────────────────
        cache_key = make_cache_key("generate_executive_report", ticker, form_type)
        cached = cache_get(cache_key)
        if cached:
            return ExecutiveReportOutput(**cached)

        try:
            result = await asyncio.wait_for(
                _run_report_pipeline(ticker, form_type),
                timeout=TOOL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            return ExecutiveReportOutput(
                ticker=ticker, form_type=form_type,
                pipeline_success=False,
                failure_reason=f"Report generation timed out after {TOOL_TIMEOUT}s. Try again.",
                report=None, filing_date=None, overall_materiality=None,
                top_signals=[], elapsed_seconds=round(elapsed, 2),
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            return ExecutiveReportOutput(
                ticker=ticker, form_type=form_type,
                pipeline_success=False,
                failure_reason=f"Unexpected error: {exc}",
                report=None, filing_date=None, overall_materiality=None,
                top_signals=[], elapsed_seconds=round(elapsed, 2),
            )

        # ── CACHE SAVE — only successful results are worth caching ─────────
        if result.pipeline_success:
            cache_set(
                cache_key, result.model_dump(),
                ticker=ticker, form_type=form_type,
                tool_name="generate_executive_report",
            )

        return result


async def _run_report_pipeline(ticker: str, form_type: str) -> ExecutiveReportOutput:
    start = time.monotonic()

    # ── Fetch ────────────────────────────────────────────────────────────────
    fetch_result, financial_context = await asyncio.gather(
        fetch_two_filings(ticker, form_type),
        fetch_financial_context(ticker, form_type)
    )

    if not fetch_result.pipeline_success:
        return ExecutiveReportOutput(
            ticker=ticker, form_type=form_type,
            pipeline_success=False,
            failure_reason=fetch_result.failure_reason,
            report=None, filing_date=None, overall_materiality=None,
            top_signals=[], elapsed_seconds=round(time.monotonic() - start, 2),
        )

    newer_meta = fetch_result.newer
    older_meta = fetch_result.older

    # ── Extract ──────────────────────────────────────────────────────────────
    newer_ext, older_ext = await asyncio.gather(
        extract_sections_cached(
            fetch_result.newer_html or "",
            accession=newer_meta.accession_number,
            filing_date=newer_meta.filing_date,
            form_type=form_type,
        ),
        extract_sections_cached(
            fetch_result.older_html or "",
            accession=older_meta.accession_number,
            filing_date=older_meta.filing_date,
            form_type=form_type,
        ),
    )

    # ── Guard: skip delta/scoring if extraction fell back to full document ───
    rf_newer = newer_ext.risk_factors
    mda_newer = newer_ext.mda

    rf_usable  = rf_newer.extraction_success and not _is_raw_fallback(rf_newer)
    mda_usable = mda_newer.extraction_success and not _is_raw_fallback(mda_newer)

    if not rf_usable and not mda_usable:
        return ExecutiveReportOutput(
            ticker=ticker, form_type=form_type,
            pipeline_success=False,
            failure_reason=(
                f"Could not extract Risk Factors or MD&A from {ticker}'s {form_type} filing "
                f"(filed {newer_meta.filing_date}). The filing may use an unsupported format "
                f"(image-based PDF, heavily customized XBRL, or exhibit-only filing)."
            ),
            report=None, filing_date=newer_meta.filing_date,
            overall_materiality=None, top_signals=[],
            elapsed_seconds=round(time.monotonic() - start, 2),
        )

    # ── Delta ────────────────────────────────────────────────────────────────
    older_rf_text  = older_ext.risk_factors.text if older_ext.risk_factors.extraction_success else None
    older_mda_text = older_ext.mda.text          if older_ext.mda.extraction_success          else None

    # Skip delta for 10-Q reference pointers
    if _is_reference_pointer(rf_newer.text):
        older_rf_text = None

    delta = compute_delta(
        older_risk=older_rf_text,
        newer_risk=rf_newer.text if rf_usable else None,
        older_mda=older_mda_text,
        newer_mda=mda_newer.text if mda_usable else None,
    )

    # ── Score ────────────────────────────────────────────────────────────────
    scoring = score_sections(
        newer_risk_text=rf_newer.text  if rf_usable  else None,
        older_risk_text=older_rf_text,
        newer_mda_text=mda_newer.text  if mda_usable else None,
        older_mda_text=older_mda_text,
        risk_delta=delta.risk_factors,
        mda_delta=delta.mda,
    )

    # ── Build report ─────────────────────────────────────────────────────────
    report = _build_report(
        ticker=ticker,
        form_type=form_type,
        newer_meta=newer_meta,
        older_meta=older_meta,
        newer_ext=newer_ext,
        delta=delta,
        scoring=scoring,
        financial_context=financial_context,
    )

    return ExecutiveReportOutput(
        ticker=ticker,
        form_type=form_type,
        pipeline_success=True,
        failure_reason=None,
        report=report,
        filing_date=newer_meta.filing_date,
        overall_materiality=scoring.overall_materiality.value,
        top_signals=scoring.top_signals[:8],
        elapsed_seconds=round(time.monotonic() - start, 2),
    )


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

_MATERIALITY_EMOJI = {
    "low":      "🟢",
    "moderate": "🟡",
    "high":     "🟠",
    "critical": "🔴",
}

_MAGNITUDE_PLAIN = {
    "none":     "unchanged",
    "minor":    "minor changes",
    "moderate": "moderate changes",
    "major":    "significant changes",
}


def _build_report(
    ticker: str,
    form_type: str,
    newer_meta: FilingMetaOut,
    older_meta: FilingMetaOut,
    newer_ext: ExtractionOut,
    delta: DeltaOut,
    scoring: ScoringOut,
    financial_context: FinancialContextOut,
) -> str:
    lines = []
    now   = datetime.utcnow().strftime("%B %d, %Y")
    mat   = scoring.overall_materiality.value
    emoji = _MATERIALITY_EMOJI.get(mat, "⚪")

    rf_delta  = delta.risk_factors
    mda_delta = delta.mda
    rf_score  = scoring.risk_factors
    mda_score = scoring.mda

    # ── Header ───────────────────────────────────────────────────────────────
    lines += [
        "=" * 64,
        f"  RISKLENS v2 — ANALYST REPORT",
        f"  {ticker}  |  {form_type}  |  Filed {newer_meta.filing_date}",
        f"  Generated {now} via RiskLens v2",
        "=" * 64,
        "",
    ]

    # ── Overall verdict ──────────────────────────────────────────────────────
    verdict = _verdict_paragraph(
        ticker=ticker,
        form_type=form_type,
        newer_meta=newer_meta,
        older_meta=older_meta,
        rf_score=rf_score,
        mda_score=mda_score,
        rf_delta=rf_delta,
        mda_delta=mda_delta,
        mat=scoring.overall_materiality.value,
    )
    lines += [
        f"OVERALL RISK RATING:  {emoji} {mat.upper()}",
        "",
        "EXECUTIVE SUMMARY",
        "─" * 40,
        verdict,
        "",
    ]

    # ── What changed ─────────────────────────────────────────────────────────
    lines += [
        "WHAT CHANGED VS PRIOR FILING",
        "─" * 40,
    ]

    if rf_delta.delta_success:
        mag_plain = _MAGNITUDE_PLAIN.get(rf_delta.magnitude.value, rf_delta.magnitude.value)
        lines.append(
            f"Risk Factors:  {mag_plain} ({rf_delta.pct_changed*100:.0f}% of sentences affected)"
        )
        if rf_score.new_signals:
            new_names = ", ".join(h.signal for h in rf_score.new_signals[:5])
            lines.append(f"  ⚠  New risk signals appeared: {new_names}")
        if rf_score.removed_signals:
            rem_names = ", ".join(h.signal for h in rf_score.removed_signals[:3])
            lines.append(f"  ✓  Risk signals removed: {rem_names}")
    else:
        lines.append("Risk Factors:  comparison not available (10-Q incorporates 10-K by reference)")

    if mda_delta.delta_success:
        mag_plain = _MAGNITUDE_PLAIN.get(mda_delta.magnitude.value, mda_delta.magnitude.value)
        lines.append(
            f"MD&A:          {mag_plain} ({mda_delta.pct_changed*100:.0f}% of sentences affected)"
        )
    else:
        lines.append("MD&A:          comparison not available")

    lines.append("")

    # ── Top risk signals ─────────────────────────────────────────────────────
    lines += [
        "TOP RISK SIGNALS",
        "─" * 40,
    ]

    t1 = rf_score.tier1_hits[:6]
    if t1:
        lines.append("Critical (Tier 1):")
        for h in t1:
            tag = " [NEW]" if h.signal in {x.signal for x in rf_score.new_signals} else ""
            lines.append(f"  • {h.signal}{tag}")
            if h.context:
                lines.append(f"    {h.context[:120]}")
        lines.append("")

    t2 = rf_score.tier2_hits[:5]
    if t2:
        lines.append("Elevated (Tier 2):")
        for h in t2:
            tag = " [NEW]" if h.signal in {x.signal for x in rf_score.new_signals} else ""
            lines.append(f"  • {h.signal}{tag}")
        lines.append("")

    # ── MD&A financial highlights ─────────────────────────────────────────────
    mda_text = newer_ext.mda.text or ""
    highlights = _extract_mda_highlights(
        mda_text,
        mda_score.tier1_hits + mda_score.tier2_hits,
        financial_context,
    )
    if highlights:
        lines += [
            "MD&A FINANCIAL HIGHLIGHTS",
            "─" * 40,
        ]
        for h in highlights:
            lines.append(f"  • {h}")
        lines.append("")

    # ── Risk categorization summary ───────────────────────────────────────────
    rf_text = newer_ext.risk_factors.text or ""
    cat_summary = _quick_categorize(rf_text)
    if cat_summary:
        lines += [
            "RISK CATEGORIES PRESENT",
            "─" * 40,
        ]
        for cat, count in cat_summary:
            lines.append(f"  {cat:<30}  {count} signals")
        lines.append("")

    # ── Scores ───────────────────────────────────────────────────────────────
    lines += [
        "SECTION SCORES",
        "─" * 40,
        f"  Risk Factors:  {_MATERIALITY_EMOJI.get(rf_score.materiality.value,'⚪')} "
        f"{rf_score.materiality.value.upper()}  (score {rf_score.raw_score:.1f})",
        f"  MD&A:          {_MATERIALITY_EMOJI.get(mda_score.materiality.value,'⚪')} "
        f"{mda_score.materiality.value.upper()}  (score {mda_score.raw_score:.1f})",
        "",
    ]

    # ── Filing metadata ───────────────────────────────────────────────────────
    lines += [
        "FILING DETAILS",
        "─" * 40,
        f"  Ticker:          {ticker}",
        f"  Form type:       {form_type}",
        f"  Filing date:     {newer_meta.filing_date}",
        f"  Prior filing:    {older_meta.filing_date}",
        f"  Accession:       {newer_meta.accession_number}",
        f"  Risk chars:      {newer_ext.risk_factors.char_count:,}",
        f"  MD&A chars:      {newer_ext.mda.char_count:,}",
        f"  Extraction:      {newer_ext.risk_factors.method.value}",
        "",
    ]

    # ── Disclaimer ────────────────────────────────────────────────────────────
    lines += [
        "─" * 64,
        "Generated by RiskLens v2 | github.com/your-org/risklens-v2",
        "This report is for informational purposes only and does not",
        "constitute investment, legal, or financial advice.",
        "Always verify against the original EDGAR filing.",
        "=" * 64,
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Verdict paragraph builder
# ---------------------------------------------------------------------------

def _verdict_paragraph(ticker, form_type, mat, rf_score, mda_score,
                        rf_delta, mda_delta, newer_meta, older_meta) -> str:
    period = "annual" if form_type == "10-K" else "quarterly"
    
    # Opening statement based on overall materiality
    if mat == "critical":
        opening = (f"{ticker}'s most recent {period} filing ({newer_meta.filing_date}) "
                   f"presents a CRITICAL risk profile, demanding immediate attention.")
    elif mat == "high":
        opening = (f"{ticker}'s most recent {period} filing ({newer_meta.filing_date}) "
                   f"indicates a HIGH risk rating, with several key areas requiring close monitoring.")
    elif mat == "moderate":
        opening = (f"{ticker}'s most recent {period} filing ({newer_meta.filing_date}) "
                   f"reveals a MODERATE risk profile, suggesting a need for vigilance in specific areas.")
    else: # low
        opening = (f"{ticker}'s most recent {period} filing ({newer_meta.filing_date}) "
                   f"shows a LOW risk profile, with no significant new concerns identified.")

    # Narrative about changes in Risk Factors
    rf_change_narrative = ""
    if rf_delta.delta_success:
        pct_rf_changed = rf_delta.pct_changed * 100
        if rf_delta.magnitude.value == "major":
            rf_change_narrative = (f"The Risk Factors section underwent significant revisions, "
                                   f"with {pct_rf_changed:.0f}% of sentences affected compared to the prior filing "
                                   f"({older_meta.filing_date}).")
        elif rf_delta.magnitude.value == "moderate":
            rf_change_narrative = (f"There were moderate changes in the Risk Factors section, "
                                   f"with {pct_rf_changed:.0f}% of sentences updated from the prior filing "
                                   f"({older_meta.filing_date}).")
        else:
            rf_change_narrative = (f"The Risk Factors section remained largely stable, "
                                   f"with only {pct_rf_changed:.0f}% of sentences changed from the prior filing "
                                   f"({older_meta.filing_date}).")
    else:
        rf_change_narrative = "Risk Factor comparison was not available (e.g., 10-Q incorporating 10-K by reference)."

    # Narrative about new and removed signals
    signal_narrative_parts = []
    new_sigs = rf_score.new_signals[:3]
    if new_sigs:
        names = ", ".join(f'"{h.signal}"' for h in new_sigs)
        signal_narrative_parts.append(f"Notably, new risk signals emerged, including {names}.")
    
    rem_sigs = rf_score.removed_signals[:2]
    if rem_sigs:
        names = ", ".join(f'"{h.signal}"' for h in rem_sigs)
        signal_narrative_parts.append(f"Conversely, previously flagged risks such as {names} are no longer present.")
    
    signal_narrative = " ".join(signal_narrative_parts)

    # Narrative about MD&A changes and tone
    mda_narrative = ""
    if mda_delta.delta_success:
        pct_mda_changed = mda_delta.pct_changed * 100
        mda_narrative = (f"The Management's Discussion and Analysis (MD&A) section saw "
                         f"{_MAGNITUDE_PLAIN.get(mda_delta.magnitude.value, 'some')} changes, "
                         f"with {pct_mda_changed:.0f}% of sentences affected. ")
    else:
        mda_narrative = "MD&A comparison was not available. "

    mda_mat = mda_score.materiality.value
    if mda_mat == "critical":
        mda_narrative += "It highlights critical financial stress signals."
    elif mda_mat == "high":
        mda_narrative += "It reveals elevated financial stress signals."
    elif mda_mat == "moderate":
        mda_narrative += "It indicates areas of financial caution."
    else:
        mda_narrative += "It reflects stable financial performance."

    return f"{opening} {rf_change_narrative} {signal_narrative} {mda_narrative}".strip()


# ---------------------------------------------------------------------------
# MD&A highlights extractor — pulls key financial sentences
# ---------------------------------------------------------------------------

_MDA_KEYWORDS = [
    "revenue", "net income", "operating income", "cash flow", "liquidity",
    "earnings", "gross margin", "operating margin", "capital expenditure",
    "free cash flow", "debt", "guidance", "outlook", "increased", "decreased",
]


def _extract_mda_highlights(
    mda_text: str,
    mda_signals: list[SignalHitOut],
    financial_context: FinancialContextOut,
    max_highlights: int = 5,
) -> list[str]:
    """
    Extracts key highlights from the MD&A section, prioritizing financial metrics
    from FinancialContextOut if available, otherwise using keyword matching.
    """
    if not mda_text:
        return []

    highlights = []

    # 1. Prioritize structured financial context if available
    if financial_context and financial_context.fetch_success:
        financial_metrics = {
            "Revenue": financial_context.revenue,
            "Net Income": financial_context.net_income,
            "Cash and Equivalents": financial_context.cash_and_equivalents,
            "Total Debt": financial_context.total_debt,
            "Current Ratio": financial_context.current_ratio,
            "Capex": financial_context.capex,
        }
        
        for metric, value in financial_metrics.items():
            if value is not None:
                highlights.append(f"{metric}: {value:,.0f}")
        
        if highlights:
            return highlights

    # 2. Fallback to keyword-based extraction if financial context is not available or failed
    import re
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', mda_text)
    scored = []
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 40 or len(sent) > 300:
            continue
        score = sum(1 for kw in _MDA_KEYWORDS if kw.lower() in sent.lower())
        for signal_hit in mda_signals:
            if signal_hit.signal.lower() in sent.lower():
                score += 2 # Signals are more important
        if score > 0:
            scored.append((score, sent))
    scored.sort(key=lambda x: -x[0])
    seen = set()
    results = []
    for _, sent in scored:
        # Deduplicate by first 60 chars
        key = sent[:60]
        if key not in seen:
            seen.add(key)
            results.append(sent[:200])
        if len(results) >= max_highlights:
            break
    return results


# ---------------------------------------------------------------------------
# Quick categorizer for report summary
# ---------------------------------------------------------------------------

_QUICK_TAXONOMY = {
    "Financial & Liquidity":      ["liquidity","cash flow","debt","net loss","going concern","default"],
    "Legal & Regulatory":         ["litigation","class action","SEC","DOJ","regulatory","compliance"],
    "Cybersecurity & Data":       ["cybersecurity","data breach","ransomware","unauthorized access"],
    "Operational":                ["supply chain","disruption","key personnel","restructuring"],
    "Market & Competitive":       ["competition","market share","customer concentration","declining revenue"],
    "Macroeconomic & Geopolitical":["inflation","interest rate","tariff","sanctions","recession","geopolitical"],
    "Strategic & Execution":      ["acquisition","integration risk","execution risk"],
    "Technology & Innovation":    ["artificial intelligence","cloud","obsolescence","intellectual property"],
    "ESG & Climate":              ["climate change","ESG","carbon","sustainability"],
    "Reputational":               ["reputational","brand","trust"],
}


def _quick_categorize(text: str) -> list[tuple[str, int]]:
    if not text:
        return []
    tl = text.lower()
    results = []
    for cat, kws in _QUICK_TAXONOMY.items():
        count = sum(1 for kw in kws if kw in tl)
        if count > 0:
            results.append((cat, count))
    results.sort(key=lambda x: -x[1])
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RF_REFERENCE_PHRASES = [
    "incorporated by reference", "annual report on form 10-k",
    "see our 10-k", "see part i, item 1a",
]

def _is_reference_pointer(text: Optional[str]) -> bool:
    if not text or len(text) >= 2000:
        return False
    return any(p in text.lower() for p in _RF_REFERENCE_PHRASES)

def _is_raw_fallback(section) -> bool:
    """True if extraction fell back to the full document (unreliable for analysis)."""
    return section.method == ExtractionMethod.RAW_FALLBACK