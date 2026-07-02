import httpx
import json
from services.encryption_service import decrypt_api_key

SYSTEM_PROMPT = "You are RiskLens AI, an expert financial analyst specialising in SEC filing analysis. When given analysis data explain it in plain English. Focus on what risks mean for investors, what changed vs prior filing, and what action to consider. Be direct and avoid jargon. Never give specific buy/sell recommendations."

DEFAULT_MODELS = {
    "claude": "claude-sonnet-4-6",
    "grok": "grok-3-mini",
    "gemini": "gemini-2.0-flash",
}

async def get_ai_response(provider: str, api_key_enc: str, messages: list[dict], model: str = None) -> str:
    """
    Decrypts the API key and routes the request to the correct AI provider.
    """
    api_key = decrypt_api_key(api_key_enc)
    client = httpx.AsyncClient(timeout=60.0)
    
    if provider == "claude":
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        body = {
            "model": model if model else DEFAULT_MODELS["claude"],
            "max_tokens": 1024,
            "system": SYSTEM_PROMPT,
            "messages": messages
        }
        response = await client.post(url, headers=headers, json=body)
        
        if response.status_code != 200:
            raise ValueError(f"Claude API error: {response.status_code} - {response.text}")
        
        data = response.json()
        return data["content"][0]["text"]
    
    elif provider == "grok":
        url = "https://api.x.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json"
        }
        # Grok expects system message as part of the messages list
        grok_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
        body = {
            "model": model if model else DEFAULT_MODELS["grok"],
            "messages": grok_messages,
            "max_tokens": 1024
        }
        response = await client.post(url, headers=headers, json=body)
        
        if response.status_code != 200:
            raise ValueError(f"Grok API error: {response.status_code} - {response.text}")
        
        data = response.json()
        return data["choices"][0]["message"]["content"]
    
    elif provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model if model else DEFAULT_MODELS['gemini']}:generateContent"
        params = {"key": api_key}
        
        # Convert messages for Gemini format
        converted_messages = []
        for msg in messages:
            if msg["role"] == "user":
                converted_messages.append({"role": "user", "parts": [{"text": msg["content"]}]})
            elif msg["role"] == "assistant":
                converted_messages.append({"role": "model", "parts": [{"text": msg["content"]}]})
        
        body = {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": converted_messages,
            "generationConfig": {"maxOutputTokens": 1024}
        }
        response = await client.post(url, params=params, json=body)
        
        if response.status_code != 200:
            raise ValueError(f"Gemini API error: {response.status_code} - {response.text}")
        
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    
    else:
        raise ValueError(f"Unsupported AI provider: {provider}")