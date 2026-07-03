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

def detect_tool(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in 
           ["executive report", "analyst report", "full report"]):
        return "generate_executive_report"
    if any(w in text_lower for w in 
           ["compare", "vs", "versus", "difference"]):
        return "compare_filings"
    if any(w in text_lower for w in 
           ["trend", "history", "years", "over time", "multiple"]):
        return "analyze_risk_trends"
    if any(w in text_lower for w in 
           ["categor", "breakdown", "types of risk", "domain"]):
        return "categorize_risks"
    if any(w in text_lower for w in 
           ["8-k", "8k", "event", "recent filing", "latest"]):
        return "generate_executive_report"
    return "generate_executive_report"

async def run_tool(tool_name: str, ticker: str) -> str:
    try:
        if tool_name == "generate_executive_report":
            from tools.executive_report import generate_executive_report
            result = generate_executive_report(
                ticker=ticker, form_type="10-K"
            )
        elif tool_name == "compare_filings":
            from tools.compare_filings import compare_filings
            result = compare_filings(
                ticker=ticker, form_type="10-K"
            )
        elif tool_name == "analyze_risk_trends":
            from tools.risk_trends import analyze_risk_trends
            result = analyze_risk_trends(
                ticker=ticker, form_type="10-K", n_filings=3
            )
        elif tool_name == "categorize_risks":
            from tools.risk_categorizer import categorize_risks
            result = categorize_risks(
                ticker=ticker, form_type="10-K"
            )
        else:
            return f"Unknown tool: {tool_name}"
        
        if isinstance(result, dict):
            import json
            return json.dumps(result, indent=2)
        return str(result)
        
    except Exception as e:
        print(f"[tool] Error running {tool_name}: {e}")
        return f"Error running analysis: {str(e)}"

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
    
    # Detect tool and ticker
    tool_name = detect_tool(content)
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
    
    # Run the tool
    print(f"[msg] Running {tool_name} for {ticker}")
    tool_result = await run_tool(tool_name, ticker)
    
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