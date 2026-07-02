"""
api/routers/analyses.py — RiskLens v2
========================================
Analysis execution and history routes.

POST /analyses/run         — run any tool, get results + save to DB
GET  /analyses             — list user's past analyses (paginated)
GET  /analyses/tools       — list available tools for current plan
GET  /analyses/{id}        — get full analysis including tool_output

Design notes:
  - Usage limit is checked BEFORE the tool runs (via check_usage_limit dep)
  - Usage is logged AFTER a successful tool run (not on failures)
  - Tool output is saved in the analyses table for chat context in Phase 2
  - List endpoint returns summary rows (no tool_output) for fast loading
  - Detail endpoint returns full tool_output for display/download
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import check_usage_limit, get_current_user, get_subscription
from api.schemas.analyses import (
    AnalysisDetail,
    AnalysisListResponse,
    AnalysisSummary,
    RunAnalysisRequest,
    RunAnalysisResponse,
    ToolListResponse,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Run a tool
# ---------------------------------------------------------------------------

@router.post("/run", response_model=RunAnalysisResponse, status_code=status.HTTP_200_OK)
async def run_analysis(
    body:     RunAnalysisRequest,
    user_sub: Annotated[tuple[dict, dict], Depends(check_usage_limit)],
):
    """
    Run any of the 8 RiskLens analysis tools.

    The check_usage_limit dependency enforces the daily free-tier limit
    BEFORE the tool runs. If the user is over their limit, a 429 is
    returned immediately without hitting EDGAR.

    The Upstash Redis cache is checked inside the tool pipeline — if this
    ticker/tool was recently analysed (by anyone), results come back in
    milliseconds. Cache hits still count as one of the user's daily analyses.

    On success, the full structured result is saved to public.analyses for
    chat context and history browsing. Usage is logged only on success.
    """
    user, sub = user_sub
    plan = sub.get("plan", "free")

    from services.analysis_service import run_analysis as _run

    try:
        result = await _run(
            user_id=user["id"],
            tool_name=body.tool_name,
            ticker=body.ticker,
            params=body.params,
            plan=plan,
            user_timezone=user.get("timezone", "UTC"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    return RunAnalysisResponse(
        analysis_id=result["analysis_id"],
        ticker=result["ticker"],
        tool_name=result["tool_name"],
        status=result["status"],
        pipeline_success=result["pipeline_success"],
        failure_reason=result.get("failure_reason"),
        elapsed_seconds=result["elapsed_seconds"],
        tool_output=result.get("tool_output"),
    )


# ---------------------------------------------------------------------------
# List tools available for this plan
# ---------------------------------------------------------------------------

@router.get("/tools", response_model=ToolListResponse)
async def list_tools(
    user: Annotated[dict, Depends(get_current_user)],
    sub:  Annotated[dict, Depends(get_subscription)],
):
    """
    Returns all 8 tools with availability based on the user's current plan.
    Free users see pro tools marked as unavailable with the min_plan hint.
    """
    from services.analysis_service import list_tools as _list_tools

    plan = sub.get("plan", "free")
    tools = _list_tools(plan)
    return ToolListResponse(tools=tools)


# ---------------------------------------------------------------------------
# List analysis history
# ---------------------------------------------------------------------------

@router.get("", response_model=AnalysisListResponse)
async def get_analyses(
    user:      Annotated[dict, Depends(get_current_user)],
    ticker:    Optional[str] = Query(None, description="Filter by ticker symbol"),
    tool_name: Optional[str] = Query(None, description="Filter by tool name"),
    limit:     int = Query(20, ge=1, le=100, description="Results per page"),
    offset:    int = Query(0,  ge=0,          description="Pagination offset"),
):
    """
    Returns paginated list of the user's past analyses.

    Note: tool_output is NOT included here to keep responses fast.
    Use GET /analyses/{id} to retrieve the full output of a specific analysis.
    """
    from services.analysis_service import get_user_analyses

    rows = await get_user_analyses(
        user_id=user["id"],
        limit=limit,
        offset=offset,
        ticker=ticker,
        tool_name=tool_name,
    )

    summaries = [
        AnalysisSummary(
            id=r["id"],
            ticker=r["ticker"],
            tool_name=r["tool_name"],
            tool_params=r.get("tool_params") or {},
            status=r["status"],
            failure_reason=r.get("failure_reason"),
            elapsed_seconds=r.get("elapsed_seconds"),
            ai_interpretation=r.get("ai_interpretation"),
            ai_provider=r.get("ai_provider"),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]

    return AnalysisListResponse(
        analyses=summaries,
        total=len(summaries),   # Phase 2: add a COUNT query for true total
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Get a single analysis with full tool_output
# ---------------------------------------------------------------------------

@router.get("/{analysis_id}", response_model=AnalysisDetail)
async def get_analysis(
    analysis_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """
    Returns the full analysis record including tool_output.
    Only the owner of the analysis can access it (enforced by user_id filter).
    """
    from services.analysis_service import get_analysis_by_id

    row = await get_analysis_by_id(analysis_id, user["id"])

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Analysis '{analysis_id}' not found.",
        )

    return AnalysisDetail(
        id=row["id"],
        ticker=row["ticker"],
        tool_name=row["tool_name"],
        tool_params=row.get("tool_params") or {},
        status=row["status"],
        failure_reason=row.get("failure_reason"),
        elapsed_seconds=row.get("elapsed_seconds"),
        ai_interpretation=row.get("ai_interpretation"),
        ai_provider=row.get("ai_provider"),
        tool_output=row.get("tool_output"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
