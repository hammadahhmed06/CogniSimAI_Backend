# main.py (Updated to complete Sub-Project 1.3)
import logging
from uuid import UUID
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.core.config import settings # Import the centralized settings object
from app.services.feature_flags import load_feature_flags, feature_enabled # Import feature flag utilities
from fastapi.responses import Response

# --- 1. Initial Configuration & Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cognisim_ai")

limiter = Limiter(key_func=get_remote_address)

# --- Step 3 Change: Initialize FastAPI app using settings from config.py ---
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
    This API handles all backend services for CogniSim AI.
    It uses Supabase for authentication and provides role-based access control.
    
    **Key Features:**
    - Secure JWT authentication integrated with Supabase.
    - Role-Based Access Control (RBAC) for sensitive operations.
    - Rate limiting to prevent abuse.
    - Structured logging for security and debugging.
    - Centralized configuration and feature flags.
    """,
)

# Apply rate limiting middleware
app.state.limiter = limiter

def rate_limit_handler(request: Request, exc: Exception) -> Response:
    # Only handle RateLimitExceeded, otherwise re-raise
    if isinstance(exc, RateLimitExceeded):
        return _rate_limit_exceeded_handler(request, exc)
    raise exc

app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

# --- Step 3 Change: Add CORS middleware using settings from config.py ---
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# --- Supabase Client Initialization (using settings) ---
if not settings.SUPABASE_SERVICE_ROLE_KEY:
    logger.error("SUPABASE_SERVICE_ROLE_KEY is not configured properly in settings")
    raise ValueError("SUPABASE_SERVICE_ROLE_KEY must be provided for the application to work")

supabase: Client = create_client(
    str(settings.SUPABASE_URL),
    settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value()
)
logger.info("Supabase client initialized successfully.")


# --- Step 3 Change: Application Startup Event ---
@app.on_event("startup")
async def startup_event():
    """
    This function runs once when the application starts.
    It loads the feature flags from the database into the cache.
    """
    await load_feature_flags(supabase)
    logger.info("Application startup complete. Feature flags loaded.")


# --- 2. Pydantic Models & Dependencies (largely unchanged) ---
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr

class UserModel(BaseModel):
    id: UUID
    email: EmailStr

class ErrorResponse(BaseModel):
    detail: str

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
            role_query =  supabase.table("team_members").select("role").eq("user_id", user_id).eq("team_id", team_id).single().execute()
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


# --- 3. API Endpoints ---

@app.get("/", summary="Health Check", tags=["System"])
async def root():
    return {"status": "CogniSim AI Backend is running"}

@app.get("/api/profile", response_model=UserModel, summary="Get Current User's Profile", tags=["User"])
@limiter.limit("10/minute")
async def get_user_profile(request: Request, current_user: UserModel = Depends(get_current_user)):
    return current_user

@app.delete("/api/teams/{team_id}/members/{member_id}", summary="Remove a Team Member", tags=["Teams"], status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def delete_team_member(
    request: Request,
    team_id: UUID,
    member_id: UUID,
    user_role: str = require_role(required_roles=["lead"]) # Correct usage of the dependency
):
    logger.info(f"User with role '{user_role}' deleted member {member_id} from team {team_id}.")
    return

# --- Step 3 Change: New Endpoint Protected by a Feature Flag ---
@app.get(
    "/api/ai/epic-architect/suggest",
    summary="Get AI suggestions for an Epic (Feature Flagged)",
    tags=["AI Agents"],
    # This endpoint will only be accessible if the feature flag is enabled in the database.
    dependencies=[feature_enabled("epic_architect_agent_enabled")]
)
async def get_epic_suggestions(request: Request, current_user: UserModel = Depends(get_current_user)):
    """
    An endpoint for the Epic Architect AI agent.
    Its visibility is controlled by the 'epic_architect_agent_enabled' feature flag.
    If the flag is disabled, this endpoint will return a 404 Not Found error.
    """
    return {
        "message": f"Welcome, {current_user.email}! The Epic Architect Agent is enabled and ready.",
        "suggestions": [
            "Create user story for login flow.",
            "Create user story for password reset.",
            "Define acceptance criteria for user profile page."
        ]
    }

# --- Include Jira Integration Routes ---
from app.api.routes.integrations import router as integrations_router
app.include_router(integrations_router)
