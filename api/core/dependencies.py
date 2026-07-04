import os
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from api.core.database import get_supabase_client
from api.services.usage_service import UsageService

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
    current_user: dict = Depends(get_current_user),
    supabase_client = Depends(get_supabase_client)
):
    try:
        usage_service = UsageService(supabase_client)
        user_id = current_user["user_id"]

        is_within_limit = await usage_service.check_limit(user_id)

        if not is_within_limit:
            # Fetch current usage details to provide a more informative message
            usage_details = await usage_service.get_usage(user_id)
            raise HTTPException(
                status_code=403,
                detail=f"Usage limit reached ({usage_details.get('analyses_used', 0)}/{usage_details.get('limit', 0)}). Please upgrade your plan."
            )
        return current_user

    except HTTPException:
        raise
    except Exception as e:
        print(f"[usage] Error in require_usage_limit: {e}")
        # In case of an error, we should deny access to prevent potential abuse
        raise HTTPException(status_code=500, detail="Could not verify usage limits.")


async def get_subscription(
    current_user: dict = Depends(get_current_user),
    supabase_client = Depends(get_supabase_client)
):
    try:
        user_id = current_user["user_id"]
        response = supabase_client.table("subscriptions").select("*").eq("user_id", user_id).single().execute()
        
        if response.data:
            return response.data
        
        # Default to a free plan if no subscription is found
        return {
            "id": None,
            "user_id": user_id,
            "plan": "free",
            "status": "active", # Free plan is always active
            "current_period_start": None,
            "current_period_end": None,
            "team_seats": 1,
            "created_at": None,
            "updated_at": None,
        }
    except Exception as e:
        print(f"[subscription] Error getting subscription for user {user_id}: {e}")
        # Fallback to a default free plan in case of error
        return {
            "id": None,
            "user_id": current_user["user_id"],
            "plan": "free",
            "status": "active",
            "current_period_start": None,
            "current_period_end": None,
            "team_seats": 1,
            "created_at": None,
            "updated_at": None,
        }
