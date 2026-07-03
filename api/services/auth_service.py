import os
import os
from datetime import datetime, timedelta
from typing import Optional
from supabase import Client
from passlib.context import CryptContext
import jwt

from api.models.schemas import UserRegister, UserLogin, ConnectAI
from api.core.database import get_supabase_client
from api.services.usage_service import UsageService

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "super-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30 # Changed from minutes to days

class AuthService:
    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.usage_service = UsageService(supabase)

    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None):
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS) # Changed to days
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
                # Create a default free trial plan for the new user
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
                access_token_expires = timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS) # Changed to days
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
            # Supabase user metadata is a JSONB column, so we can store provider and encrypted key
            # For simplicity, we're not encrypting the key here, but in a real app, you should.
            # You would use a proper encryption service for the API key.
            response = self.supabase.auth.update_user(
                user_id,
                user_metadata={"ai_provider": ai_data.provider, "ai_api_key": ai_data.api_key}
            )
            if response.user:
                return {"message": "AI provider connected successfully."}
            raise Exception("Failed to connect AI provider.")
        except Exception as e:
            print(f"Error connecting AI provider for user {user_id}: {e}")
            raise
