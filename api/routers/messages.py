from fastapi import APIRouter, HTTPException, Depends, Request
from api.core.dependencies import get_current_user, require_usage_limit
from api.services.message_service import process_message
from api.core.database import get_supabase_client

router = APIRouter()

@router.post("/messages")
async def send_message(
    request: Request,
    current_user: dict = Depends(require_usage_limit)
):
    try:
        body = await request.json()
        conversation_id = body.get("conversation_id")
        content = body.get("content", "").strip()
        
        if not content:
            raise HTTPException(status_code=400, 
              detail="Message content required")
        
        if not conversation_id:
            raise HTTPException(status_code=400,
              detail="conversation_id required")
        
        result = await process_message(
            user_id=current_user["user_id"],
            conversation_id=conversation_id,
            content=content
        )
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[messages] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/messages/{conversation_id}")
async def get_messages(
    conversation_id: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        supabase = get_supabase_client()
        result = supabase.table("messages").select("*").eq(
            "conversation_id", conversation_id
        ).order("created_at").execute()
        return result.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))