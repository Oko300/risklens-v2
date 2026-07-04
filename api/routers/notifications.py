from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from uuid import UUID

from api.models.schemas import Notification, UserInfo
from api.services.notification_service import NotificationService
from api.core.dependencies import get_current_active_user

router = APIRouter()

@router.get("/notifications", response_model=List[Notification])
async def get_user_notifications(
    unread_only: bool = False,
    current_user: UserInfo = Depends(get_current_active_user),
    notification_service: NotificationService = Depends()
):
    """
    Retrieve a list of notifications for the current user.
    """
    return await notification_service.get_notifications(current_user.id, unread_only)

@router.post("/notifications/{notification_id}/read", response_model=Notification)
async def mark_notification_as_read(
    notification_id: UUID,
    current_user: UserInfo = Depends(get_current_active_user),
    notification_service: NotificationService = Depends()
):
    """
    Mark a specific notification as read.
    """
    notification = await notification_service.mark_as_read(notification_id, current_user.id)
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or not authorized"
        )
    return notification

@router.post("/notifications/mark_all_read", response_model=List[Notification])
async def mark_all_notifications_as_read(
    current_user: UserInfo = Depends(get_current_active_user),
    notification_service: NotificationService = Depends()
):
    """
    Mark all unread notifications for the current user as read.
    """
    return await notification_service.mark_all_as_read(current_user.id)