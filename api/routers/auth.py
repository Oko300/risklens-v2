from fastapi import APIRouter, HTTPException, Request, Depends
from api.core.database import get_supabase_client, get_supabase_auth_client
from api.core.dependencies import get_current_user

router = APIRouter()

@router.post("/login")
async def login(request: Request):
    try:
        body = await request.json()
        email = body.get("email", "")
        password = body.get("password", "")
        if not email or not password:
            raise HTTPException(status_code=400, detail="Email and password required")
        supabase = get_supabase_auth_client()
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if not response.session:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        return {
            "access_token": response.session.access_token,
            "token_type": "bearer",
            "user": {"id": response.user.id, "email": response.user.email}
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[login] Error: {e}")
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/register")
async def register(request: Request):
    try:
        body = await request.json()
        email = body.get("email", "")
        password = body.get("password", "")
        full_name = body.get("full_name", "")
        if not email or not password:
            raise HTTPException(status_code=400, detail="Email and password required")
        supabase = get_supabase_auth_client()
        response = supabase.auth.sign_up({
            "email": email,
            "password": password,
            "options": {"data": {"full_name": full_name}}
        })
        if not response.user:
            raise HTTPException(status_code=400, detail="Registration failed.")
        return {"message": "Account created. Please check your email to verify your account and then sign in."}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[register] Error: {e}")
        if "User already registered" in str(e):
            raise HTTPException(status_code=400, detail="User with this email already exists.")
        raise HTTPException(status_code=400, detail=f"Registration failed: {e}")

@router.get("/me")
async def read_users_me(current_user: dict = Depends(get_current_user)):
    return current_user

@router.get("/test")
async def test():
    return {"status": "auth router working"}

@router.post("/connect-ai")
async def connect_ai(request: Request, current_user: dict = Depends(get_current_user)):
    print(f"[connect-ai] Called by user: {current_user}")
    try:
        body = await request.json()
        provider = body.get("provider", "").strip()
        api_key = body.get("api_key", "").strip()

        if not provider or not api_key:
            raise HTTPException(status_code=400, detail="Provider and API key are required")

        # Validate key format per provider
        if provider == "grok" and not api_key.startswith("xai-"):
            raise HTTPException(status_code=400, detail="Grok keys must start with xai-")
        if provider == "gemini" and not api_key.startswith("AIza"):
            raise HTTPException(status_code=400, detail="Gemini keys must start with AIza")
        if provider == "claude" and not api_key.startswith("sk-ant-"):
            raise HTTPException(status_code=400, detail="Claude keys must start with sk-ant-")

        user_id = str(current_user.get("user_id") or current_user.get("sub"))
        supabase = get_supabase_client()

        # Delete-then-insert ensures old keys are always replaced
        supabase.table("user_ai_keys").delete().eq("user_id", user_id).execute()
        print(f"[connect-ai] Deleted old keys for user {user_id}")

        insert_result = supabase.table("user_ai_keys").insert({
            "user_id": user_id,
            "provider": provider,
            "api_key": api_key
        }).execute()
        print(f"[connect-ai] Inserted new {provider} key: {insert_result}")

        return {"success": True, "provider": provider, "message": f"{provider.capitalize()} API key saved successfully"}

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[connect-ai] ERROR: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))