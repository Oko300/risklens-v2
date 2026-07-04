import os
from datetime import datetime, timedelta
from typing import Optional
from supabase import Client
from passlib.context import CryptContext
import jwt # Added import for jwt

from api.models.schemas import UserRegister, UserLogin, ConnectAI
from api.core.database import get_supabase_client
from api.services.usage_service import UsageService

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "super-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

class AuthService:
    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.usage_service = UsageService(supabase)

    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None):
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt

    async def register_user(self, user_data: UserRegister) -> dict:
        try:
            response = self.supabase.auth.sign_up({
                "email": user_data.email,
                "password": user_data.password
            })
            user = response.user
            if user:
                await self.usage_service._create_default_plan(user.id)
                return {"message": "User registered successfully. Please check your email to verify your account."}
            raise Exception("User registration failed.")
        except Exception as e:
            print(f"Error during user registration: {e}")
            raise

    async def login_user(self, user_data: UserLogin) -> str:
        try:
            response = self.supabase.auth.sign_in_with_password({
                "email": user_data.email,
                "password": user_data.password
            })
            user = response.user
            if user:
                access_token_expires = timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
                access_token = self.create_access_token(
                    data={"sub": str(user.id)}, expires_delta=access_token_expires
                )
                return access_token
            raise Exception("Invalid credentials.")
        except Exception as e:
            print(f"Error during user login: {e}")
            raise

    async def connect_ai_provider(self, user_id: str, ai_data: ConnectAI) -> dict:
        try:
            # Check if an entry already exists for the user in user_ai_keys
            existing_key, count = await self.supabase.from_("user_ai_keys").select(
                "id"
            ).eq("user_id", user_id).execute()

            if existing_key.data:
                # Update existing entry
                await self.supabase.from_("user_ai_keys").update({
                    "provider": ai_data.provider,
                    "api_key": ai_data.api_key
                }).eq("user_id", user_id).execute()
            else:
                # Insert new entry
                await self.supabase.from_("user_ai_keys").insert({
                    "user_id": user_id,
                    "provider": ai_data.provider,
                    "api_key": ai_data.api_key
                }).execute()
            
            return {"message": "AI provider connected successfully."}
        except Exception as e:
            print(f"Error connecting AI provider for user {user_id}: {e}")
            raise
