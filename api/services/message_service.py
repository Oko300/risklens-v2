import uuid
from typing import List, Dict, Any, Optional
from supabase import Client
import httpx
import os

from api.models.schemas import MessageCreate, Message
from api.services.tool_runner import ToolRunnerService
from api.services.usage_service import UsageService

class MessageService:
    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.tool_runner = ToolRunnerService()
        self.usage_service = UsageService(supabase)
        self.ai_api_url = os.environ.get("AI_API_URL", "http://localhost:8000/chat") # Placeholder for AI service

    async def send_message(self, user_id: uuid.UUID, message_data: MessageCreate) -> Message:
        # Save user message
        user_message = {
            "conversation_id": str(message_data.conversation_id),
            "user_id": str(user_id),
            "role": "user",
            "content": message_data.content,
        }
        try:
            response = self.supabase.from_('messages').insert(user_message).execute()
            if not response.data:
                raise Exception("Failed to save user message.")
        except Exception as e:
            print(f"Error saving user message: {e}")
            raise

        # Fetch AI provider details
        ai_provider = None
        ai_api_key = None
        try:
            response = self.supabase.from_('user_ai_keys').select('provider, api_key').eq('user_id', str(user_id)).single().execute()
            if response.data:
                ai_provider = response.data['provider']
                ai_api_key = response.data['api_key']
        except Exception as e:
            print(f"No AI key found for user {user_id}: {e}")
            # Continue without AI key if not found, or raise an error if it's mandatory

        # Detect tool
        tool_name = self.tool_runner.detect_tool(message_data.content)
        ticker = self.tool_runner.extract_ticker(message_data.content)
        tool_result = None

        if tool_name:
            # Increment usage before running tool
            if not await self.usage_service.increment_usage(user_id):
                raise Exception("Usage limit exceeded. Cannot run tool.")

            tool_result = await self.tool_runner.run_tool(tool_name, ticker)
            if "error" in tool_result:
                ai_response_content = f"Error running tool {tool_name}: {tool_result['error']}"
            else:
                ai_response_content = f"Tool '{tool_name}' executed successfully. Result: {tool_result['result']}"
        else:
            ai_response_content = "No specific tool detected. I can help with executive reports, comparing filings, risk trends, or categorizing risks."

        # Send to AI for natural language explanation (if AI provider is connected)
        if ai_provider and ai_api_key:
            try:
                async with httpx.AsyncClient() as client:
                    ai_payload = {
                        "model": ai_provider,
                        "messages": [
                            {"role": "user", "content": message_data.content},
                            {"role": "assistant", "content": ai_response_content} # Include tool result for context
                        ]
                    }
                    headers = {"Authorization": f"Bearer {ai_api_key}"} # Assuming AI service uses bearer token
                    ai_response = await client.post(self.ai_api_url, json=ai_payload, headers=headers)
                    ai_response.raise_for_status()
                    ai_response_content = ai_response.json().get("choices")[0].get("message").get("content")
            except httpx.HTTPStatusError as e:
                print(f"Error calling AI service: {e.response.status_code} - {e.response.text}")
                ai_response_content = f"Error communicating with AI provider: {e.response.text}"
            except Exception as e:
                print(f"Unexpected error with AI service: {e}")
                ai_response_content = f"An unexpected error occurred with the AI service: {str(e)}"

        # Save AI response
        assistant_message = {
            "conversation_id": str(message_data.conversation_id),
            "user_id": str(user_id),
            "role": "assistant",
            "content": ai_response_content,
            "tool_used": tool_name,
            "ticker": ticker
        }
        try:
            response = self.supabase.from_('messages').insert(assistant_message).execute()
            if response.data:
                return Message(**response.data[0])
            raise Exception("Failed to save AI response.")
        except Exception as e:
            print(f"Error saving AI response: {e}")
            raise

    async def get_messages(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> List[Message]:
        try:
            response = self.supabase.from_('messages').select('*').eq('conversation_id', str(conversation_id)).eq('user_id', str(user_id)).order('created_at', desc=False).execute()
            if response.data:
                return [Message(**msg) for msg in response.data]
            return []
        except Exception as e:
            print(f"Error fetching messages for conversation {conversation_id}: {e}")
            raise