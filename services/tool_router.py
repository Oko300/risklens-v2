import re
from typing import Optional

def detect_tool_intent(message: str) -> Optional[dict]:
    """
    Checks if the user message implies running a tool. Returns dict with
    {tool_name, ticker, params} or None if it is just a chat message.
    """
    ticker = None
    tool_name = None
    params = {}

    # 1. Extract ticker: look for uppercase 1-5 letter word that looks like a ticker
    #    Common false positives to ignore: I, A, THE, FOR, AND, OR, IN, OF, AT, MY, ME, IS, IT, DO, SO.
    ignore_words = {"I", "A", "THE", "FOR", "AND", "OR", "IN", "OF", "AT", "MY", "ME", "IS", "IT", "DO", "SO"}
    ticker_matches = re.findall(r'\b[A-Z]{1,5}\b', message.upper())
    
    for match in ticker_matches:
        if match not in ignore_words:
            ticker = match
            break

    # 2. Match intent keywords to tool
    message_lower = message.lower()
    
    if any(keyword in message_lower for keyword in ["compare", "what changed", "difference", "vs prior"]):
        tool_name = "compare_filings"
        params["form_type"] = "10-K"
    elif any(keyword in message_lower for keyword in ["report", "executive", "summary", "overview", "brief"]):
        tool_name = "generate_executive_report"
        params["form_type"] = "10-K"
    elif any(keyword in message_lower for keyword in ["8-k", "8k", "event", "material event", "recent news"]):
        tool_name = "analyze_8k_events"
    elif any(keyword in message_lower for keyword in ["insider", "buying", "selling", "form 4", "officers"]):
        tool_name = "analyze_insider_activity"
    elif any(keyword in message_lower for keyword in ["ownership", "activist", "13d", "institutional", "who owns"]):
        tool_name = "analyze_ownership"
    elif any(keyword in message_lower for keyword in ["proxy", "governance", "compensation", "board", "def 14a"]):
        tool_name = "analyze_proxy"
    elif any(keyword in message_lower for keyword in ["trend", "trajectory", "over time", "history", "years"]):
        tool_name = "analyze_risk_trends"
        params["form_type"] = "10-K"
    elif any(keyword in message_lower for keyword in ["risk", "categorize", "breakdown", "domains", "types of risk"]):
        tool_name = "categorize_risks"
        params["form_type"] = "10-K"

    # 3. Only return a tool intent if BOTH a ticker AND a keyword match are found.
    if ticker and tool_name:
        return {"tool_name": tool_name, "ticker": ticker, "params": params}
    else:
        return None