"""api/schemas/analyses.py — Request and response models for analysis routes."""

from typing import Any, Optional
from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class RunAnalysisRequest(BaseModel):
    """
    Unified request body for running any analysis tool.

    ticker     — US stock ticker (e.g. 'AAPL', 'MSFT')
    tool_name  — one of the 8 registered tools
    params     — tool-specific optional parameters (see below)

    Supported params per tool:
      compare_filings          → {"form_type": "10-Q"|"10-K"|"20-F"}
      generate_executive_report→ {"form_type": "10-K"|"10-Q"}
      categorize_risks         → {"form_type": "10-K"|"10-Q"|"20-F"}
      analyze_risk_trends      → {"form_type": "10-K", "n_filings": 4}
      analyze_8k_events        → {"n_filings": 5}
      analyze_proxy            → {}
      analyze_insider_activity → {"n_filings": 10}
      analyze_ownership        → {"n_filings": 6}
    """
    ticker:    str
    tool_name: str
    params:    dict[str, Any] = {}

    @field_validator("ticker")
    @classmethod
    def ticker_format(cls, v: str) -> str:
        v = v.upper().strip()
        if not v or not v.replace("-", "").replace(".", "").isalpha():
            raise ValueError(
                f"Invalid ticker '{v}'. Must be a US stock symbol like AAPL or BRK.B"
            )
        return v

    @field_validator("tool_name")
    @classmethod
    def valid_tool(cls, v: str) -> str:
        valid = {
            "compare_filings", "generate_executive_report", "categorize_risks",
            "analyze_risk_trends", "analyze_8k_events", "analyze_proxy",
            "analyze_insider_activity", "analyze_ownership",
        }
        if v not in valid:
            raise ValueError(f"Unknown tool '{v}'. Valid: {sorted(valid)}")
        return v


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class AnalysisSummary(BaseModel):
    """Lightweight row returned in list views — no full tool_output."""
    id:                 str
    ticker:             str
    tool_name:          str
    tool_params:        dict
    status:             str
    failure_reason:     Optional[str]
    elapsed_seconds:    Optional[float]
    ai_interpretation:  Optional[str]
    ai_provider:        Optional[str]
    created_at:         str
    updated_at:         str


class AnalysisDetail(AnalysisSummary):
    """Full analysis row including the raw tool output JSONB."""
    tool_output: Optional[Any]


class RunAnalysisResponse(BaseModel):
    analysis_id:      str
    ticker:           str
    tool_name:        str
    status:           str
    pipeline_success: bool
    failure_reason:   Optional[str]
    elapsed_seconds:  float
    tool_output:      Optional[Any]


class AnalysisListResponse(BaseModel):
    analyses: list[AnalysisSummary]
    total:    int
    limit:    int
    offset:   int


class ToolInfo(BaseModel):
    tool_name:   str
    description: str
    available:   bool
    min_plan:    str


class ToolListResponse(BaseModel):
    tools: list[ToolInfo]
