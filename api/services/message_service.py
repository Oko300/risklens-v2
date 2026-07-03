import re
import json
import httpx
from api.core.database import get_supabase_client


def extract_ticker(text: str) -> str:
    skip = {
        "I","A","AN","THE","FOR","OF","AND","OR","IN","ON","AT",
        "TO","IS","IT","BE","MY","ME","WE","US","DO","GO","GET",
        "CAN","SEC","CEO","CFO","AI","ML","API","USD","UK","EU",
        "GDP","IPO","ETF","GENERATE","REPORT","ANALYZE","COMPARE",
        "SHOW","WHAT","HOW","WHY","WHEN","WHO","TELL","GIVE","RISK",
        "TREND","FILING","ANNUAL","QUARTER","EXECUTIVE","LATEST"
    }
    words = text.upper().split()
    for word in words:
        clean = re.sub(r'[^A-Z]', '', word)
        if 2 <= len(clean) <= 5 and clean not in skip:
            return clean
    return ""


def get_tool_hint(message: str) -> str:
    msg = message.lower()
    if any(w in msg for w in ["compare","vs","versus","difference","last two"]):
        return "compare_filings"
    elif any(w in msg for w in ["trend","history","years","over time","past","trajectory"]):
        return "risk_trends"
    elif any(w in msg for w in ["categor","breakdown","types","domain","classify"]):
        return "categorize_risks"
    else:
        return "executive_report"


class MockMCP:
    def __init__(self):
        self._tools = {}
    def tool(self):
        parent = self
        class Dec:
            def __call__(self, f):
                parent._tools[f.__name__] = f
                return f
        return Dec()


async def run_best_tool(ticker: str, message: str) -> tuple:
    hint = get_tool_hint(message)
    print(f"[bridge] hint={hint} ticker={ticker}")

    mock = MockMCP()

    try:
        if hint == "compare_filings":
            from tools.compare_filings import register_compare_filings
            register_compare_filings(mock)
        elif hint == "risk_trends":
            from tools.risk_trends import register_risk_trends
            register_risk_trends(mock)
        elif hint == "categorize_risks":
            from tools.risk_categorizer import register_risk_categorizer
            register_risk_categorizer(mock)
        else:
            from tools.executive_report import register_executive_report
            register_executive_report(mock)
    except Exception as e:
        print(f"[bridge] register error: {e}")
        return f"Could not load tool: {e}", "error"

        registered_tool_names = list(mock._tools.keys())
        print(f"[bridge] registered tools: {registered_tool_names}")

        if not mock._tools:
            return f"No tools found for {hint}", "error"

        # Ensure only the intended tool is registered
        if len(registered_tool_names) != 1:
            print(f"[bridge] WARNING: Expected 1 tool, but found {len(registered_tool_names)}: {registered_tool_names}")
            # Attempt to find the correct tool if multiple are registered
            expected_tool_name = ""
            if hint == "compare_filings":
                expected_tool_name = "compare_filings"
            elif hint == "risk_trends":
                expected_tool_name = "risk_trends"
            elif hint == "categorize_risks":
                expected_tool_name = "categorize_risks"
            else: # executive_report
                expected_tool_name = "generate_executive_report"

            if expected_tool_name in mock._tools:
                name = expected_tool_name
                func = mock._tools[expected_tool_name]
                print(f"[bridge] Corrected to call {name}")
            else:
                return f"Could not find expected tool '{expected_tool_name}' among registered tools: {registered_tool_names}", "error"
        else:
            name, func = next(iter(mock._tools.items()))

        print(f"[bridge] calling {name}")

    try:
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        print(f"[bridge] params: {params}")

        if 'ticker' in params and 'form_type' in params:
            result = await func(ticker=ticker, form_type="10-K")
        elif 'ticker' in params:
            result = await func(ticker=ticker)
        elif len(params) >= 1:
            result = await func(ticker)
        else:
            result = await func()

        if isinstance(result, dict):
            return json.dumps(result, indent=2), name
        return str(result), name

    except Exception as e:
        import traceback
        print(f"[bridge] call error: {e}")
        print(traceback.format_exc())
        return f"Tool error: {e}", "error"


async def call_grok(api_key: str, user_message: str,
                    context: str, ticker: str = "") -> str:
    try:
        if ticker and context:
            prompt = f"""You are RiskLens, an expert AI financial analyst.

User asked: "{user_message}"

SEC filing analysis for {ticker}:
{context[:3000]}

Respond as an expert analyst. Be clear, insightful and conversational.
Highlight the most important findings and what they mean for investors."""
        else:
            prompt = f"""You are RiskLens, an expert AI financial analyst.

User asked: "{user_message}"

Answer helpfully. If they mention a company suggest:
"Try: Analyze AAPL or Compare TSLA filings" """

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "grok-3-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2048,
                    "temperature": 0.7
                }
            )
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            elif response.status_code == 429:
                return (
                    "⚠️ **API Quota Limit Reached**\n\n"
                    "Your Grok API key has hit its usage limit.\n\n"
                    "**What to do:**\n"
                    "• Go to ⚙️ Settings → AI Connection → Change Provider\n"
                    "• Or wait for your quota to reset\n\n"
                    f"**Raw filing data:**\n\n{context[:2000]}"
                )
            elif response.status_code == 401:
                return (
                    "❌ **Invalid API Key**\n\n"
                    "Your Grok API key is invalid.\n"
                    "Go to ⚙️ Settings → AI Connection and reconnect."
                )
            else:
                error_msg = f"Grok API Error {response.status_code}: {response.text[:200]}"
                print(f"[grok] {error_msg}")
                return error_msg if not context else f"{error_msg}\n\n**Raw filing data:**\n\n{context[:2000]}"
    except Exception as e:
        error_msg = f"Grok Exception: {e}"
        print(f"[grok] {error_msg}")
        return error_msg if not context else f"{error_msg}\n\n**Raw filing data:**\n\n{context[:2000]}"


