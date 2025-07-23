# app/core/dependencies.py
# Shared dependencies to avoid circular imports

import logging
from uuid import UUID
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

# Initialize logger
logger = logging.getLogger("cognisim_ai")

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

# Initialize Supabase client
if not settings.SUPABASE_SERVICE_ROLE_KEY:
    logger.error("SUPABASE_SERVICE_ROLE_KEY is not configured properly in settings")
    raise ValueError("SUPABASE_SERVICE_ROLE_KEY must be provided for the application to work")

supabase: Client = create_client(
    str(settings.SUPABASE_URL),
    settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value()
)
logger.info("Supabase client initialized successfully.")

# Pydantic Models
class UserModel(BaseModel):
    id: UUID
    email: EmailStr

class ErrorResponse(BaseModel):
    detail: str

# Authentication dependencies
bearer_scheme = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> UserModel:
    token = credentials.credentials
    try:
        user_response = supabase.auth.get_user(token)
        user = getattr(user_response, "user", None)
        if not user or not getattr(user, "id", None) or not getattr(user, "email", None):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token or user data.")
        
        logger.info(f"User successfully authenticated: {user.email} (ID: {user.id})")
        return UserModel(id=UUID(user.id), email=user.email)
    except Exception as e:
        logger.error(f"Token validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

def require_role(required_roles: list[str]):
    async def role_checker(team_id: UUID, current_user: UserModel = Depends(get_current_user)):
        user_id = current_user.id
        try:
            role_query = supabase.table("team_members").select("role").eq("user_id", user_id).eq("team_id", team_id).single().execute()
            user_role = role_query.data.get("role") if role_query.data else None
            if user_role not in required_roles:
                logger.warning(f"Authorization Failed: User {user_id} with role '{user_role}' attempted action requiring one of {required_roles} on team {team_id}.")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions.")
            
            logger.info(f"Authorization Success: User {user_id} granted access with role '{user_role}'.")
            return user_role
        except Exception as e:
            logger.error(f"RBAC check failed for user {user_id} on team {team_id}: {e}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    # Return the dependency correctly
    return Depends(role_checker)
