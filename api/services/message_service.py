import os
import re
import httpx
from api.core.database import get_supabase_client

def extract_ticker(text: str) -> str:
    words = text.upper().split()
    skip = {"FOR", "THE", "OF", "AND", "A", "AN", "IN", 
            "GENERATE", "REPORT", "ANALYZE", "COMPARE",
            "EXECUTIVE", "RISK", "TRENDS", "FILINGS",
            "ABOUT", "ME", "SHOW", "GET", "WHAT", "IS"}
    for word in words:
        clean = re.sub(r'[^A-Z]', '', word)
        if 2 <= len(clean) <= 5 and clean not in skip:
            return clean
    return ""

async def run_best_tool(ticker: str, message: str) -> tuple:
    from api.services.tool_bridge import run_tool
    
    msg_lower = message.lower()
    
    if any(w in msg_lower for w in 
           ["compare", "vs", "versus", "difference"]):
        tool_hint = "compare_filings"
    elif any(w in msg_lower for w in 
             ["trend", "history", "years", "over time"]):
        tool_hint = "risk_trends"
    elif any(w in msg_lower for w in 
             ["categor", "breakdown", "types", "domain"]):
        tool_hint = "categorize_risks"
    else:
        tool_hint = "executive_report"
    
    print(f"[msg] Using tool_hint: {tool_hint} for ticker: {ticker}")
    result = await run_tool(tool_hint, ticker)
    return result, tool_hint

async def call_gemini(api_key: str, 
                      user_message: str, 
                      tool_result: str,
                      ticker: str,
                      tool_name: str) -> str:
    try:
        prompt = f"""You are RiskLens, an expert financial analyst 
specializing in SEC filing analysis.

The user asked: "{user_message}"

I ran the {tool_name} tool for {ticker} and got these results:

{tool_result}

Please provide a clear, insightful analysis of these results in 
plain conversational language. 
- Start with a one-line summary of the overall risk level
- Highlight the 3 most important findings
- Explain what this means for investors
- End with a brief outlook

Be direct and professional. Use clear paragraphs, not bullet points 
unless listing specific risk items."""

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 1500
                }
            })
            
            if response.status_code == 200:
                data = response.json()
                return (data["candidates"][0]["content"]
                        ["parts"][0]["text"])
            elif response.status_code == 429:
                print("[gemini] Quota exceeded")
                return (
                    "⚠️ **API Quota Limit Reached**\n\n"
                    "Your current API key has hit its daily usage limit.\n\n"
                    "**What to do now:**\n"
                    "• Go to ⚙️ **Account Settings → AI Connection → "
                    "Change Provider** and paste a fresh API key\n"
                    "• Or wait 24 hours for the quota to reset\n"
                    "• Get a new free key at: https://aistudio.google.com\n\n"
                    "**Your filing data is ready — just needs AI interpretation:**\n\n"
                    f"{tool_result[:2000]}"
                )
            elif response.status_code == 401:
                return (
                    "❌ **Invalid API Key**\n\n"
                    "Your Gemini API key is invalid or has been revoked.\n"
                    "Please go to **Account Settings → AI Connection** "
                    "and reconnect with a valid key from "
                    "https://aistudio.google.com"
                )
            elif response.status_code == 403:
                return (
                    "❌ **API Access Denied**\n\n"
                    "Your Gemini API key does not have permission to use "
                    "this model. Please check your Google AI Studio account "
                    "or try a different API key."
                )
            else:
                print(f"[gemini] Error: {response.text}")
                return tool_result
                
    except Exception as e:
        print(f"[gemini] Error: {e}")
        return tool_result

async def process_message(user_id: str, 
                          conversation_id: str,
                          content: str) -> dict:
    supabase = get_supabase_client()
    
    # Save user message
    try:
        supabase.table("messages").insert({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": "user",
            "content": content
        }).execute()
    except Exception as e:
        print(f"[msg] Save user msg error: {e}")
    
    # Detect ticker
    ticker = extract_ticker(content)
    
    if not ticker:
        reply = ("Please specify a company ticker symbol. "
                "For example: 'Generate executive report for AAPL' "
                "or 'Analyze risk trends for TSLA'")
        try:
            supabase.table("messages").insert({
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "assistant",
                "content": reply
            }).execute()
        except:
            pass
        return {"content": reply, "tool_used": None}
    
    if ticker:
        tool_result, tool_name = await run_best_tool(ticker, content)
    else:
        tool_result = ""
        tool_name = None
    
    # Get user's AI key
    ai_response = tool_result
    try:
        key_result = supabase.table("user_ai_keys").select(
            "provider, api_key"
        ).eq("user_id", user_id).execute()
        
        if key_result.data:
            provider = key_result.data[0]["provider"]
            api_key = key_result.data[0]["api_key"]
            
            if provider == "gemini":
                ai_response = await call_gemini(
                    api_key, content, tool_result, 
                    ticker, tool_name
                )
            else:
                ai_response = tool_result
    except Exception as e:
        print(f"[msg] AI call error: {e}")
        ai_response = tool_result
    
    # Save assistant response
    try:
        supabase.table("messages").insert({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": "assistant",
            "content": ai_response,
            "tool_used": tool_name,
            "ticker": ticker
        }).execute()
    except Exception as e:
        print(f"[msg] Save assistant msg error: {e}")
    
    # Update usage count
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
        print(f"[msg] Usage update error: {e}")
    
    return {
        "content": ai_response,
        "tool_used": tool_name,
        "ticker": ticker
    }