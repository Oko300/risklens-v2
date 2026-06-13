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

from core.cache      import cache_get, cache_set, make_cache_key
from core.fetcher    import fetch_two_filings
from core.extractor  import extract_sections_cached
from core.delta      import compute_delta
from core.scorer     import score_sections, MaterialityLevel
from schemas         import ExecutiveReportOutput


TOOL_TIMEOUT = 90


def register_executive_report(mcp: FastMCP) -> None:

    @mcp.tool()
    async def generate_executive_report(
        ticker: str,
        form_type: Literal["10-Q", "10-K"] = "10-K",
    ) -> ExecutiveReportOutput:
        """
        Generate a professional analyst report for any US public company SEC filing.

        This is the best tool to use when you want a complete, readable summary
        of a company's risk profile and financial narrative — not raw data.

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

        _ck = make_cache_key("generate_executive_report", ticker, form_type)
        _hit = cache_get(_ck)
        if _hit:
            from pydantic import TypeAdapter
            return TypeAdapter(ExecutiveReportOutput).validate_python(_hit)

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

        return result


async def _run_report_pipeline(ticker: str, form_type: str) -> ExecutiveReportOutput:
    start = time.monotonic()

    # ── Fetch ────────────────────────────────────────────────────────────────
    fetch_result = await fetch_two_filings(ticker, form_type)

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
    ticker, form_type, newer_meta, older_meta,
    newer_ext, delta, scoring,
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
    verdict = _verdict_paragraph(ticker, form_type, mat, rf_score, mda_score,
                                  rf_delta, mda_delta, newer_meta, older_meta)
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
    highlights = _extract_mda_highlights(mda_text)
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

    # Opening
    if mat == "critical":
        opening = (f"{ticker}'s most recent {period} filing ({newer_meta.filing_date}) "
                   f"carries a CRITICAL risk rating.")
    elif mat == "high":
        opening = (f"{ticker}'s most recent {period} filing ({newer_meta.filing_date}) "
                   f"carries a HIGH risk rating requiring close attention.")
    elif mat == "moderate":
        opening = (f"{ticker}'s most recent {period} filing ({newer_meta.filing_date}) "
                   f"shows a MODERATE risk profile with several areas to monitor.")
    else:
        opening = (f"{ticker}'s most recent {period} filing ({newer_meta.filing_date}) "
                   f"shows a LOW risk profile with no major new concerns identified.")

    # Change narrative
    change_parts = []
    if rf_delta.delta_success:
        mag = rf_delta.magnitude.value
        pct = rf_delta.pct_changed * 100
        if mag in ("major", "moderate"):
            change_parts.append(
                f"Risk Factors changed significantly from the prior filing "
                f"({older_meta.filing_date}), with {pct:.0f}% of sentences affected."
            )
        else:
            change_parts.append(
                f"Risk Factors were largely stable vs the prior filing ({older_meta.filing_date})."
            )

    # New signals
    new_sigs = rf_score.new_signals[:3]
    if new_sigs:
        names = ", ".join(f'"{h.signal}"' for h in new_sigs)
        change_parts.append(f"Notable new risk signals include {names}.")

    # Removed signals
    rem_sigs = rf_score.removed_signals[:2]
    if rem_sigs:
        names = ", ".join(f'"{h.signal}"' for h in rem_sigs)
        change_parts.append(f"Previously flagged risks no longer present: {names}.")

    # MD&A tone
    mda_mat = mda_score.materiality.value
    if mda_mat in ("high", "critical"):
        change_parts.append(
            "The MD&A section shows elevated financial stress signals."
        )
    elif mda_mat == "moderate":
        change_parts.append(
            "The MD&A section shows some areas of financial caution."
        )
    else:
        change_parts.append(
            "The MD&A section reflects stable financial performance."
        )

    return opening + " " + " ".join(change_parts)


# ---------------------------------------------------------------------------
# MD&A highlights extractor — pulls key financial sentences
# ---------------------------------------------------------------------------

_MDA_KEYWORDS = [
    "revenue", "net income", "operating income", "cash flow", "liquidity",
    "earnings", "gross margin", "operating margin", "capital expenditure",
    "free cash flow", "debt", "guidance", "outlook", "increased", "decreased",
]


def _extract_mda_highlights(text: str, max_highlights: int = 5) -> list[str]:
    if not text:
        return []
    import re
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    scored = []
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 40 or len(sent) > 300:
            continue
        score = sum(1 for kw in _MDA_KEYWORDS if kw.lower() in sent.lower())
        if score >= 2:
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
    from core.extractor import ExtractionMethod
    return section.method == ExtractionMethod.RAW_FALLBACK
