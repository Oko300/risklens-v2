"""
schemas.py — RiskLens v2
==========================
All Pydantic output models shared across all four tools.
Schema version: 2.0
"""

from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class FilingMetaOut(BaseModel):
    ticker:           str
    cik:              str
    form_type:        str
    accession_number: str
    filing_date:      str
    report_date:      str
    document_url:     str
    fetch_success:    bool
    failure_reason:   Optional[str]
    html_byte_length: int


class SectionExtractionOut(BaseModel):
    section_name:       str
    item_label:         str
    extraction_success: bool
    method:             str
    confidence_score:   float
    char_count:         int
    failure_reason:     Optional[str]
    coverage_gap_note:  Optional[str]


class ExtractionOut(BaseModel):
    filing_accession:    str
    filing_date:         str
    risk_factors:        SectionExtractionOut
    mda:                 SectionExtractionOut
    full_doc_char_count: int
    known_gaps:          list[str]
    both_succeeded:      bool
    any_succeeded:       bool


# ---------------------------------------------------------------------------
# compare_filings output
# ---------------------------------------------------------------------------

class ChangeOut(BaseModel):
    type:       str
    older:      str
    newer:      str
    similarity: float


class SectionDeltaOut(BaseModel):
    section_name:          str
    magnitude:             str
    total_older_sentences: int
    total_newer_sentences: int
    added_count:           int
    removed_count:         int
    rewritten_count:       int
    unchanged_count:       int
    pct_changed:           float
    delta_success:         bool
    failure_reason:        Optional[str]
    top_changes:           list[ChangeOut]


class DeltaOut(BaseModel):
    risk_factors:    Optional[SectionDeltaOut]
    mda:             Optional[SectionDeltaOut]
    comparison_note: str


class SignalHitOut(BaseModel):
    signal:    str
    tier:      int
    weight:    int
    in_change: bool
    context:   str


class SectionScoreOut(BaseModel):
    section_name:    str
    materiality:     str
    raw_score:       float
    is_estimate:     bool
    analyst_note:    str
    tier1_hits:      list[SignalHitOut]
    tier2_hits:      list[SignalHitOut]
    new_signals:     list[SignalHitOut]
    removed_signals: list[SignalHitOut]


class ScoringOut(BaseModel):
    risk_factors:        SectionScoreOut
    mda:                 SectionScoreOut
    overall_materiality: str
    top_signals:         list[str]
    scoring_success:     bool
    failure_reason:      Optional[str]


class CompareFilingsOutput(BaseModel):
    schema_version:          str
    tool:                    str
    ticker:                  str
    form_type:               str
    pipeline_success:        bool
    failure_reason:          Optional[str]
    elapsed_seconds:         float
    newer_filing:            Optional[FilingMetaOut]
    older_filing:            Optional[FilingMetaOut]
    newer_extraction:        Optional[ExtractionOut]
    older_extraction:        Optional[ExtractionOut]
    delta:                   Optional[DeltaOut]
    scoring:                 Optional[ScoringOut]
    coverage_gap_disclosure: str


# ---------------------------------------------------------------------------
# analyze_risk_trends output
# ---------------------------------------------------------------------------

class TrendPoint(BaseModel):
    filing_date:        str
    accession_number:   str
    risk_materiality:   str
    mda_materiality:    str
    risk_raw_score:     float
    mda_raw_score:      float
    risk_char_count:    int
    mda_char_count:     int
    extraction_success: bool
    failure_note:       Optional[str]
    top_signals:        list[str]


class TrendSignalAppearance(BaseModel):
    signal:           str
    filing_date:      str
    accession_number: str


class TrendSummary(BaseModel):
    trajectory:            str
    overall_trend_note:    str
    peak_risk_date:        Optional[str]
    peak_risk_score:       float
    total_new_signals:     int
    total_removed_signals: int
    analyst_summary:       str


class RiskTrendsOutput(BaseModel):
    ticker:             str
    form_type:          str
    n_requested:        int
    n_processed:        int
    pipeline_success:   bool
    failure_reason:     Optional[str]
    trend_points:       list[TrendPoint]
    signal_appearances: list[TrendSignalAppearance]
    signal_removals:    list[TrendSignalAppearance]
    summary:            Optional[TrendSummary]
    elapsed_seconds:    float


# ---------------------------------------------------------------------------
# categorize_risks output
# ---------------------------------------------------------------------------

class RiskCategory(str, Enum):
    FINANCIAL          = "financial"
    LEGAL_REGULATORY   = "legal_regulatory"
    CYBERSECURITY      = "cybersecurity"
    OPERATIONAL        = "operational"
    MARKET_COMPETITIVE = "market_competitive"
    MACRO_GEOPOLITICAL = "macro_geopolitical"
    STRATEGIC          = "strategic"
    TECHNOLOGY         = "technology"
    ESG_CLIMATE        = "esg_climate"
    REPUTATIONAL       = "reputational"


class RiskCategoryDetail(BaseModel):
    category:        RiskCategory
    label:           str
    tier:            int
    signal_count:    int
    matched_signals: list[str]
    excerpts:        list[str]


class RiskCategorizationSummary(BaseModel):
    total_domains_identified: int
    total_signals:            int
    tier1_domain_count:       int
    top_domains:              list[str]
    executive_summary:        str


class CategorizeRisksOutput(BaseModel):
    ticker:                  str
    form_type:               str
    pipeline_success:        bool
    failure_reason:          Optional[str]
    filing_date:             Optional[str]
    accession_number:        Optional[str]
    risk_section_char_count: int
    categories:              list[RiskCategoryDetail]
    summary:                 Optional[RiskCategorizationSummary]
    elapsed_seconds:         float


# ---------------------------------------------------------------------------
# generate_executive_report output
# ---------------------------------------------------------------------------

class ExecutiveReportOutput(BaseModel):
    ticker:              str
    form_type:           str
    pipeline_success:    bool
    failure_reason:      Optional[str]
    report:              Optional[str]
    filing_date:         Optional[str]
    overall_materiality: Optional[str]
    top_signals:         list[str]
    elapsed_seconds:     float
