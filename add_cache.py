"""
add_cache.py — Run this once to patch all tools with SQLite caching.
Run from your risklens-v2 folder: py add_cache.py
"""
import os

# ── 1. Patch compare_filings.py ─────────────────────────────────────────────
cf = os.path.join("tools", "compare_filings.py")
if os.path.exists(cf):
    with open(cf, "r", encoding="utf-8") as f:
        c = f.read()
    if "cache_get" not in c:
        c = c.replace(
            "from schemas         import (",
            "from core.cache      import cache_get, cache_set, make_cache_key\nfrom schemas         import ("
        )
        c = c.replace(
            "        try:\n            result = await asyncio.wait_for(\n                _run_pipeline(ticker, form_type),\n                timeout=TOOL_TIMEOUT,\n            )",
            """        _ck = make_cache_key("compare_filings", ticker, form_type)
        _hit = cache_get(_ck)
        if _hit:
            from pydantic import TypeAdapter
            return TypeAdapter(CompareFilingsOutput).validate_python(_hit)

        try:
            result = await asyncio.wait_for(
                _run_pipeline(ticker, form_type),
                timeout=TOOL_TIMEOUT,
            )"""
        )
        c = c.replace(
            "        return result\n\n\n# ---------------------------------------------------------------------------\n# Pipeline",
            """        if result.pipeline_success:
            cache_set(_ck, result.model_dump(), ticker=ticker, form_type=form_type, tool_name="compare_filings")
        return result


# ---------------------------------------------------------------------------
# Pipeline"""
        )
        with open(cf, "w", encoding="utf-8") as f:
            f.write(c)
        print(f"✓ Patched {cf}")
    else:
        print(f"✓ {cf} already has cache")
else:
    print(f"✗ {cf} not found")


# ── 2. Patch risk_categorizer.py ────────────────────────────────────────────
rc = os.path.join("tools", "risk_categorizer.py")
if os.path.exists(rc):
    with open(rc, "r", encoding="utf-8") as f:
        c = f.read()
    if "cache_get" not in c:
        c = c.replace(
            "from core.fetcher",
            "from core.cache      import cache_get, cache_set, make_cache_key\nfrom core.fetcher"
        )
        c = c.replace(
            "        try:\n            result = await asyncio.wait_for(\n                _run_categorizer_pipeline(ticker, form_type),\n                timeout=TOOL_TIMEOUT,\n            )",
            """        _ck = make_cache_key("categorize_risks", ticker, form_type)
        _hit = cache_get(_ck)
        if _hit:
            from pydantic import TypeAdapter
            return TypeAdapter(CategorizeRisksOutput).validate_python(_hit)

        try:
            result = await asyncio.wait_for(
                _run_categorizer_pipeline(ticker, form_type),
                timeout=TOOL_TIMEOUT,
            )"""
        )
        c = c.replace(
            "        return result\n\n\n# ---------------------------------------------------------------------------\n# Pipeline",
            """        if result.pipeline_success:
            cache_set(_ck, result.model_dump(), ticker=ticker, form_type=form_type, tool_name="categorize_risks")
        return result


# ---------------------------------------------------------------------------
# Pipeline"""
        )
        with open(rc, "w", encoding="utf-8") as f:
            f.write(c)
        print(f"✓ Patched {rc}")
    else:
        print(f"✓ {rc} already has cache")
else:
    print(f"✗ {rc} not found")


# ── 3. Patch risk_trends.py ──────────────────────────────────────────────────
rt = os.path.join("tools", "risk_trends.py")
if os.path.exists(rt):
    with open(rt, "r", encoding="utf-8") as f:
        c = f.read()
    if "cache_get" not in c:
        c = c.replace(
            "from core.fetcher",
            "from core.cache      import cache_get, cache_set, make_cache_key\nfrom core.fetcher"
        )
        c = c.replace(
            "        try:\n            result = await asyncio.wait_for(\n                _run_trends_pipeline(ticker, form_type, n_filings),\n                timeout=TOOL_TIMEOUT,\n            )",
            """        _ck = make_cache_key("analyze_risk_trends", ticker, form_type, str(n_filings))
        _hit = cache_get(_ck)
        if _hit:
            from pydantic import TypeAdapter
            return TypeAdapter(RiskTrendsOutput).validate_python(_hit)

        try:
            result = await asyncio.wait_for(
                _run_trends_pipeline(ticker, form_type, n_filings),
                timeout=TOOL_TIMEOUT,
            )"""
        )
        c = c.replace(
            "        return result\n\n\n# ---------------------------------------------------------------------------\n# Pipeline",
            """        if result.pipeline_success:
            cache_set(_ck, result.model_dump(), ticker=ticker, form_type=form_type, tool_name="analyze_risk_trends")
        return result


# ---------------------------------------------------------------------------
# Pipeline"""
        )
        with open(rt, "w", encoding="utf-8") as f:
            f.write(c)
        print(f"✓ Patched {rt}")
    else:
        print(f"✓ {rt} already has cache")
else:
    print(f"✗ {rt} not found")


# ── 4. Patch executive_report.py ─────────────────────────────────────────────
er = os.path.join("tools", "executive_report.py")
if os.path.exists(er):
    with open(er, "r", encoding="utf-8") as f:
        c = f.read()
    if "cache_get" not in c:
        c = c.replace(
            "from core.fetcher",
            "from core.cache      import cache_get, cache_set, make_cache_key\nfrom core.fetcher"
        )
        c = c.replace(
            "        try:\n            result = await asyncio.wait_for(\n                _run_report_pipeline(ticker, form_type),\n                timeout=TOOL_TIMEOUT,\n            )",
            """        _ck = make_cache_key("generate_executive_report", ticker, form_type)
        _hit = cache_get(_ck)
        if _hit:
            from pydantic import TypeAdapter
            return TypeAdapter(ExecutiveReportOutput).validate_python(_hit)

        try:
            result = await asyncio.wait_for(
                _run_report_pipeline(ticker, form_type),
                timeout=TOOL_TIMEOUT,
            )"""
        )
        c = c.replace(
            "        if result.pipeline_success:\n            return ExecutiveReportOutput(",
            """        if result.pipeline_success:
            cache_set(_ck, result.model_dump(), ticker=ticker, form_type=form_type, tool_name="generate_executive_report")
        if result.pipeline_success:
            return ExecutiveReportOutput("""
        )
        with open(er, "w", encoding="utf-8") as f:
            f.write(c)
        print(f"✓ Patched {er}")
    else:
        print(f"✓ {er} already has cache")
else:
    print(f"✗ {er} not found")


# ── 5. Update server.py to register 8-K tool ────────────────────────────────
sv = "server.py"
if os.path.exists(sv):
    with open(sv, "r", encoding="utf-8") as f:
        c = f.read()
    if "eight_k" not in c:
        c = c.replace(
            "from tools.executive_report  import register_executive_report",
            "from tools.executive_report  import register_executive_report\nfrom tools.eight_k_events    import register_eight_k_events"
        )
        c = c.replace(
            "register_executive_report(mcp)",
            "register_executive_report(mcp)\nregister_eight_k_events(mcp)"
        )
        with open(sv, "w", encoding="utf-8") as f:
            f.write(c)
        print(f"✓ Patched {sv} — 8-K tool registered")
    else:
        print(f"✓ {sv} already has 8-K")
else:
    print(f"✗ {sv} not found")

print("\nDone! Now run: git add . && git commit -m 'Add SQLite cache + 8-K stub' && git push")
