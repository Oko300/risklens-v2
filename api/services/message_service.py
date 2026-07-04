import re
import json
import httpx
import inspect
from api.core.database import get_supabase_client
from api.services.usage_service import UsageService
from api.services.tool_bridge import run_best_tool, get_tool_hint


def extract_ticker(text: str) -> str:
    skip = {
        "I","A","AN","THE","FOR","OF","AND","OR","IN","ON","AT",
        "TO","IS","IT","BE","MY","ME","WE","US","DO","GO","GET",
        "CAN","SEC","CEO","CFO","AI","ML","API","USD","UK","EU",
        "GDP","IPO","ETF","GENERATE","REPORT","ANALYZE","COMPARE",
        "SHOW","WHAT","HOW","WHY","WHEN","WHO","TELL","GIVE","RISK",
        "RISKS","TREND","TRENDS","FILING","ANNUAL","QUARTER","EXECUTIVE",
        "LATEST","HI","HELLO","HEY","PLEASE","THANKS","THANK","YES","NO",
        "TWO","LAST","OVER","TIME","DATA","NEWS","INFO","ABOUT","WITH",
        "FROM","YOUR","THEIR","ITS","HAS","HAVE","HAD","ARE","WAS","WERE",
        "CATEGORIZE","BREAKDOWN","TYPES","DOMAIN","CLASSIFY","CATEGORIES",
        "HISTORY","YEARS","PAST","TRAJECTORY","HISTORICALLY","DIFFERENCE",
        "VERSUS","BOTH","FILINGS","RECENT","NEW","OLD"
    }
    clean_text = re.sub(r'[*"\'`#]', ' ', text)
    words = clean_text.upper().split()
    for word in words:
        clean = re.sub(r"[^A-Z]", "", word)
        if 2 <= len(clean) <= 5 and clean not in skip:
            return clean
    return ""


def is_greeting_or_general(text: str, ticker: str) -> bool:
    if ticker:
        return False
    greetings = [
        "hi", "hello", "hey", "good morning", "good evening",
        "what can you do", "help me", "how does this work",
        "what is risklens", "what are you", "who are you"
    ]
    msg = text.lower().strip()
    is_short_generic = len(msg.split()) <= 3 and not any(
        w in msg for w in ["analyz", "compar", "risk", "filing", "report", "trend"]
    )
    return any(msg.startswith(g) for g in greetings) or is_short_generic


def tool_succeeded(tool_result: str, tool_name: str) -> bool:
    """
    Returns True only if the tool ran and returned real filing data.
    A count is only charged when this returns True.
    """
    if not tool_result or tool_name == "error":
        return False
    fail_phrases = [
        "pipeline_success=False",
        "Could not fetch",
        "Cik resolution failed",
        "No filing found",
        "Analysis failed",
        "Could not load",
        "Tool error",
        "No tool available",
        "No tools found",
    ]
    for phrase in fail_phrases:
        if phrase.lower() in tool_result.lower():
            return False
    # Must have meaningful length to count as real data
    return len(tool_result.strip()) > 100


def ai_call_succeeded(ai_response: str) -> bool:
    """
    Returns True only if AI returned a real response, not an error/quota message.
    """
    if not ai_response:
        return False
    error_phrases = [
        "api quota reached",
        "api key error",
        "invalid api key",
        "403 forbidden",
        "connection error",
        "api error",
        "exception",
    ]
    lower = ai_response.lower()
    return not any(phrase in lower for phrase in error_phrases)


FRIENDLY_INTRO = """👋 Hi! I'm **RiskLens**, your AI-powered SEC filing analyst.

Here's what I can do for you:

📊 **Analyze a company** — *"Analyze AAPL"* or *"Give me a risk report on TSLA"*
🔍 **Compare filings** — *"Compare MSFT's last two filings"*
📈 **Risk trends** — *"Show AMZN risk trends over time"*
🗂 **Risk breakdown** — *"Categorize risks for NVDA"*

Just mention a stock ticker and I'll pull the latest SEC data for you!"""


async def call_grok(api_key: str, user_message: str, context: str, ticker: str = "") -> str:
    try:
        if ticker and context:
            prompt = f"""You are RiskLens, a friendly and expert AI financial analyst helping investors understand SEC filings.

The user asked: "{user_message}"

Here is the SEC filing analysis for {ticker}:
{context[:3000]}

Respond in a clear, friendly, and insightful way. Highlight the most important findings and what they mean for investors. Use bullet points where helpful."""
        else:
            prompt = f"""You are RiskLens, a friendly AI financial analyst.
The user said: "{user_message}"
Help them out warmly."""

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "grok-3-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2048,
                    "temperature": 0.7
                }
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]
            elif response.status_code == 429:
                return "⚠️ **Grok API quota reached.**\n\nGo to ⚙️ Settings → AI Connection and update your key."
            elif response.status_code == 401:
                return "❌ **Invalid Grok API key.** Go to ⚙️ Settings → AI Connection and reconnect."
            else:
                return f"❌ Grok API error {response.status_code}. Please try again."
    except Exception as e:
        print(f"[grok] exception: {e}")
        return f"❌ Grok connection error. Please try again."


