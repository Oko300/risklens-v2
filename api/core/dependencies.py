import os
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from api.core.database import get_supabase_client

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials
    try:
        supabase = get_supabase_client()
        # Let Supabase verify the token - no manual JWT needed
        response = supabase.auth.get_user(token)
        
        if not response or not response.user:
            raise HTTPException(status_code=401, 
              detail="Invalid or expired token")
        
        user = response.user
        return {
            "user_id": user.id,
            "email": user.email or ""
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[auth] Error: {e}")
        raise HTTPException(status_code=401,
          detail="Could not validate credentials")


async def require_usage_limit(
    current_user: dict = Depends(get_current_user)
):
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
            return current_user

        plan_data = result.data[0]
        plan = plan_data.get("plan", "free_trial")
        used = plan_data.get("analyses_used", 0)
        limits = {"free_trial": 10, "pro": 500, "business": 999999}
        limit = limits.get(plan, 10)

        if used >= limit:
            raise HTTPException(
                status_code=403,
                detail=f"Usage limit reached ({used}/{limit}). Please upgrade."
            )
        return current_user

    except HTTPException:
        raise
    except Exception as e:
        print(f"[usage] Error: {e}")
        return current_user