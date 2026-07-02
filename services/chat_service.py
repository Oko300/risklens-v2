import asyncio
import json
from typing import Optional

async def build_context_window(
    conversation_id: str,
    user_message: str,
    analysis_id: Optional[str] = None,
    fresh_tool_output: Optional[dict] = None,
    fresh_tool_name: Optional[str] = None
) -> list[dict]:
    """
    Builds a list of messages for sending to the AI, including analysis context and chat history.
    """
    from db.client import get_admin_client # Imported inside to avoid event loop issues
    supabase = get_admin_client()
    messages_context = []

    # 1. If analysis_id given: load from analyses table
    if analysis_id:
        def fetch_analysis_sync():
            return supabase.from_('analyses').select(
                "ticker, tool_name, tool_output, ai_interpretation"
            ).eq("id", analysis_id).single().execute()

        analysis_response = await asyncio.to_thread(fetch_analysis_sync)
        analysis_data = analysis_response.data

        if analysis_data:
            summary_parts = []
            summary_parts.append(f"Ticker: {analysis_data['ticker']}")
            summary_parts.append(f"Tool: {analysis_data['tool_name']}")
            
            # Attempt to parse tool_output if it's a string
            tool_output_content = analysis_data['tool_output']
            if isinstance(tool_output_content, str):
                try:
                    tool_output_content = json.loads(tool_output_content)
                except json.JSONDecodeError:
                    pass # Keep as string if not valid JSON

            if isinstance(tool_output_content, dict):
                # Summarize based on tool_name, similar to fresh_tool_output logic
                if analysis_data['tool_name'] == "generate_executive_report":
                    summary_parts.append(f"Report: {tool_output_content.get('report', '')[:1500]}")
                elif analysis_data['tool_name'] == "compare_filings":
                    scoring = tool_output_content.get('scoring', {})
                    summary_parts.append(f"Overall Materiality: {scoring.get('overall_materiality', '')}")
                    summary_parts.append(f"Top Signals: {scoring.get('top_signals', '')}")
                elif analysis_data['tool_name'] == "categorize_risks":
                    summary = tool_output_content.get('summary', {})
                    summary_parts.append(f"Executive Summary: {summary.get('executive_summary', '')}")
                elif analysis_data['tool_name'] == "analyze_8k_events":
                    summary_parts.append(f"Highest Risk Event: {tool_output_content.get('highest_risk_event', '')}")
                    events = tool_output_content.get('events', [])
                    if events:
                        summary_parts.append(f"Recent Events: {json.dumps(events[:3])}")
                elif analysis_data['tool_name'] in ["analyze_insider_activity", "analyze_ownership", "analyze_proxy"]:
                    summary_parts.append(f"Summary: {tool_output_content.get('summary', '')}")
                elif analysis_data['tool_name'] == "analyze_risk_trends":
                    summary = tool_output_content.get('summary', {})
                    summary_parts.append(f"Analyst Summary: {summary.get('analyst_summary', '')}")
                else:
                    summary_parts.append(f"Tool Output: {json.dumps(tool_output_content)[:1500]}")
            else:
                summary_parts.append(f"Tool Output: {str(tool_output_content)[:1500]}")

            if analysis_data['ai_interpretation']:
                summary_parts.append(f"AI Interpretation: {analysis_data['ai_interpretation']}")

            summary_string = "\n".join(summary_parts)
            
            messages_context.append({"role": "user", "content": f"Here is the analysis context:\n{summary_string[:2000]}"})
            messages_context.append({"role": "assistant", "content": "I have reviewed the analysis. Ready to discuss."})

    # 2. Load last 12 messages from messages table
    def fetch_messages_sync():
        return supabase.from_('messages').select(
            "role, content"
        ).eq("conversation_id", conversation_id).order("created_at", desc=False).limit(12).execute()

    messages_response = await asyncio.to_thread(fetch_messages_sync)
    for msg in messages_response.data:
        if msg["role"] in ["user", "assistant"]:
            messages_context.append({"role": msg["role"], "content": msg["content"]})

    # 3. If fresh_tool_output given: add user message summarising the tool result
    if fresh_tool_output and fresh_tool_name:
        tool_summary = ""
        if fresh_tool_name == "generate_executive_report":
            tool_summary = fresh_tool_output.get("report", "")[:1500]
        elif fresh_tool_name == "compare_filings":
            scoring = fresh_tool_output.get('scoring', {})
            tool_summary = f"Overall Materiality: {scoring.get('overall_materiality', '')}\nTop Signals: {scoring.get('top_signals', '')}"
        elif fresh_tool_name == "categorize_risks":
            summary = fresh_tool_output.get('summary', {})
            tool_summary = summary.get("executive_summary", "")
        elif fresh_tool_name == "analyze_8k_events":
            highest_risk = fresh_tool_output.get("highest_risk_event", "")
            events = fresh_tool_output.get("events", [])
            tool_summary = f"Highest Risk Event: {highest_risk}\nRecent Events: {json.dumps(events[:3])}"
        elif fresh_tool_name in ["analyze_insider_activity", "analyze_ownership", "analyze_proxy"]:
            tool_summary = fresh_tool_output.get("summary", "")
        elif fresh_tool_name == "analyze_risk_trends":
            summary = fresh_tool_output.get('summary', {})
            tool_summary = summary.get("analyst_summary", "")
        else:
            tool_summary = json.dumps(fresh_tool_output)[:1500]
        
        if tool_summary:
            messages_context.append({"role": "user", "content": f"Here is the result of the tool I just ran:\n{tool_summary}"})

    # 4. Append the actual user_message as final user turn.
    messages_context.append({"role": "user", "content": user_message})

    return messages_context