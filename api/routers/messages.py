from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client
import uuid

from api.core.database import get_supabase_client
from api.core.dependencies import get_current_user, require_usage_limit
from api.models.schemas import MessageCreate, Message, UserInfo
from api.services.message_service import MessageService

router = APIRouter()

@router.post("/", response_model=Message, dependencies=[Depends(require_usage_limit)])
async def send_new_message(
    message_data: MessageCreate,
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    supabase: Annotated[Client, Depends(get_supabase_client)]
):
    try:
        message_service = MessageService(supabase)
        ai_provider = current_user.ai_provider
        ai_api_key = current_user.user_metadata.get('ai_api_key') # Assuming this is stored in user_metadata
        return await message_service.send_message(current_user.id, message_data, ai_provider, ai_api_key)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("/{conversation_id}", response_model=List[Message])
async def get_conversation_messages(
    conversation_id: uuid.UUID,
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    supabase: Annotated[Client, Depends(get_supabase_client)]
):
    try:
        message_service = MessageService(supabase)
        return await message_service.get_messages(current_user.id, conversation_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))