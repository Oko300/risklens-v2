import os
from datetime import datetime, timedelta
from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jose import JWTError
from pydantic import BaseModel
from supabase import Client

from api.core.database import get_supabase_client
from api.models.schemas import UserInfo
from api.services.usage_service import UsageService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "super-secret-key")
ALGORITHM = "HS256"

class TokenData(BaseModel):
    id: str | None = None

async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    supabase: Annotated[Client, Depends(get_supabase_client)]
) -> UserInfo:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        token_data = TokenData(id=user_id)
    except JWTError:
        raise credentials_exception

    try:
        # Fetch user from Supabase auth
        user_response = supabase.auth.get_user(token)
        user = user_response.user
        if not user:
            raise credentials_exception

        # Fetch user plan from user_plans table
        user_plan_response = supabase.from_('user_plans').select('*').eq('user_id', user.id).single().execute()
        user_plan_data = user_plan_response.data

        if not user_plan_data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User plan not found")

        usage_service = UsageService(supabase)
        plan_limit = usage_service.get_plan_limit(user_plan_data['plan'])
        days_remaining = (user_plan_data['period_end'] - datetime.now()).days

        user_data = UserInfo(
            id=user.id,
            email=user.email,
            plan=user_plan_data['plan'],
            analyses_used=user_plan_data['analyses_used'],
            plan_limit=plan_limit,
            days_remaining=days_remaining,
            ai_provider=user.user_metadata.get('ai_provider')
        )
        print(f"[get_current_user] returning: {user_data}")
        return user_data
    except Exception as e:
        print(f"Error fetching user or plan: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

async def require_usage_limit(
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    supabase: Annotated[Client, Depends(get_supabase_client)]
):
    usage_service = UsageService(supabase)
    if not usage_service.check_limit(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usage limit exceeded. Please upgrade your plan."
        )