async def call_gemini(api_key: str, user_message: str, context: str, ticker: str = "") -> str:
    try:
        if ticker and context:
            prompt = f"""You are RiskLens, a friendly and expert AI financial analyst.
The user asked: "{user_message}"
SEC filing analysis for {ticker}:
{context[:3000]}
Respond clearly and conversationally. Highlight key risks and what they mean for investors."""
        else:
            prompt = f"""You are RiskLens, a friendly AI financial analyst. The user said: "{user_message}". Help them out warmly."""

        models = ["gemini-2.0-flash", "gemini-1.5-flash"]
        last_error = ""
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(url, json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}
                })
                if response.status_code == 200:
                    return response.json()["candidates"][0]["content"]["parts"][0]["text"]
                elif response.status_code == 429:
                    last_error = "quota"
                    continue
                elif response.status_code == 403:
                    last_error = "forbidden"
                    continue
                else:
                    last_error = str(response.status_code)
                    continue

        if last_error == "quota":
            return ("⚠️ **Gemini API quota reached.**\n\n"
                    "Go to ⚙️ Settings → AI Connection and update your key, "
                    "or get a new one at https://aistudio.google.com")
        elif last_error == "forbidden":
            return ("❌ **Gemini API key error (403 Forbidden).**\n\n"
                    "• Go to https://aistudio.google.com and generate a fresh key\n"
                    "• Then update it in ⚙️ Settings → AI Connection")
        else:
            return "❌ **Gemini API error.** Go to ⚙️ Settings → AI Connection and reconnect."
    except Exception as e:
        print(f"[gemini] exception: {e}")
        return "❌ Gemini connection error. Please try again."


async def call_claude(api_key: str, user_message: str, context: str, ticker: str = "") -> str:
    try:
        prompt = f"""You are RiskLens, a friendly and expert AI financial analyst.
The user asked: "{user_message}"
{"SEC filing data for " + ticker + ": " + context[:3000] if context else "Help the user warmly and suggest they provide a ticker symbol."}
Respond clearly, helpfully, and in a friendly tone."""

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
                return response.json()["content"][0]["text"]
            elif response.status_code == 429:
                return "⚠️ **Claude API quota reached.**\n\nGo to ⚙️ Settings → AI Connection and update your key."
            else:
                return f"❌ Claude API error {response.status_code}. Please try again."
    except Exception as e:
        print(f"[claude] exception: {e}")
        return "❌ Claude connection error. Please try again."


async def process_message(user_id: str, conversation_id: str, content: str) -> dict:
    supabase = get_supabase_client()
    usage_service = UsageService(supabase)
    print(f"[process] user:{user_id} msg:{content[:60]}")

    # Save user message
    try:
        supabase.table("messages").insert({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": "user",
            "content": content
        }).execute()
    except Exception as e:
        print(f"[process] save user msg error: {e}")

    # Extract ticker FIRST
    ticker = extract_ticker(content)
    print(f"[process] ticker: '{ticker}'")

    # Handle greetings — no tool, no usage charge
    if is_greeting_or_general(content, ticker):
        ai_response = FRIENDLY_INTRO
        try:
            supabase.table("messages").insert({
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "assistant",
                "content": ai_response,
                "tool_used": None,
                "ticker": None
            }).execute()
        except Exception as e:
            print(f"[process] save greeting error: {e}")
        return {"content": ai_response, "tool_used": None, "ticker": None}

    # Always fetch fresh AI key from DB
    ai_key = None
    ai_provider = None
    try:
        key_result = supabase.table("user_ai_keys").select("provider,api_key").eq("user_id", user_id).execute()
        if key_result.data:
            ai_provider = key_result.data[0]["provider"]
            ai_key = key_result.data[0]["api_key"]
            print(f"[process] loaded {ai_provider} key ending ...{ai_key[-6:] if ai_key else 'none'}")
    except Exception as e:
        print(f"[process] key fetch error: {e}")

    # Run exactly one tool if ticker found
    tool_result = ""
    tool_name = None
    filing_succeeded = False

    if ticker:
        tool_result, tool_name = await run_best_tool(ticker, content)
        filing_succeeded = tool_succeeded(tool_result, tool_name)
        print(f"[process] tool={tool_name} succeeded={filing_succeeded} result_len={len(tool_result)}")

    # Call AI provider
    ai_response = ""
    ai_succeeded = False

    if ai_key:
        if ai_provider == "grok":
            ai_response = await call_grok(ai_key, content, tool_result, ticker)
        elif ai_provider == "gemini":
            ai_response = await call_gemini(ai_key, content, tool_result, ticker)
        elif ai_provider == "claude":
            ai_response = await call_claude(ai_key, content, tool_result, ticker)
        else:
            ai_response = tool_result or "Please connect an AI provider in ⚙️ Settings."
        ai_succeeded = ai_call_succeeded(ai_response)
    elif tool_result and tool_name != "error":
        ai_response = tool_result
        ai_succeeded = filing_succeeded
    elif ticker and not ai_key:
        ai_response = (f"I pulled filing data for **{ticker}** but you haven't connected an AI provider yet.\n\n"
                       "Go to ⚙️ Settings → AI Connection to add your Grok, Gemini, or Claude key.")
    else:
        ai_response = FRIENDLY_INTRO

    # Save assistant response
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

    # Only charge usage if tool got real filing data AND AI responded successfully
    should_charge = filing_succeeded and ai_succeeded
    print(f"[process] should_charge={should_charge} (filing={filing_succeeded}, ai={ai_succeeded})")

    if should_charge:
        try:
            await usage_service.increment_usage(user_id)
        except Exception as e:
            print(f"[process] usage increment error: {e}")
    else:
        print(f"[process] usage NOT charged — no successful filing delivery")

    return {"content": ai_response, "tool_used": tool_name, "ticker": ticker or None}