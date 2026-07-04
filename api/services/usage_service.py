from datetime import datetime, timedelta
from supabase import Client
import uuid

class UsageService:
    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.plans = {
            "free_trial": {"limit": 10, "duration_days": 30},
            "pro": {"limit": 500, "duration_days": 30},
            "business": {"limit": -1, "duration_days": 30}, # -1 for unlimited
        }

    def get_plan_limit(self, plan_name: str) -> int:
        return self.plans.get(plan_name, {"limit": 0})["limit"]

    async def get_usage(self, user_id: uuid.UUID) -> dict:
        try:
            response = self.supabase.from_('user_plans').select('*').eq('user_id', str(user_id)).single().execute()
            user_plan = response.data

            if not user_plan:
                # Create a default free trial plan if not found
                return await self._create_default_plan(user_id)

            # Check if the plan needs to be reset (e.g., monthly for pro/business)
            if user_plan['plan'] in ['pro', 'business'] and datetime.now() > datetime.fromisoformat(user_plan['period_end']):
                await self._reset_plan(user_id, user_plan['plan'])
                response = self.supabase.from_('user_plans').select('*').eq('user_id', str(user_id)).single().execute()
                user_plan = response.data

            plan_limit = self.get_plan_limit(user_plan['plan'])
            period_end_dt = datetime.fromisoformat(user_plan['period_end'])
            days_remaining = (period_end_dt - datetime.now()).days

            return {
                "plan": user_plan['plan'],
                "analyses_used": user_plan['analyses_used'],
                "limit": plan_limit,
                "days_remaining": max(0, days_remaining)
            }
        except Exception as e:
            print(f"Error getting usage for user {user_id}: {e}")
            # Fallback to a default free trial if there's an error
            return {
                "plan": "free_trial",
                "analyses_used": 0,
                "limit": self.get_plan_limit("free_trial"),
                "days_remaining": 30 # Default for new users
            }

    async def increment_usage(self, user_id: uuid.UUID) -> bool:
        try:
            response = self.supabase.from_('user_plans').select('*').eq('user_id', str(user_id)).single().execute()
            user_plan = response.data

            if not user_plan:
                user_plan = (await self._create_default_plan(user_id)) # Create and get the new plan

            if not self.check_limit(user_id, user_plan):
                return False

            updated_analyses_used = user_plan['analyses_used'] + 1
            self.supabase.from_('user_plans').update({'analyses_used': updated_analyses_used}).eq('user_id', str(user_id)).execute()
            return True
        except Exception as e:
            print(f"Error incrementing usage for user {user_id}: {e}")
            return False

    async def check_limit(self, user_id: uuid.UUID, user_plan: dict = None) -> bool:
        try:
            if user_plan is None:
                response = self.supabase.from_('user_plans').select('*').eq('user_id', str(user_id)).single().execute()
                user_plan = response.data

            if not user_plan:
                user_plan = (await self._create_default_plan(user_id)) # Create and get the new plan

            plan_limit = self.get_plan_limit(user_plan['plan'])

            if plan_limit == -1: # Unlimited plan
                return True

            return user_plan['analyses_used'] < plan_limit
        except Exception as e:
            print(f"Error checking limit for user {user_id}: {e}")
            return False # Deny access on error

    async def _create_default_plan(self, user_id: uuid.UUID) -> dict:
        try:
            plan_name = "free_trial"
            plan_details = self.plans[plan_name]
            period_end = datetime.now() + timedelta(days=plan_details['duration_days'])
            new_plan = {
                "user_id": str(user_id),
                "plan": plan_name,
                "analyses_used": 0,
                "period_start": datetime.now().isoformat(),
                "period_end": period_end.isoformat()
            }
            response = self.supabase.from_('user_plans').insert(new_plan).execute()
            if response.data:
                return response.data[0]
            raise Exception("Failed to create default plan")
        except Exception as e:
            print(f"Error creating default plan for user {user_id}: {e}")
            raise

    async def _reset_plan(self, user_id: uuid.UUID, plan_name: str):
        try:
            plan_details = self.plans[plan_name]
            new_period_start = datetime.now()
            new_period_end = new_period_start + timedelta(days=plan_details['duration_days'])
            self.supabase.from_('user_plans').update({
                'analyses_used': 0,
                'period_start': new_period_start.isoformat(),
                'period_end': new_period_end.isoformat()
            }).eq('user_id', str(user_id)).execute()
        except Exception as e:
            print(f"Error resetting plan for user {user_id}: {e}")

    async def update_user_plan(self, user_id: uuid.UUID, new_plan_name: str, status: str = "active"):
        try:
            plan_details = self.plans.get(new_plan_name)
            if not plan_details:
                print(f"Warning: Attempted to set unknown plan '{new_plan_name}' for user {user_id}")
                return

            period_start = datetime.now()
            period_end = period_start + timedelta(days=plan_details['duration_days'])

            # Check if the user already has a plan entry
            response = self.supabase.from_('user_plans').select('*').eq('user_id', str(user_id)).single().execute()
            if response.data:
                # Update existing plan
                self.supabase.from_('user_plans').update({
                    'plan': new_plan_name,
                    'analyses_used': 0, # Reset usage on plan change
                    'period_start': period_start.isoformat(),
                    'period_end': period_end.isoformat(),
                    'status': status # Add status to user_plans table
                }).eq('user_id', str(user_id)).execute()
            else:
                # Create new plan entry if none exists
                new_plan = {
                    "user_id": str(user_id),
                    "plan": new_plan_name,
                    "analyses_used": 0,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "status": status
                }
                self.supabase.from_('user_plans').insert(new_plan).execute()
            print(f"User {user_id} plan updated to {new_plan_name} with status {status}")
        except Exception as e:
            print(f"Error updating user plan for user {user_id} to {new_plan_name}: {e}")
            raise
