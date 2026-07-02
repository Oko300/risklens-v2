from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from api.core.database import get_supabase_client
from api.core.dependencies import get_current_user
from api.models.schemas import UsageInfo, UserInfo
from api.services.usage_service import UsageService

router = APIRouter()

@router.get("/me", response_model=UsageInfo)
async def get_my_usage(
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    supabase: Annotated[Client, Depends(get_supabase_client)]
):
    try:
        usage_service = UsageService(supabase)
        usage_data = await usage_service.get_usage(current_user.id)
        return UsageInfo(**usage_data)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))