async def call_gemini(api_key: str, user_message: str,
                      context: str, ticker: str = "") -> str:
    try:
        if ticker and context:
            prompt = f"""You are RiskLens, an expert AI financial analyst.

User asked: "{user_message}"

SEC filing analysis for {ticker}:
{context[:3000]}

Respond as an expert analyst. Be clear and conversational."""
        else:
            prompt = f"""You are RiskLens financial analyst.
User asked: "{user_message}"
Answer helpfully."""

        models = ["gemini-2.0-flash", "gemini-1.5-flash"]
        for model in models:
            url = (f"https://generativelanguage.googleapis.com/v1beta"
                   f"/models/{model}:generateContent?key={api_key}")
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(url, json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.7,
                        "maxOutputTokens": 2048
                    }
                })
                if response.status_code == 200:
                    data = response.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                elif response.status_code == 429:
                    error_msg = f"Gemini API Error {model} quota exceeded"
                    print(f"[gemini] {error_msg}")
                    continue
                else:
                    error_msg = f"Gemini API Error {model} {response.status_code}: {response.text[:200]}"
                    print(f"[gemini] {error_msg}")
                    continue

        return (
            "⚠️ **API Quota Limit Reached**\n\n"
            "Your Gemini API key has hit its daily limit.\n\n"
            "**What to do:**\n"
            "• Go to ⚙️ Settings → AI Connection → Change Provider\n"
            "• Get a new key at https://aistudio.google.com\n\n"
            f"**Raw filing data:**\n\n{context[:2000]}"
        )
    except Exception as e:
        error_msg = f"Gemini Exception: {e}"
        print(f"[gemini] {error_msg}")
        return error_msg if not context else f"{error_msg}\n\n**Raw filing data:**\n\n{context[:2000]}"


async def call_claude(api_key: str, user_message: str,
                      context: str, ticker: str = "") -> str:
    try:
        prompt = f"""You are RiskLens financial analyst.
User asked: "{user_message}"
{"SEC filing data for " + ticker + ": " + context[:3000] if context else ""}
Respond helpfully and clearly."""

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "claude-3-5-haiku-20241022",
                    "max_tokens": 2048,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            if response.status_code == 200:
                data = response.json()
                return data["content"][0]["text"]
            elif response.status_code == 429:
                return (
                    "⚠️ **API Quota Limit Reached**\n\n"
                    "Your Claude API key has hit its limit.\n"
                    "Go to ⚙️ Settings → AI Connection → Change Provider.\n\n"
                    f"**Raw data:**\n\n{context[:2000]}"
                )
            else:
                error_msg = f"Claude API Error {response.status_code}: {response.text[:200]}"
                print(f"[claude] {error_msg}")
                return error_msg if not context else f"{error_msg}\n\n**Raw data:**\n\n{context[:2000]}"
    except Exception as e:
        error_msg = f"Claude Exception: {e}"
        print(f"[claude] {error_msg}")
        return error_msg if not context else f"{error_msg}\n\n**Raw data:**\n\n{context[:2000]}"


async def process_message(user_id: str,
                          conversation_id: str,
                          content: str) -> dict:
    supabase = get_supabase_client()
    print(f"[process] user:{user_id} msg:{content[:60]}")

    try:
        supabase.table("messages").insert({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": "user",
            "content": content
        }).execute()
    except Exception as e:
        print(f"[process] save user msg error: {e}")

    ticker = extract_ticker(content)
    print(f"[process] ticker: '{ticker}'")

    ai_key = None
    ai_provider = None
    try:
        key_result = supabase.table("user_ai_keys").select(
            "provider,api_key"
        ).eq("user_id", user_id).execute()
        if key_result.data:
            ai_provider = key_result.data[0]["provider"]
            ai_key = key_result.data[0]["api_key"]
            print(f"[process] using {ai_provider}")
    except Exception as e:
        print(f"[process] key fetch error: {e}")

    tool_result = ""
    tool_name = None

    if ticker:
        tool_result, tool_name = await run_best_tool(ticker, content)
        print(f"[process] tool={tool_name} result_len={len(tool_result)}")

    if ai_key:
        if ai_provider == "grok":
            ai_response = await call_grok(ai_key, content, tool_result, ticker)
        elif ai_provider == "gemini":
            ai_response = await call_gemini(ai_key, content, tool_result, ticker)
        elif ai_provider == "claude":
            ai_response = await call_claude(ai_key, content, tool_result, ticker)
        else:
            ai_response = tool_result or "Please connect an AI provider in Settings."
    elif tool_result:
        ai_response = tool_result
    else:
        ai_response = (
            "Hi! I'm RiskLens, your AI financial analyst. "
            "Ask me about any company's SEC filings!\n\n"
            "Try:\n• 'Analyze AAPL'\n• 'Compare TSLA filings'\n"
            "• 'What are MSFT risk trends?'"
        )

    try:
        supabase.table("messages").insert({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": "assistant",
            "content": ai_response,
            "tool_used": tool_name,
            "ticker": ticker or None
        }).execute()
    except Exception as e:
        print(f"[process] save response error: {e}")

    try:
        existing = supabase.table("user_plans").select(
            "analyses_used"
        ).eq("user_id", user_id).execute()
        if existing.data:
            current = existing.data[0]["analyses_used"]
            supabase.table("user_plans").update({
                "analyses_used": current + 1
            }).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"[process] usage update error: {e}")

    return {
        "content": ai_response,
        "tool_used": tool_name,
        "ticker": ticker or None
    }