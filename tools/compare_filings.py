"""
tools/compare_filings.py — RiskLens v2
=========================================
Implements the `compare_filings` MCP tool.

Compares the two most recent 10-Q, 10-K, or 20-F filings for any US (or
foreign-private-issuer) public company. Runs the full pipeline: EDGAR
fetch → section extraction → sentence-level delta → materiality scoring.

PERFORMANCE: the two filings are fetched concurrently (core/fetcher.py),
and results are cached for 3-7 days (core/cache.py) — repeat queries for
the same ticker/form_type return instantly without re-hitting EDGAR.
"""

import asyncio
import time
from typing import Literal, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from core.fetcher    import fetch_two_filings
from core.extractor  import extract_sections_cached
from core.delta      import compute_delta
from core.scorer     import score_sections
from core.cache      import cache_get, cache_set, make_cache_key
from schemas         import (
    CompareFilingsOutput,
    FilingMetaOut,
    ExtractionOut,
    SectionExtractionOut,
    DeltaOut,
    SectionDeltaOut,
    ChangeOut,
    ScoringOut,
    SectionScoreOut,
    SignalHitOut,
)

TOOL_TIMEOUT = 100

MDA_MIN_CHARS              = 5_000
RISK_FACTORS_MIN_CHARS_10K = 2_000

_RF_REFERENCE_PHRASES = [
    "incorporated by reference",
    "annual report on form 10-k",
    "our annual report",
    "refer to part i, item 1a",
]
_RF_REFERENCE_CHAR_THRESHOLD = 3_000

