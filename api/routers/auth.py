from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status, Request
from supabase import Client

from api.core.database import get_supabase_client
from api.core.dependencies import get_current_user
from api.models.schemas import UserRegister, UserLogin, Token, UserInfo, ConnectAI
from api.services.auth_service import AuthService

router = APIRouter()

@router.post("/register", response_model=dict)
async def register(
    user_data: UserRegister,
    supabase: Annotated[Client, Depends(get_supabase_client)]
):
    try:
        auth_service = AuthService(supabase)
        return await auth_service.register_user(user_data)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/login", response_model=Token)
async def login(
    user_data: UserLogin,
    supabase: Annotated[Client, Depends(get_supabase_client)]
):
    try:
        auth_service = AuthService(supabase)
        access_token = await auth_service.login_user(user_data)
        return {"access_token": access_token, "token_type": "bearer"}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

@router.get("/me", response_model=UserInfo)
async def read_users_me(
    current_user: Annotated[UserInfo, Depends(get_current_user)]
):
    return current_user

@router.post("/connect-ai")
async def connect_ai(
    request: Request,
    current_user = Depends(get_current_user)
):
    try:
        body = await request.json()
        provider = body.get("provider", "")
        api_key = body.get("api_key", "")
        
        if not provider or not api_key:
            raise HTTPException(status_code=400, 
              detail="Provider and api_key are required")
        
        # Save to Supabase user metadata
        from api.core.database import get_supabase_client
        supabase = get_supabase_client()
        
        # Store in a simple user_ai_keys table
        # First try to upsert
        result = supabase.table("user_ai_keys").upsert({
            "user_id": current_user["user_id"],
            "provider": provider,
            "api_key": api_key,
            "updated_at": "now()"
        }, on_conflict="user_id").execute()
        
        return {"success": True, "provider": provider}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Connect AI error: {e}")
        raise HTTPException(status_code=500, 
          detail=f"Failed to save AI key: {str(e)}")
