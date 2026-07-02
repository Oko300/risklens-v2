from typing import List
from supabase import Client
import uuid
from api.models.schemas import ConversationCreate, Conversation

class ConversationService:
    def __init__(self, supabase: Client):
        self.supabase = supabase

    async def create_conversation(self, user_id: uuid.UUID, conversation_data: ConversationCreate) -> Conversation:
        try:
            response = self.supabase.from_('conversations').insert({
                "user_id": str(user_id),
                "title": conversation_data.title
            }).execute()
            if response.data:
                return Conversation(**response.data[0])
            raise Exception("Failed to create conversation.")
        except Exception as e:
            print(f"Error creating conversation for user {user_id}: {e}")
            raise

    async def get_conversations(self, user_id: uuid.UUID) -> List[Conversation]:
        try:
            response = self.supabase.from_('conversations').select('*').eq('user_id', str(user_id)).order('created_at', desc=True).execute()
            if response.data:
                return [Conversation(**conv) for conv in response.data]
            return []
        except Exception as e:
            print(f"Error fetching conversations for user {user_id}: {e}")
            raise

    async def delete_conversation(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> dict:
        try:
            response = self.supabase.from_('conversations').delete().eq('id', str(conversation_id)).eq('user_id', str(user_id)).execute()
            if response.data:
                return {"message": "Conversation deleted successfully."}
            raise Exception("Conversation not found or user not authorized.")
        except Exception as e:
            print(f"Error deleting conversation {conversation_id} for user {user_id}: {e}")
            raise