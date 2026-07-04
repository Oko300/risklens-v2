from typing import List, Optional
from uuid import UUID
from datetime import datetime

from api.models.schemas import Notification
from api.core.database import supabase_client

class NotificationService:
    def __init__(self):
        self.db = supabase_client

    async def create_notification(self, user_id: UUID, message: str) -> Notification:
        data, count = await self.db.from_("notifications").insert({
            "user_id": str(user_id),
            "message": message,
            "read_status": False,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        
        if count and count > 0:
            return Notification(**data[1][0])
        raise Exception("Failed to create notification")

    async def get_notifications(self, user_id: UUID, unread_only: bool = False) -> List[Notification]:
        query = self.db.from_("notifications").select("*").eq("user_id", str(user_id))
        if unread_only:
            query = query.eq("read_status", False)
        
        data, count = await query.order("created_at", desc=True).execute()
        
        if count is not None:
            return [Notification(**item) for item in data[1]]
        return []

    async def mark_as_read(self, notification_id: UUID, user_id: UUID) -> Optional[Notification]:
        data, count = await self.db.from_("notifications").update({
            "read_status": True
        }).eq("id", str(notification_id)).eq("user_id", str(user_id)).execute()
        
        if count and count > 0:
            return Notification(**data[1][0])
        return None

    async def mark_all_as_read(self, user_id: UUID) -> List[Notification]:
        data, count = await self.db.from_("notifications").update({
            "read_status": True
        }).eq("user_id", str(user_id)).eq("read_status", False).execute()
        
        if count is not None:
            return [Notification(**item) for item in data[1]]
        return []