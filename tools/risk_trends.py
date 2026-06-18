"""
tools/risk_trends.py — RiskLens v2
====================================
Implements the `analyze_risk_trends` MCP tool.

Fetches up to N filings (default 4, max 8) and tracks how Risk Factors and
MD&A materially evolve over time. Returns a timeline of materiality scores,
signal appearances/disappearances, and a structured trend narrative.

Results are cached for 3-7 days — repeat calls return instantly.
"""

import asyncio
import time
from typing import Literal, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from core.fetcher   import fetch_n_filings, PIPELINE_TIMEOUT as _FETCHER_PIPELINE_TIMEOUT
from core.extractor import extract_sections_cached
from core.scorer    import score_sections
from core.cache     import cache_get, cache_set, make_cache_key
from schemas        import (
    RiskTrendsOutput,
    TrendPoint,
    TrendSignalAppearance,
    TrendSummary,
)


# TOOL_TIMEOUT must always exceed the fetcher's own internal PIPELINE_TIMEOUT
# plus headroom — this tool can fetch up to MAX_FILINGS=8 documents
# concurrently, so it gets the largest margin of the four priority tools.
TOOL_TIMEOUT  = int(_FETCHER_PIPELINE_TIMEOUT) + 60
MAX_FILINGS   = 8
DEFAULT_N     = 4


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_risk_trends(mcp: FastMCP) -> None:
    """Register the analyze_risk_trends tool onto a FastMCP instance."""

    @mcp.tool()
    async def analyze_risk_trends(
        ticker: str,
        form_type: Literal["10-Q", "10-K", "20-F"] = "10-K",
        n_filings: int = DEFAULT_N,
    ) -> RiskTrendsOutput:
        """
        Track how a company's risk profile evolves across multiple filings.

        Fetches the N most recent 10-K, 10-Q, or 20-F filings for a public
        company and builds a longitudinal risk timeline. For each filing the
        tool computes a materiality score and tracks which risk signals
        appeared, disappeared, or intensified versus the prior period.

        This is the right tool when you want to answer questions like:
          - "Has this company's risk profile been deteriorating over two years?"
          - "When did 'going concern' language first appear in their filings?"
          - "How has the MD&A tone shifted across the last four quarters?"

        For a single head-to-head comparison of the two most recent filings,
        use `compare_filings` instead. Results are cached for 3-7 days.

        Note on 10-Q Risk Factors: many 10-Q filings incorporate Risk Factors
        by reference from the annual 10-K. Use form_type='10-K' for a clean
        multi-year risk factor trend.

        Args:
            ticker:    US stock ticker symbol (e.g. AAPL, MSFT, TSLA, JPM).
            form_type: '10-K' for annual trend (recommended), '10-Q' for
                       quarterly, or '20-F' for foreign private issuers.
                       Defaults to '10-K'.
            n_filings: Number of filings to include in the trend (2-8).
                       Defaults to 4.

        Returns:
            RiskTrendsOutput containing a timeline of TrendPoints, signal
            appearance/disappearance events, and a TrendSummary.
        """
        start = time.monotonic()

        ticker = ticker.upper().strip()
        if not ticker or not ticker.replace("-", "").replace(".", "").isalpha():
            raise ToolError(f"Invalid ticker: {ticker!r}.")
        if form_type not in ("10-Q", "10-K", "20-F"):
            raise ToolError("form_type must be '10-Q', '10-K', or '20-F'.")

        n_filings = max(2, min(n_filings, MAX_FILINGS))

        cache_key = make_cache_key("analyze_risk_trends", ticker, form_type, str(n_filings))
        cached = cache_get(cache_key)
        if cached:
            return RiskTrendsOutput(**cached)

        try:
            result = await asyncio.wait_for(
                _run_trends_pipeline(ticker, form_type, n_filings),
                timeout=TOOL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            return RiskTrendsOutput(
                ticker=ticker, form_type=form_type,
                n_requested=n_filings, n_processed=0,
                pipeline_success=False,
                failure_reason=f"Pipeline timed out after {TOOL_TIMEOUT}s. Try a smaller n_filings.",
                trend_points=[], signal_appearances=[], signal_removals=[],
                summary=None,
                elapsed_seconds=round(elapsed, 2),
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            return RiskTrendsOutput(
                ticker=ticker, form_type=form_type,
                n_requested=n_filings, n_processed=0,
                pipeline_success=False,
                failure_reason=f"Unexpected error: {exc}",
                trend_points=[], signal_appearances=[], signal_removals=[],
                summary=None,
                elapsed_seconds=round(elapsed, 2),
            )

        if result.pipeline_success:
            cache_set(cache_key, result.model_dump(), ticker=ticker, form_type=form_type, tool_name="analyze_risk_trends")

        return result


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def _run_trends_pipeline(
    ticker: str, form_type: str, n_filings: int,
) -> RiskTrendsOutput:
    start = time.monotonic()

    filings = await fetch_n_filings(ticker, form_type, n=n_filings)

    if not filings:
        return RiskTrendsOutput(
            ticker=ticker, form_type=form_type,
            n_requested=n_filings, n_processed=0,
            pipeline_success=False,
            failure_reason=f"No {form_type} filings found for {ticker}.",
            trend_points=[], signal_appearances=[], signal_removals=[],
            summary=None,
            elapsed_seconds=round(time.monotonic() - start, 2),
        )

    extraction_tasks = [
        extract_sections_cached(
            f.html or "",
            accession=f.accession_number,
            filing_date=f.filing_date,
            form_type=form_type,
            document_url=f.document_url,
        )
        for f in filings
        if f.fetch_success and f.html
    ]

    valid_filings = [f for f in filings if f.fetch_success and f.html]
    extractions   = await asyncio.gather(*extraction_tasks, return_exceptions=True)

    paired = list(zip(valid_filings, extractions))
    paired.reverse()   # chronological order

    trend_points: list[TrendPoint] = []
    all_signal_sets: list[set] = []

    for filing_meta, extraction in paired:
        if isinstance(extraction, Exception):
            trend_points.append(TrendPoint(
                filing_date=filing_meta.filing_date,
                accession_number=filing_meta.accession_number,
                risk_materiality="unknown",
                mda_materiality="unknown",
                risk_raw_score=0.0,
                mda_raw_score=0.0,
                risk_char_count=0,
                mda_char_count=0,
                extraction_success=False,
                failure_note=str(extraction),
                top_signals=[],
            ))
            all_signal_sets.append(set())
            continue

        score = score_sections(
            newer_risk_text=extraction.risk_factors.text,
            older_risk_text=None,
            newer_mda_text=extraction.mda.text,
            older_mda_text=None,
        )

        signals_this_filing = {
            h.signal
            for h in (score.risk_factors.tier1_hits + score.risk_factors.tier2_hits
                      + score.mda.tier1_hits + score.mda.tier2_hits)
        }
        all_signal_sets.append(signals_this_filing)

        top_signals = list({
            h.signal
            for h in (score.risk_factors.tier1_hits[:3] + score.mda.tier1_hits[:3])
        })[:6]

        trend_points.append(TrendPoint(
            filing_date=filing_meta.filing_date,
            accession_number=filing_meta.accession_number,
            risk_materiality=score.risk_factors.materiality.value,
            mda_materiality=score.mda.materiality.value,
            risk_raw_score=score.risk_factors.raw_score,
            mda_raw_score=score.mda.raw_score,
            risk_char_count=extraction.risk_factors.char_count,
            mda_char_count=extraction.mda.char_count,
            extraction_success=extraction.both_succeeded,
            failure_note=", ".join(extraction.known_gaps) if extraction.known_gaps else None,
            top_signals=top_signals,
        ))

    signal_appearances: list[TrendSignalAppearance] = []
    signal_removals:    list[TrendSignalAppearance] = []

    for i in range(1, len(all_signal_sets)):
        prev    = all_signal_sets[i - 1]
        current = all_signal_sets[i]
        date    = trend_points[i].filing_date

        for sig in sorted(current - prev):
            signal_appearances.append(TrendSignalAppearance(
                signal=sig, filing_date=date,
                accession_number=trend_points[i].accession_number,
            ))
        for sig in sorted(prev - current):
            signal_removals.append(TrendSignalAppearance(
                signal=sig, filing_date=date,
                accession_number=trend_points[i].accession_number,
            ))

    summary = _build_trend_summary(trend_points, signal_appearances, signal_removals)

    return RiskTrendsOutput(
        ticker=ticker,
        form_type=form_type,
        n_requested=n_filings,
        n_processed=len(trend_points),
        pipeline_success=True,
        failure_reason=None,
        trend_points=trend_points,
        signal_appearances=signal_appearances,
        signal_removals=signal_removals,
        summary=summary,
        elapsed_seconds=round(time.monotonic() - start, 2),
    )


# ---------------------------------------------------------------------------
# Trend summary builder
# ---------------------------------------------------------------------------

_MATERIALITY_ORDER = {"low": 0, "moderate": 1, "high": 2, "critical": 3, "unknown": -1}


def _build_trend_summary(
    points: list[TrendPoint],
    appearances: list[TrendSignalAppearance],
    removals: list[TrendSignalAppearance],
) -> TrendSummary:
    if not points:
        return TrendSummary(
            trajectory="insufficient_data",
            overall_trend_note="No filing data available.",
            peak_risk_date=None,
            peak_risk_score=0.0,
            total_new_signals=0,
            total_removed_signals=0,
            analyst_summary="Insufficient data to compute a trend.",
        )

    risk_scores    = [p.risk_raw_score for p in points]
    first_score    = risk_scores[0]
    last_score     = risk_scores[-1]
    score_delta    = last_score - first_score

    if len(risk_scores) >= 2:
        if score_delta > 5:
            trajectory = "deteriorating"
        elif score_delta < -5:
            trajectory = "improving"
        else:
            trajectory = "stable"
    else:
        trajectory = "single_point"

    materiality_vals = [_MATERIALITY_ORDER.get(p.risk_materiality, -1) for p in points]
    peak_mat_idx     = materiality_vals.index(max(materiality_vals))
    peak_point       = points[peak_mat_idx]

    period_str = f"{points[0].filing_date} → {points[-1].filing_date}"
    parts = [
        f"Risk trend for {len(points)} {('annual' if len(points) <= 4 else 'quarterly')} "
        f"filings ({period_str}).",
        f"Trajectory: {trajectory.upper()}.",
        f"Risk score moved {first_score:.1f} → {last_score:.1f} "
        f"({'↑' if score_delta > 0 else '↓'}{abs(score_delta):.1f}).",
    ]
    if appearances:
        top_new = [a.signal for a in appearances[:5]]
        parts.append(f"New signals over period: {', '.join(top_new)}.")
    if removals:
        top_removed = [r.signal for r in removals[:3]]
        parts.append(f"Removed signals: {', '.join(top_removed)}.")
    if peak_point.risk_materiality in ("high", "critical"):
        parts.append(
            f"Peak risk period: {peak_point.filing_date} "
            f"(materiality={peak_point.risk_materiality.upper()})."
        )

    return TrendSummary(
        trajectory=trajectory,
        overall_trend_note=f"Risk score {trajectory} from {first_score:.1f} to {last_score:.1f}.",
        peak_risk_date=peak_point.filing_date,
        peak_risk_score=peak_point.risk_raw_score,
        total_new_signals=len(appearances),
        total_removed_signals=len(removals),
        analyst_summary=" ".join(parts),
    )