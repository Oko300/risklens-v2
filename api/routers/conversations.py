from fastapi import APIRouter, HTTPException, Depends, Request
from api.core.dependencies import get_current_user
from api.core.database import get_supabase_client

router = APIRouter()

@router.post("/conversations")
async def create_conversation(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    try:
        body = await request.json()
        title = body.get("title", "New Analysis")
        supabase = get_supabase_client()
        result = supabase.table("conversations").insert({
            "user_id": current_user["user_id"],
            "title": title
        }).execute()
        if result.data:
            return result.data[0]
        raise HTTPException(status_code=400, 
          detail="Failed to create conversation")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[conv] Create error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/conversations")
async def list_conversations(
    current_user: dict = Depends(get_current_user)
):
    try:
        supabase = get_supabase_client()
        result = supabase.table("conversations").select("*").eq(
            "user_id", current_user["user_id"]
        ).order("created_at", desc=True).execute()
        return result.data or []
    except Exception as e:
        print(f"[conv] List error: {e}")
        return []

@router.delete("/conversations/{conv_id}")
async def delete_conversation(
    conv_id: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        supabase = get_supabase_client()
        supabase.table("conversations").delete().eq(
            "id", conv_id
        ).eq("user_id", current_user["user_id"]).execute()
        return {"deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))