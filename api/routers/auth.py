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

@router.get("/test")
async def test():
    return {"status": "auth router working"}

@router.post("/connect-ai")
async def connect_ai(request: Request, current_user = Depends(get_current_user)):
    print(f"[connect-ai] Called by user: {current_user}")
    try:
        body = await request.json()
        print(f"[connect-ai] Body received: {body}")
        provider = body.get("provider", "")
        api_key = body.get("api_key", "")
        print(f"[connect-ai] Provider: {provider}")
        
        from api.core.database import get_supabase_client
        supabase = get_supabase_client()
        print(f"[connect-ai] Supabase client obtained")
        
        result = supabase.table("user_ai_keys").upsert({
            "user_id": str(current_user.get("user_id") or current_user.get("sub")),
            "provider": provider,
            "api_key": api_key
        }, on_conflict="user_id").execute()
        print(f"[connect-ai] Upsert result: {result}")
        
        return {"success": True, "provider": provider}
        
    except Exception as e:
        import traceback
        print(f"[connect-ai] ERROR: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))
