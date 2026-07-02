"""api/schemas/auth.py — Request and response models for auth routes."""

from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email:     EmailStr
    password:  str
    full_name: str
    timezone:  str = "UTC"

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("full_name cannot be empty")
        return v.strip()


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


class UpdateProfileRequest(BaseModel):
    full_name:   Optional[str] = None
    timezone:    Optional[str] = None
    ai_provider: Optional[str] = None    # 'claude' | 'grok' | 'gemini'
    ai_api_key:  Optional[str] = None    # plaintext — encrypted server-side
    ai_model:    Optional[str] = None    # e.g. 'claude-sonnet-4-6'

    @field_validator("ai_provider")
    @classmethod
    def valid_provider(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("claude", "grok", "gemini"):
            raise ValueError("ai_provider must be 'claude', 'grok', or 'gemini'")
        return v


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    id:              str
    email:           str
    full_name:       str
    ai_provider:     Optional[str]
    ai_model:        Optional[str]
    has_ai_key:      bool           # True if ai_api_key_enc is set — never expose the key itself
    timezone:        str
    created_at:      str
    last_active_at:  str


class AuthTokenOut(BaseModel):
    access_token:   str
    refresh_token:  str
    token_type:     str = "bearer"
    expires_in:     int
    user:           UserOut


class MessageOut(BaseModel):
    message: str
