"""
services/analysis_service.py — RiskLens v2
============================================
Orchestrates tool execution, result persistence, and usage logging.

Architecture
------------
This service sits between the FastAPI route handlers and the existing
tool pipeline functions. It does NOT import from FastMCP or touch the
MCP server — it calls the core pipeline functions directly, just as the
MCP tool wrappers do. Both the MCP server and REST API share the same
Upstash Redis cache, so a result cached by a Claude Desktop call is
instantly available to a REST API caller for the same ticker/tool.

Tool registry
-------------
Each tool entry maps a tool_name string to:
  - fn      : the async pipeline function to call
  - params  : which kwargs to pass from the user's request
  - plan    : minimum plan required ('free' = everyone)

The private pipeline functions (_run_pipeline etc.) are intentionally
called directly. Python does not enforce name-mangling on _ prefixes
for module-level functions — this is an established pattern when wrapping
internal logic for a different transport layer.

Serialisation
-------------
Pydantic models are serialised via .model_dump_json() → json.loads()
to guarantee all enum values and datetimes are JSON-native before
storage in Supabase's JSONB column.
"""

import asyncio
import json
import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

def _load_tool_registry() -> dict:
    """
    Lazily import pipeline functions to avoid circular imports at module load.
    Each tool entry:
      fn        — async callable that runs the analysis
      param_map — maps request param names to pipeline kwargs
      min_plan  — minimum subscription plan required
    """
    from tools.compare_filings   import _run_pipeline          as _compare
    from tools.executive_report  import _run_report_pipeline   as _report
    from tools.risk_categorizer  import _run_categorizer_pipeline as _categorize
    from tools.risk_trends       import _run_trends_pipeline   as _trends
    from core.eight_k            import analyze_8k_filings
    from core.proxy              import analyze_proxy_filing
    from core.insider            import analyze_insider_filings
    from core.ownership          import analyze_ownership_filings

    # Thin async wrappers that normalise return types to plain dicts
    async def compare(p):
        result = await _compare(p["ticker"], p.get("form_type", "10-Q"))
        return _to_dict(result)

    async def report(p):
        result = await _report(p["ticker"], p.get("form_type", "10-K"))
        return _to_dict(result)

    async def categorize(p):
        result = await _categorize(p["ticker"], p.get("form_type", "10-K"))
        return _to_dict(result)

    async def trends(p):
        result = await _trends(
            p["ticker"],
            p.get("form_type", "10-K"),
            int(p.get("n_filings", 4)),
        )
        return _to_dict(result)

    async def eight_k(p):
        result = await analyze_8k_filings(
            p["ticker"], n_filings=int(p.get("n_filings", 5))
        )
        result.pop("from_cache", None)
        return result

    async def proxy(p):
        result = await analyze_proxy_filing(p["ticker"])
        result.pop("from_cache", None)
        return result

    async def insider(p):
        result = await analyze_insider_filings(
            p["ticker"], n_filings=int(p.get("n_filings", 10))
        )
        result.pop("from_cache", None)
        return result

    async def ownership(p):
        result = await analyze_ownership_filings(
            p["ticker"], n_filings=int(p.get("n_filings", 6))
        )
        result.pop("from_cache", None)
        return result

    return {
        "compare_filings": {
            "fn": compare,
            "description": "Compare two most recent filings (Risk Factors + MD&A delta)",
            "min_plan": "free",
        },
        "generate_executive_report": {
            "fn": report,
            "description": "Human-readable analyst report from SEC filing",
            "min_plan": "free",
        },
        "categorize_risks": {
            "fn": categorize,
            "description": "Classify Risk Factors into 10 standardised domains",
            "min_plan": "free",
        },
        "analyze_risk_trends": {
            "fn": trends,
            "description": "Multi-filing risk trajectory analysis",
            "min_plan": "pro",          # heavier pipeline — pro only
        },
        "analyze_8k_events": {
            "fn": eight_k,
            "description": "Real-time material event intelligence from 8-K filings",
            "min_plan": "free",
        },
        "analyze_proxy": {
            "fn": proxy,
            "description": "Governance & compensation risk from DEF 14A proxy",
            "min_plan": "pro",
        },
        "analyze_insider_activity": {
            "fn": insider,
            "description": "Insider buying/selling trends from Form 4 filings",
            "min_plan": "pro",
        },
        "analyze_ownership": {
            "fn": ownership,
            "description": "Activist & institutional ownership from 13D/13G/13F",
            "min_plan": "pro",
        },
    }


_REGISTRY: Optional[dict] = None


def get_registry() -> dict:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_tool_registry()
    return _REGISTRY


