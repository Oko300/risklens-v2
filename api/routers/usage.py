from fastapi import APIRouter, HTTPException, Depends
from api.core.dependencies import get_current_user
from api.core.database import get_supabase_client

router = APIRouter()

@router.get("/usage/me")
async def get_usage(current_user: dict = Depends(get_current_user)):
    try:
        supabase = get_supabase_client()
        user_id = current_user["user_id"]
        
        result = supabase.table("user_plans").select("*").eq(
            "user_id", user_id
        ).execute()
        
        if not result.data:
            supabase.table("user_plans").insert({
                "user_id": user_id,
                "plan": "free_trial",
                "analyses_used": 0
            }).execute()
            return {
                "plan": "free_trial",
                "analyses_used": 0,
                "limit": 10,
                "days_remaining": 30
            }
        
        data = result.data[0]
        plan = data.get("plan", "free_trial")
        used = data.get("analyses_used", 0)
        limits = {"free_trial": 10, "pro": 500, "business": 999999}
        limit = limits.get(plan, 10)
        
        return {
            "plan": plan,
            "analyses_used": used,
            "limit": limit,
            "days_remaining": 30
        }
    except Exception as e:
        print(f"[usage] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))