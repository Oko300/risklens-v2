from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
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

@router.post("/connect-ai", response_model=dict)
async def connect_ai(
    ai_data: ConnectAI,
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    supabase: Annotated[Client, Depends(get_supabase_client)]
):
    try:
        auth_service = AuthService(supabase)
        return await auth_service.connect_ai_provider(str(current_user.id), ai_data)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))