from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client
import uuid

from api.core.database import get_supabase_client
from api.core.dependencies import get_current_user
from api.models.schemas import ConversationCreate, Conversation, UserInfo
from api.services.conversation_service import ConversationService

router = APIRouter()

@router.post("/", response_model=Conversation)
async def create_new_conversation(
    conversation_data: ConversationCreate,
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    supabase: Annotated[Client, Depends(get_supabase_client)]
):
    try:
        conversation_service = ConversationService(supabase)
        return await conversation_service.create_conversation(current_user.id, conversation_data)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("/", response_model=List[Conversation])
async def list_conversations(
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    supabase: Annotated[Client, Depends(get_supabase_client)]
):
    try:
        conversation_service = ConversationService(supabase)
        return await conversation_service.get_conversations(current_user.id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.delete("/{conversation_id}", response_model=dict)
async def delete_single_conversation(
    conversation_id: uuid.UUID,
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    supabase: Annotated[Client, Depends(get_supabase_client)]
):
    try:
        conversation_service = ConversationService(supabase)
        return await conversation_service.delete_conversation(current_user.id, conversation_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))