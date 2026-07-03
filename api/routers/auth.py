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
            raise HTTPException(status_code=400, 
              detail="Email and password required")
        
        supabase = get_supabase_auth_client()
        response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        
        if not response.session:
            raise HTTPException(status_code=401,
              detail="Invalid email or password")
        
        return {
            "access_token": response.session.access_token,
            "token_type": "bearer",
            "user": {
                "id": response.user.id,
                "email": response.user.email
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[login] Error: {e}")
        raise HTTPException(status_code=401,
          detail=str(e))

@router.post("/register")
async def register(request: Request):
    try:
        body = await request.json()
        email = body.get("email", "")
        password = body.get("password", "")
        full_name = body.get("full_name", "")
        
        if not email or not password:
            raise HTTPException(status_code=400,
              detail="Email and password required")
        
        supabase = get_supabase_auth_client()
        response = supabase.auth.sign_up({
            "email": email,
            "password": password,
            "options": {
                "data": {"full_name": full_name}
            }
        })
        
        if not response.user:
            raise HTTPException(status_code=400,
              detail="Registration failed")
        
        # Auto sign in after register
        login_response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        
        if login_response.session:
            return {
                "access_token": login_response.session.access_token,
                "token_type": "bearer",
                "user": {
                    "id": login_response.user.id,
                    "email": login_response.user.email
                }
            }
        
        return {"message": "Account created. Please sign in."}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[register] Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

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
        print(f"[connect-ai] Body received: {body}")
        provider = body.get("provider", "")
        api_key = body.get("api_key", "")
        print(f"[connect-ai] Provider: {provider}")
        
        supabase = get_supabase_client()
        print(f"[connect-ai] Supabase client obtained")
        
        result = supabase.table("user_ai_keys").upsert({
            "user_id": str(current_user.get("user_id") or current_user.get("sub")),
            "provider": provider,
            "api_key": api_key
        }, on_conflict="user_id").execute()
        print(f"[connect-ai] Upsert result: {result}")
        
        return {"success": True, "provider": provider}
        
    except Exception as e:
        import traceback
        print(f"[connect-ai] ERROR: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))
