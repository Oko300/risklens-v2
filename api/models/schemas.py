from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime
import uuid

class UserRegister(BaseModel):
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class UserInfo(BaseModel):
    id: uuid.UUID
    email: EmailStr
    plan: str
    analyses_used: int
    plan_limit: int
    days_remaining: int
    ai_provider: Optional[str] = None

class ConnectAI(BaseModel):
    provider: str
    api_key: str

class ConversationCreate(BaseModel):
    title: str

class Conversation(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    created_at: datetime

    class Config:
        from_attributes = True

class MessageCreate(BaseModel):
    conversation_id: uuid.UUID
    content: str

class Message(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    content: str
    tool_used: Optional[str] = None
    ticker: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class UsageInfo(BaseModel):
    plan: str
    analyses_used: int
    limit: int
    days_remaining: int