_COVERAGE_GAP = (
    "RiskLens v2 analyzes 10-K, 10-Q, and 20-F filings for the core risk pipeline. "
    "Only Risk Factors and MD&A (or 20-F equivalents) are compared. "
    "Only the two most recent filings are compared per call. "
    "Results are cached for 3-7 days; cached responses return instantly. "
    "Many 10-Q filings incorporate Risk Factors by reference from the annual 10-K — "
    "use form_type='10-K' for annual risk factor comparisons."
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_compare_filings(mcp: FastMCP) -> None:
    """Register the compare_filings tool onto a FastMCP instance."""

    @mcp.tool()
    async def compare_filings(
        ticker: str,
        form_type: Literal["10-Q", "10-K", "20-F"] = "10-Q",
    ) -> CompareFilingsOutput:
        """
        Compare the two most recent SEC filings for a public company.

        Fetches the two most recent 10-K, 10-Q, or 20-F (foreign private
        issuer annual report) filings directly from EDGAR, extracts Risk
        Factors and MD&A (or the 20-F equivalents: Item 3.D and Item 5),
        runs a sentence-level diff between them, and scores each section
        for materiality using a tiered financial signal library.

        Results are cached for 3-7 days — a repeat call for the same
        ticker/form_type returns instantly instead of re-fetching EDGAR.

        Risk Factors note
        -----------------
        Most S&P 500 10-Q filings incorporate Risk Factors by reference from
        the annual 10-K. When detected the tool flags it explicitly and skips
        the delta rather than producing a misleading comparison. Use
        form_type='10-K' for reliable annual risk factor comparisons.

        Args:
            ticker:    US stock ticker symbol (e.g. AAPL, MSFT, TSLA, JPM).
            form_type: '10-Q' (quarterly), '10-K' (annual), or '20-F'
                       (foreign private issuer annual report, e.g. TSM, BABA, ASML).
                       Defaults to '10-Q'.

        Returns:
            CompareFilingsOutput with nested filing metadata, extraction
            diagnostics, sentence-level delta, and materiality scores for
            both sections.
        """
        start = time.monotonic()

        ticker = ticker.upper().strip()
        if not ticker or not ticker.replace("-", "").replace(".", "").isalpha():
            raise ToolError(f"Invalid ticker: {ticker!r}. Must be alphabetic (e.g. AAPL).")
        if form_type not in ("10-Q", "10-K", "20-F"):
            raise ToolError("form_type must be '10-Q', '10-K', or '20-F'.")

        cache_key = make_cache_key("compare_filings", ticker, form_type)
        cached = cache_get(cache_key)
        if cached:
            return CompareFilingsOutput(**cached)

        try:
            result = await asyncio.wait_for(
                _run_pipeline(ticker, form_type),
                timeout=TOOL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            return _build_output(
                ticker=ticker, form_type=form_type,
                pipeline_success=False,
                failure_reason=(
                    f"Pipeline timed out after {TOOL_TIMEOUT}s. Large filings (e.g. JPM, BAC) "
                    f"can take longer on first fetch — try again, the result will be cached."
                ),
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            return _build_output(
                ticker=ticker, form_type=form_type,
                pipeline_success=False,
                failure_reason=f"Unexpected pipeline error: {exc}",
                elapsed_seconds=elapsed,
            )

        if result.pipeline_success:
            cache_set(cache_key, result.model_dump(), ticker=ticker, form_type=form_type, tool_name="compare_filings")

        return result


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def _run_pipeline(ticker: str, form_type: str) -> CompareFilingsOutput:
    start = time.monotonic()

    fetch_result = await fetch_two_filings(ticker, form_type)

    if not fetch_result.pipeline_success:
        return _build_output(
            ticker=ticker, form_type=form_type,
            pipeline_success=False,
            failure_reason=fetch_result.failure_reason,
            newer_meta=fetch_result.newer,
            older_meta=fetch_result.older,
            elapsed_seconds=time.monotonic() - start,
        )

    newer_extraction, older_extraction = await asyncio.gather(
        extract_sections_cached(
            fetch_result.newer_html or "",
            accession=fetch_result.newer.accession_number if fetch_result.newer else "",
            filing_date=fetch_result.newer.filing_date   if fetch_result.newer else "",
            form_type=form_type,
            document_url=fetch_result.newer.document_url if fetch_result.newer else "",
        ),
        extract_sections_cached(
            fetch_result.older_html or "",
            accession=fetch_result.older.accession_number if fetch_result.older else "",
            filing_date=fetch_result.older.filing_date   if fetch_result.older else "",
            form_type=form_type,
            document_url=fetch_result.older.document_url if fetch_result.older else "",
        ),
    )

    sanity_failure = _check_extraction_sanity(newer_extraction, older_extraction, form_type)
    if sanity_failure:
        return _build_output(
            ticker=ticker, form_type=form_type,
            pipeline_success=False, failure_reason=sanity_failure,
            newer_meta=fetch_result.newer, older_meta=fetch_result.older,
            newer_extraction=newer_extraction, older_extraction=older_extraction,
            elapsed_seconds=time.monotonic() - start,
        )

    rf_pointer_note = None
    if form_type == "10-Q":
        newer_rf = newer_extraction.risk_factors
        if _is_rf_reference_pointer(newer_rf.text, newer_rf.char_count):
            rf_pointer_note = (
                "REFERENCE POINTER DETECTED: This 10-Q's Risk Factors section "
                f"({newer_rf.char_count} chars) incorporates the Annual Report (10-K) "
                "by reference. No meaningful quarter-over-quarter Risk Factor comparison "
                "is possible. Use form_type='10-K' to compare annual Risk Factor disclosures."
            )
            newer_extraction.risk_factors.coverage_gap_note = rf_pointer_note
            if older_extraction.risk_factors.coverage_gap_note is None:
                older_extraction.risk_factors.coverage_gap_note = rf_pointer_note

    older_rf_text = None if rf_pointer_note else older_extraction.risk_factors.text
    newer_rf_text = None if rf_pointer_note else newer_extraction.risk_factors.text

    delta_result = compute_delta(
        older_risk=older_rf_text,
        newer_risk=newer_rf_text,
        older_mda=older_extraction.mda.text,
        newer_mda=newer_extraction.mda.text,
    )

    scoring_result = score_sections(
        newer_risk_text=newer_extraction.risk_factors.text,
        older_risk_text=older_extraction.risk_factors.text,
        newer_mda_text=newer_extraction.mda.text,
        older_mda_text=older_extraction.mda.text,
        risk_delta=delta_result.risk_factors,
        mda_delta=delta_result.mda,
    )

    return _build_output(
        ticker=ticker, form_type=form_type,
        pipeline_success=fetch_result.pipeline_success,
        failure_reason=rf_pointer_note if rf_pointer_note else None,
        newer_meta=fetch_result.newer, older_meta=fetch_result.older,
        newer_extraction=newer_extraction, older_extraction=older_extraction,
        delta_result=delta_result, scoring_result=scoring_result,
        elapsed_seconds=time.monotonic() - start,
    )


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------

def _is_rf_reference_pointer(text: Optional[str], char_count: int) -> bool:
    if char_count >= _RF_REFERENCE_CHAR_THRESHOLD:
        return False
    if not text:
        return False
    return any(phrase in text.lower() for phrase in _RF_REFERENCE_PHRASES)


def _check_extraction_sanity(newer_extraction, older_extraction, form_type: str) -> Optional[str]:
    issues = []
    for label, extraction in [("newer", newer_extraction), ("older", older_extraction)]:
        rf  = extraction.risk_factors
        mda = extraction.mda
        method_val = mda.method.value if hasattr(mda.method, "value") else mda.method
        if not mda.extraction_success:
            issues.append(
                f"{label} MD&A: extraction failed "
                f"(method={method_val}, {mda.char_count} chars) — full document used as fallback."
            )
        elif mda.char_count < MDA_MIN_CHARS:
            issues.append(
                f"{label} MD&A: only {mda.char_count} chars extracted "
                f"(minimum {MDA_MIN_CHARS}) — likely a fragment."
            )
        if form_type in ("10-K", "20-F"):
            rf_method_val = rf.method.value if hasattr(rf.method, "value") else rf.method
            if not rf.extraction_success:
                issues.append(
                    f"{label} Risk Factors: extraction failed "
                    f"(method={rf_method_val}, {rf.char_count} chars)."
                )
            elif rf.char_count < RISK_FACTORS_MIN_CHARS_10K:
                issues.append(
                    f"{label} Risk Factors: only {rf.char_count} chars extracted "
                    f"(minimum {RISK_FACTORS_MIN_CHARS_10K})."
                )
    if issues:
        return (
            "Extraction quality below threshold — comparison unreliable. "
            + " | ".join(issues)
        )
    return None


# ---------------------------------------------------------------------------
# Output builder
# ---------------------------------------------------------------------------

def _build_output(
    ticker: str,
    form_type: str,
    pipeline_success: bool,
    failure_reason: Optional[str],
    newer_meta=None,
    older_meta=None,
    newer_extraction=None,
    older_extraction=None,
    delta_result=None,
    scoring_result=None,
    elapsed_seconds: float = 0.0,
) -> CompareFilingsOutput:

    def meta_out(m) -> Optional[FilingMetaOut]:
        if m is None:
            return None
        return FilingMetaOut(
            ticker=m.ticker, cik=m.cik, form_type=m.form_type,
            accession_number=m.accession_number, filing_date=m.filing_date,
            report_date=m.report_date, document_url=m.document_url,
            fetch_success=m.fetch_success, failure_reason=m.failure_reason,
            html_byte_length=m.html_byte_length,
        )

    def section_extraction_out(s) -> SectionExtractionOut:
        return SectionExtractionOut(
            section_name=s.section_name, item_label=s.item_label,
            extraction_success=s.extraction_success,
            method=s.method.value if hasattr(s.method, "value") else s.method,
            confidence_score=s.confidence_score, char_count=s.char_count,
            failure_reason=s.failure_reason, coverage_gap_note=s.coverage_gap_note,
            source_reference=getattr(s, "source_reference", None),
        )

    def extraction_out(e) -> Optional[ExtractionOut]:
        if e is None:
            return None
        return ExtractionOut(
            filing_accession=e.filing_accession, filing_date=e.filing_date,
            risk_factors=section_extraction_out(e.risk_factors),
            mda=section_extraction_out(e.mda),
            full_doc_char_count=e.full_doc_char_count,
            known_gaps=e.known_gaps,
            both_succeeded=e.both_succeeded,
            any_succeeded=e.any_succeeded,
        )

    def section_delta_out(sd) -> Optional[SectionDeltaOut]:
        if sd is None:
            return None
        top_changes = []
        for c in sd.changes[:50]:
            if c.change_type in ("added", "removed", "rewritten"):
                top_changes.append(ChangeOut(
                    type=c.change_type,
                    older=(c.older_text or "")[:300],
                    newer=(c.newer_text or "")[:300],
                    similarity=c.similarity,
                ))
        return SectionDeltaOut(
            section_name=sd.section_name,
            magnitude=sd.magnitude.value,
            total_older_sentences=sd.total_older_sentences,
            total_newer_sentences=sd.total_newer_sentences,
            added_count=sd.added_count, removed_count=sd.removed_count,
            rewritten_count=sd.rewritten_count, unchanged_count=sd.unchanged_count,
            pct_changed=sd.pct_changed, delta_success=sd.delta_success,
            failure_reason=sd.failure_reason, top_changes=top_changes,
        )

    def delta_out(d) -> Optional[DeltaOut]:
        if d is None:
            return None
        return DeltaOut(
            risk_factors=section_delta_out(d.risk_factors),
            mda=section_delta_out(d.mda),
            comparison_note=d.comparison_note,
        )

    def signal_hit_out(h) -> SignalHitOut:
        return SignalHitOut(
            signal=h.signal, tier=h.tier, weight=h.weight,
            in_change=h.in_change, context=h.context[:200],
        )

    def section_score_out(ss) -> SectionScoreOut:
        return SectionScoreOut(
            section_name=ss.section_name,
            materiality=ss.materiality.value,
            raw_score=ss.raw_score, is_estimate=ss.is_estimate,
            analyst_note=ss.analyst_note,
            tier1_hits=[signal_hit_out(h) for h in ss.tier1_hits],
            tier2_hits=[signal_hit_out(h) for h in ss.tier2_hits[:10]],
            new_signals=[signal_hit_out(h) for h in ss.new_signals],
            removed_signals=[signal_hit_out(h) for h in ss.removed_signals],
        )

    def scoring_out(s) -> Optional[ScoringOut]:
        if s is None:
            return None
        return ScoringOut(
            risk_factors=section_score_out(s.risk_factors),
            mda=section_score_out(s.mda),
            overall_materiality=s.overall_materiality.value,
            top_signals=s.top_signals,
            scoring_success=s.scoring_success,
            failure_reason=s.failure_reason,
        )

    return CompareFilingsOutput(
        schema_version="2.1",
        tool="compare_filings",
        ticker=ticker,
        form_type=form_type,
        pipeline_success=pipeline_success,
        failure_reason=failure_reason,
        elapsed_seconds=round(elapsed_seconds, 2),
        newer_filing=meta_out(newer_meta),
        older_filing=meta_out(older_meta),
        newer_extraction=extraction_out(newer_extraction),
        older_extraction=extraction_out(older_extraction),
        delta=delta_out(delta_result),
        scoring=scoring_out(scoring_result),
        coverage_gap_disclosure=_COVERAGE_GAP,
    )
