from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class CreateConversationRequest(BaseModel):
    title: Optional[str] = None
    ticker: Optional[str] = None
    analysis_id: Optional[str] = None

class ConversationOut(BaseModel):
    id: str
    user_id: str
    analysis_id: Optional[str] = None
    title: Optional[str] = None
    ticker: Optional[str] = None
    message_count: int
    last_message_at: datetime
    created_at: datetime

class ConversationListResponse(BaseModel):
    conversations: list[ConversationOut]
    total: int

class SendMessageRequest(BaseModel):
    content: str

class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    triggered_analysis_id: Optional[str] = None
    created_at: datetime

class SendMessageResponse(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut
    analysis_ran: bool
    analysis_id: Optional[str] = None