def list_tools(plan: str) -> list[dict]:
    """Returns tool metadata visible to the user based on their plan."""
    registry = get_registry()
    _PLAN_ORDER = {"free": 0, "pro": 1, "business": 2}
    user_level = _PLAN_ORDER.get(plan, 0)
    return [
        {
            "tool_name":   name,
            "description": meta["description"],
            "available":   _PLAN_ORDER.get(meta["min_plan"], 0) <= user_level,
            "min_plan":    meta["min_plan"],
        }
        for name, meta in registry.items()
    ]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_analysis(
    user_id:       str,
    tool_name:     str,
    ticker:        str,
    params:        dict,
    plan:          str,
    user_timezone: str,
) -> dict:
    """
    Full analysis lifecycle:
      1. Validate tool name and plan access
      2. Create a 'pending' analysis record
      3. Run the tool pipeline (with Upstash cache)
      4. Update the record with results
      5. Log usage (on success only)

    Returns a dict with 'analysis_id' and the full tool output.
    Raises ValueError for validation failures.
    Raises PermissionError for plan restrictions.
    """
    registry = get_registry()

    # ── Validation ───────────────────────────────────────────────────────────
    if tool_name not in registry:
        raise ValueError(
            f"Unknown tool: '{tool_name}'. "
            f"Valid options: {list(registry.keys())}"
        )

    tool_meta = registry[tool_name]
    _PLAN_ORDER = {"free": 0, "pro": 1, "business": 2}
    if _PLAN_ORDER.get(plan, 0) < _PLAN_ORDER.get(tool_meta["min_plan"], 0):
        raise PermissionError(
            f"'{tool_name}' requires a {tool_meta['min_plan'].upper()} plan. "
            f"Your current plan is {plan.upper()}. Upgrade to access this tool."
        )

    ticker = ticker.upper().strip()
    if not ticker:
        raise ValueError("ticker is required")

    # ── Create analysis record (status=processing) ────────────────────────
    analysis_id = str(uuid.uuid4())
    tool_params = {"ticker": ticker, **params}

    analysis_record = {
        "id":          analysis_id,
        "user_id":     user_id,
        "ticker":      ticker,
        "tool_name":   tool_name,
        "tool_params": tool_params,
        "status":      "processing",
    }

    await _db_insert("analyses", analysis_record)

    # ── Run the pipeline ──────────────────────────────────────────────────
    start = time.monotonic()
    tool_output = None
    success = False
    failure_reason = None

    try:
        tool_output = await tool_meta["fn"](tool_params)
        success = tool_output.get("pipeline_success", True)
        if not success:
            failure_reason = tool_output.get("failure_reason", "Pipeline returned failure")
    except Exception as exc:
        failure_reason = f"Unexpected error during {tool_name}: {type(exc).__name__}: {exc}"
        success = False

    elapsed = round(time.monotonic() - start, 2)

    # ── Update analysis record ────────────────────────────────────────────
    update_payload: dict[str, Any] = {
        "status":          "completed" if success else "failed",
        "elapsed_seconds": elapsed,
    }
    if tool_output:
        update_payload["tool_output"] = _make_json_safe(tool_output)
    if failure_reason:
        update_payload["failure_reason"] = failure_reason

    await _db_update("analyses", analysis_id, update_payload)

    # ── Log usage (only on success) ───────────────────────────────────────
    if success:
        from services.usage_service import log_usage
        await log_usage(user_id, user_timezone, tool_name, ticker)

    return {
        "analysis_id":     analysis_id,
        "ticker":          ticker,
        "tool_name":       tool_name,
        "status":          update_payload["status"],
        "pipeline_success": success,
        "failure_reason":  failure_reason,
        "elapsed_seconds": elapsed,
        "tool_output":     tool_output,
    }


# ---------------------------------------------------------------------------
# DB helpers — all Supabase calls run in a thread to avoid blocking the
# asyncio event loop (supabase-py uses synchronous httpx internally)
# ---------------------------------------------------------------------------

async def _db_insert(table: str, data: dict) -> None:
    def _op():
        from db.client import get_admin_client
        get_admin_client().table(table).insert(data).execute()
    await asyncio.to_thread(_op)


async def _db_update(table: str, record_id: str, data: dict) -> None:
    def _op():
        from db.client import get_admin_client
        get_admin_client().table(table).update(data).eq("id", record_id).execute()
    await asyncio.to_thread(_op)


async def get_analysis_by_id(analysis_id: str, user_id: str) -> Optional[dict]:
    """Fetch a single analysis record. Returns None if not found or wrong user."""
    def _op():
        from db.client import get_admin_client
        result = (
            get_admin_client()
            .table("analyses")
            .select("*")
            .eq("id", analysis_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        return result.data
    return await asyncio.to_thread(_op)


async def get_user_analyses(
    user_id: str,
    limit: int = 20,
    offset: int = 0,
    ticker: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> list[dict]:
    """
    List analyses for a user with optional filtering.
    Returns summary rows (no tool_output JSONB to keep responses small).
    """
    def _op():
        from db.client import get_admin_client
        query = (
            get_admin_client()
            .table("analyses")
            # Exclude tool_output from list view — fetch full result with get_analysis_by_id
            .select("id,user_id,ticker,tool_name,tool_params,status,"
                    "failure_reason,elapsed_seconds,ai_interpretation,"
                    "ai_provider,created_at,updated_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if ticker:
            query = query.eq("ticker", ticker.upper())
        if tool_name:
            query = query.eq("tool_name", tool_name)
        result = query.execute()
        return result.data or []
    return await asyncio.to_thread(_op)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _to_dict(obj: Any) -> dict:
    """Convert a Pydantic model or plain dict to a JSON-safe dict."""
    if isinstance(obj, BaseModel):
        # model_dump_json() → json.loads() is the safest path:
        # it handles Enums, datetimes, and nested models correctly.
        return json.loads(obj.model_dump_json())
    return obj


def _make_json_safe(data: Any) -> Any:
    """Recursively ensure data is JSON-serialisable before Supabase insert."""
    if isinstance(data, BaseModel):
        return json.loads(data.model_dump_json())
    if isinstance(data, dict):
        return {k: _make_json_safe(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_make_json_safe(i) for i in data]
    return data
