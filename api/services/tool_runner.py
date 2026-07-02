import importlib
import os
import re
from typing import Any, Dict, Optional

# Dynamically import tools from the 'tools' directory
TOOL_MODULES = {}
for filename in os.listdir("tools"):
    if filename.endswith(".py") and not filename.startswith("__"):
        module_name = filename[:-3]
        try:
            module = importlib.import_module(f"tools.{module_name}")
            TOOL_MODULES[module_name] = module
        except Exception as e:
            print(f"Could not import tool module {module_name}: {e}")

class ToolRunnerService:
    def __init__(self):
        pass

    def extract_ticker(self, message_content: str) -> Optional[str]:
        # Look for uppercase 2-5 letter words
        match = re.search(r'\b[A-Z]{2,5}\b', message_content)
        if match:
            return match.group(0)
        return None

    async def run_tool(self, tool_name: str, ticker: Optional[str], form_type: Optional[str] = None) -> Dict[str, Any]:
        if tool_name not in TOOL_MODULES:
            return {"error": f"Tool '{tool_name}' not found."}

        tool_module = TOOL_MODULES[tool_name]
        result = {}

        try:
            if tool_name == "executive_report":
                if not ticker:
                    return {"error": "Ticker is required for executive report."}
                # Assuming generate_executive_report is a function in executive_report.py
                result = await tool_module.generate_executive_report(ticker)
            elif tool_name == "compare_filings":
                if not ticker:
                    return {"error": "Ticker is required for comparing filings."}
                # Assuming compare_filings is a function in compare_filings.py
                result = await tool_module.compare_filings(ticker)
            elif tool_name == "risk_trends":
                if not ticker:
                    return {"error": "Ticker is required for analyzing risk trends."}
                # Assuming analyze_risk_trends is a function in risk_trends.py
                result = await tool_module.analyze_risk_trends(ticker)
            elif tool_name == "risk_categorizer":
                if not ticker:
                    return {"error": "Ticker is required for categorizing risks."}
                # Assuming categorize_risks is a function in risk_categorizer.py
                result = await tool_module.categorize_risks(ticker)
            else:
                return {"error": f"Tool '{tool_name}' is not implemented in ToolRunnerService."}

            return {"tool_name": tool_name, "result": result}
        except Exception as e:
            print(f"Error running tool {tool_name} for ticker {ticker}: {e}")
            return {"error": f"Failed to run tool '{tool_name}': {str(e)}"}

    def detect_tool(self, message_content: str) -> Optional[str]:
        message_content_lower = message_content.lower()
        if "executive report" in message_content_lower or "analyst report" in message_content_lower:
            return "executive_report"
        elif "compare" in message_content_lower:
            return "compare_filings"
        elif "trend" in message_content_lower or "history" in message_content_lower or "years" in message_content_lower:
            return "risk_trends"
        elif "categorize" in message_content_lower or "category" in message_content_lower or "breakdown" in message_content_lower:
            return "risk_categorizer"
        return None