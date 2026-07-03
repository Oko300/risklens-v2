import os
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials
    try:
        jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        if not jwt_secret:
            raise HTTPException(status_code=500, 
              detail="JWT secret not configured")
        
        payload = jwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False}
        )
        
        user_id = payload.get("sub")
        email = payload.get("email", "")
        
        if not user_id:
            raise HTTPException(status_code=401, 
              detail="Invalid token")
            
        return {"user_id": user_id, "email": email}
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, 
          detail="Token expired. Please sign in again.")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, 
          detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[auth] JWT error: {e}")
        raise HTTPException(status_code=401, 
          detail="Could not validate credentials")