import re
import json
import inspect

# Maps tool hints to their register functions and exact tool names
TOOL_MAP = {
    "compare_filings": {
        "module": "tools.compare_filings",
        "register": "register_compare_filings",
        "tool_name": "compare_filings"
    },
    "risk_trends": {
        "module": "tools.risk_trends",
        "register": "register_risk_trends",
        "tool_name": "analyze_risk_trends"
    },
    "categorize_risks": {
        "module": "tools.risk_categorizer",
        "register": "register_risk_categorizer",
        "tool_name": "categorize_risks"
    },
    "executive_report": {
        "module": "tools.executive_report",
        "register": "register_executive_report",
        "tool_name": "generate_executive_report"
    }
}


class MockMCP:
    """Minimal MCP stand-in that captures the registered tool function."""
    def __init__(self):
        self._tools = {}

    def tool(self):
        parent = self
        class Dec:
            def __call__(self, f):
                parent._tools[f.__name__] = f
                return f
        return Dec()


def get_tool_hint(message: str) -> str:
    """Determine which single tool to call based on message content."""
    msg = message.lower()
    if any(w in msg for w in ["compare", "vs", "versus", "difference", "last two", "both filings"]):
        return "compare_filings"
    if any(w in msg for w in ["trend", "history", "years", "over time", "past", "trajectory", "historically"]):
        return "risk_trends"
    if any(w in msg for w in ["categor", "breakdown", "types", "domain", "classify", "categories"]):
        return "categorize_risks"
    return "executive_report"


async def run_best_tool(ticker: str, message: str) -> tuple:
    """Load and call exactly ONE tool based on message intent. Returns (result_str, tool_name)."""
    hint = get_tool_hint(message)
    config = TOOL_MAP[hint]
    print(f"[bridge] intent={hint} ticker={ticker} tool={config['tool_name']}")

    mock = MockMCP()

    try:
        import importlib
        module = importlib.import_module(config["module"])
        register_fn = getattr(module, config["register"])
        register_fn(mock)
    except Exception as e:
        import traceback
        print(f"[bridge] register error for {hint}: {e}")
        print(traceback.format_exc())
        return f"Could not load {hint} tool: {e}", "error"

    tool_name = config["tool_name"]
    func = mock._tools.get(tool_name)

    # Fallback: use first registered tool if exact name not found
    if func is None and mock._tools:
        tool_name, func = next(iter(mock._tools.items()))
        print(f"[bridge] exact tool not found, falling back to: {tool_name}")

    if func is None:
        return f"No tool available for {hint}. Please try again.", "error"

    print(f"[bridge] calling {tool_name} for {ticker}")

    try:
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        print(f"[bridge] params: {params}")

        if "ticker" in params and "form_type" in params:
            result = await func(ticker=ticker, form_type="10-K")
        elif "ticker" in params:
            result = await func(ticker=ticker)
        else:
            result = await func(ticker)

        if isinstance(result, dict):
            return json.dumps(result, indent=2), tool_name
        return str(result), tool_name

    except Exception as e:
        import traceback
        print(f"[bridge] call error for {tool_name}: {e}")
        print(traceback.format_exc())
        return f"Analysis failed for {ticker}: {str(e)}", "error"