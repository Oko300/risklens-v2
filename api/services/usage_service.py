from datetime import datetime, timedelta
from supabase import Client
import uuid


class UsageService:
    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.plans = {
            "free_trial": {"limit": 10, "duration_days": 1},   # resets daily
            "pro":        {"limit": 500, "duration_days": 30},
            "business":   {"limit": -1,  "duration_days": 30},
        }

    def get_plan_limit(self, plan_name: str) -> int:
        return self.plans.get(plan_name, {"limit": 0})["limit"]

    def _next_midnight(self) -> datetime:
        """Return tomorrow at 00:00:00 local server time."""
        tomorrow = datetime.now() + timedelta(days=1)
        return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)

    async def get_usage(self, user_id: uuid.UUID) -> dict:
        try:
            response = (
                self.supabase.from_("user_plans")
                .select("*")
                .eq("user_id", str(user_id))
                .single()
                .execute()
            )
            user_plan = response.data

            if not user_plan:
                return await self._create_default_plan(user_id)

            # Auto-reset if period has expired
            period_end_dt = datetime.fromisoformat(user_plan["period_end"])
            if datetime.now() > period_end_dt:
                await self._reset_plan(user_id, user_plan["plan"])
                response = (
                    self.supabase.from_("user_plans")
                    .select("*")
                    .eq("user_id", str(user_id))
                    .single()
                    .execute()
                )
                user_plan = response.data

            plan_limit = self.get_plan_limit(user_plan["plan"])
            period_end_dt = datetime.fromisoformat(user_plan["period_end"])
            days_remaining = (period_end_dt - datetime.now()).days

            return {
                "plan": user_plan["plan"],
                "analyses_used": user_plan["analyses_used"],
                "limit": plan_limit,
                "days_remaining": max(0, days_remaining),
            }
        except Exception as e:
            print(f"[usage] get_usage error for {user_id}: {e}")
            return {
                "plan": "free_trial",
                "analyses_used": 0,
                "limit": self.get_plan_limit("free_trial"),
                "days_remaining": 1,
            }

    async def increment_usage(self, user_id: uuid.UUID) -> bool:
        """
        Only call this when a tool ran successfully and returned real filing data.
        Returns True if incremented, False if limit already reached.
        """
        try:
            response = (
                self.supabase.from_("user_plans")
                .select("*")
                .eq("user_id", str(user_id))
                .single()
                .execute()
            )
            user_plan = response.data

            if not user_plan:
                user_plan = await self._create_default_plan(user_id)

            # Auto-reset expired period before incrementing
            period_end_dt = datetime.fromisoformat(user_plan["period_end"])
            if datetime.now() > period_end_dt:
                await self._reset_plan(user_id, user_plan["plan"])
                response = (
                    self.supabase.from_("user_plans")
                    .select("*")
                    .eq("user_id", str(user_id))
                    .single()
                    .execute()
                )
                user_plan = response.data

            plan_limit = self.get_plan_limit(user_plan["plan"])

            # Unlimited plan
            if plan_limit == -1:
                return True

            if user_plan["analyses_used"] >= plan_limit:
                print(f"[usage] user {user_id} already at limit ({user_plan['analyses_used']}/{plan_limit})")
                return False

            new_count = user_plan["analyses_used"] + 1
            self.supabase.from_("user_plans").update(
                {"analyses_used": new_count}
            ).eq("user_id", str(user_id)).execute()
            print(f"[usage] incremented user {user_id} to {new_count}/{plan_limit}")
            return True

        except Exception as e:
            print(f"[usage] increment_usage error for {user_id}: {e}")
            return False

    async def check_limit(self, user_id: uuid.UUID, user_plan: dict = None) -> bool:
        try:
            if user_plan is None:
                response = (
                    self.supabase.from_("user_plans")
                    .select("*")
                    .eq("user_id", str(user_id))
                    .single()
                    .execute()
                )
                user_plan = response.data

            if not user_plan:
                user_plan = await self._create_default_plan(user_id)

            # Auto-reset expired period before checking
            period_end_dt = datetime.fromisoformat(user_plan["period_end"])
            if datetime.now() > period_end_dt:
                await self._reset_plan(user_id, user_plan["plan"])
                response = (
                    self.supabase.from_("user_plans")
                    .select("*")
                    .eq("user_id", str(user_id))
                    .single()
                    .execute()
                )
                user_plan = response.data

            plan_limit = self.get_plan_limit(user_plan["plan"])

            if plan_limit == -1:
                return True

            return user_plan["analyses_used"] < plan_limit

        except Exception as e:
            print(f"[usage] check_limit error for {user_id}: {e}")
            return False

    async def _create_default_plan(self, user_id: uuid.UUID) -> dict:
        try:
            period_start = datetime.now()
            period_end = self._next_midnight()  # free trial resets at midnight
            new_plan = {
                "user_id": str(user_id),
                "plan": "free_trial",
                "analyses_used": 0,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            }
            response = self.supabase.from_("user_plans").insert(new_plan).execute()
            if response.data:
                return response.data[0]
            raise Exception("Insert returned no data")
        except Exception as e:
            print(f"[usage] _create_default_plan error for {user_id}: {e}")
            raise

    async def _reset_plan(self, user_id: uuid.UUID, plan_name: str):
        try:
            period_start = datetime.now()
            if plan_name == "free_trial":
                period_end = self._next_midnight()
            else:
                duration = self.plans.get(plan_name, {}).get("duration_days", 30)
                period_end = period_start + timedelta(days=duration)

            self.supabase.from_("user_plans").update({
                "analyses_used": 0,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            }).eq("user_id", str(user_id)).execute()
            print(f"[usage] reset plan for user {user_id} ({plan_name}) until {period_end}")
        except Exception as e:
            print(f"[usage] _reset_plan error for {user_id}: {e}")

    async def update_user_plan(self, user_id: uuid.UUID, new_plan_name: str, status: str = "active"):
        try:
            plan_details = self.plans.get(new_plan_name)
            if not plan_details:
                print(f"[usage] unknown plan '{new_plan_name}' for user {user_id}")
                return

            period_start = datetime.now()
            if new_plan_name == "free_trial":
                period_end = self._next_midnight()
            else:
                period_end = period_start + timedelta(days=plan_details["duration_days"])

            response = (
                self.supabase.from_("user_plans")
                .select("*")
                .eq("user_id", str(user_id))
                .single()
                .execute()
            )
            if response.data:
                self.supabase.from_("user_plans").update({
                    "plan": new_plan_name,
                    "analyses_used": 0,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                }).eq("user_id", str(user_id)).execute()
            else:
                self.supabase.from_("user_plans").insert({
                    "user_id": str(user_id),
                    "plan": new_plan_name,
                    "analyses_used": 0,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                }).execute()
            print(f"[usage] user {user_id} plan updated to {new_plan_name}")
        except Exception as e:
            print(f"[usage] update_user_plan error for {user_id}: {e}")
            raise