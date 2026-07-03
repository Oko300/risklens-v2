import os
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials
    try:
        jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        if not jwt_secret:
            raise HTTPException(status_code=500,
              detail="JWT secret not configured")

        payload = jwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False}
        )

        user_id = payload.get("sub")
        email = payload.get("email", "")

        if not user_id:
            raise HTTPException(status_code=401,
              detail="Invalid token")

        return {"user_id": user_id, "email": email}

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401,
          detail="Token expired. Please sign in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401,
          detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[auth] JWT error: {e}")
        raise HTTPException(status_code=401,
          detail="Could not validate credentials")


async def require_usage_limit(
    current_user: dict = Depends(get_current_user)
):
    try:
        from api.core.database import get_supabase_client
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
        print(f"[usage] Error checking limit: {e}")
        return current_user