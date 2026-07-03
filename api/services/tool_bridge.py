from fastmcp import FastMCP

class ToolRegistry:
    def __init__(self):
        self._tools = {}
    
    def tool(self):
        registry = self
        class Decorator:
            def __call__(self, f):
                registry._tools[f.__name__] = f
                return f
        return Decorator()

_registry = None

def get_registry():
    global _registry
    if _registry is not None:
        return _registry
    
    _registry = ToolRegistry()
    
    try:
        from tools.executive_report import register_executive_report
        register_executive_report(_registry)
        print(f"[bridge] Registered executive_report tools: "
              f"{list(_registry._tools.keys())}")
    except Exception as e:
        print(f"[bridge] executive_report error: {e}")
    
    try:
        from tools.compare_filings import register_compare_filings
        register_compare_filings(_registry)
        print(f"[bridge] Registered compare_filings tools")
    except Exception as e:
        print(f"[bridge] compare_filings error: {e}")
    
    try:
        from tools.risk_trends import register_risk_trends
        register_risk_trends(_registry)
        print(f"[bridge] Registered risk_trends tools")
    except Exception as e:
        print(f"[bridge] risk_trends error: {e}")
    
    try:
        from tools.risk_categorizer import register_risk_categorizer
        register_risk_categorizer(_registry)
        print(f"[bridge] Registered risk_categorizer tools")
    except Exception as e:
        print(f"[bridge] risk_categorizer error: {e}")
    
    print(f"[bridge] All registered tools: {list(_registry._tools.keys())}")
    return _registry

async def run_tool(tool_hint: str, ticker: str, 
                   form_type: str = "10-K") -> str:
    registry = get_registry()
    tools = registry._tools
    
    print(f"[bridge] Available tools: {list(tools.keys())}")
    print(f"[bridge] Looking for tool matching: {tool_hint}")
    
    # Find the right tool based on hint
    target_func = None
    
    if tool_hint == "compare_filings":
        for name, func in tools.items():
            if "compare" in name.lower():
                target_func = (name, func)
                break
    elif tool_hint == "risk_trends":
        for name, func in tools.items():
            if "trend" in name.lower() or "risk_trend" in name.lower():
                target_func = (name, func)
                break
    elif tool_hint == "categorize_risks":
        for name, func in tools.items():
            if "categor" in name.lower():
                target_func = (name, func)
                break
    else:
        # Default: executive report
        for name, func in tools.items():
            if "executive" in name.lower() or "report" in name.lower() or "generate" in name.lower():
                target_func = (name, func)
                break
    
    # Fallback: use first available tool
    if not target_func and tools:
        name, func = next(iter(tools.items()))
        target_func = (name, func)
    
    if not target_func:
        return f"No tools available to analyze {ticker}"
    
    name, func = target_func
    print(f"[bridge] Calling tool: {name} for {ticker}")
    
    try:
        import inspect
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        print(f"[bridge] Tool {name} params: {params}")
        
        if 'ticker' in params and 'form_type' in params:
            result = await func(ticker=ticker, form_type=form_type)
        elif 'ticker' in params:
            result = await func(ticker=ticker)
        else:
            result = await func(ticker)
        
        if isinstance(result, dict):
            import json
            return json.dumps(result, indent=2)
        return str(result)
        
    except Exception as e:
        import traceback
        print(f"[bridge] Tool {name} error: {e}")
        print(traceback.format_exc())
        return f"Analysis error for {ticker}: {str(e)}"