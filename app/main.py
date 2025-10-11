# main.py (Updated to complete Sub-Project 1.3)
import logging
from uuid import UUID
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.core.config import settings # Import the centralized settings object
from app.services.feature_flags import load_feature_flags, feature_enabled # Import feature flag utilities
from fastapi.responses import Response
from app.api.routes.integrations import router as integrations_router
from app.api.routes.projects import router as projects_router
from app.api.routes.issues import router as issues_router
from app.api.routes.agents import router as agents_router
from app.api.routes.teams import router as teams_router
from app.api.routes.workspaces import router as workspaces_router
from app.api.routes.subscribe import router as subscribe_router
from app.api.routes.auth_invite import router as auth_invite_router
from app.api.routes.members import router as members_router
from app.api.routes.account import router as account_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.slack_integration import router as slack_router
from app.core.dependencies import get_current_user, UserModel, supabase, limiter, ErrorResponse, require_role
# --- 1. Initial Configuration & Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cognisim_ai")

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

# --- Supabase Client and Rate Limiter from dependencies ---
# These are now imported from app.core.dependencies to avoid circular imports

# --- Step 3 Change: Application Startup Event ---
@app.on_event("startup")
async def startup_event():
    """
    This function runs once when the application starts.
    It loads the feature flags from the database into the cache.
    """
    load_feature_flags(supabase)
    logger.info("Application startup complete. Feature flags loaded.")

# --- 2. Dependencies imported from app.core.dependencies to avoid circular imports ---


# --- 3. API Endpoints ---

@app.get("/", summary="Root Endpoint", tags=["System"])
async def root():
    return {"status": "CogniSim AI Backend is running"}

@app.get("/health", summary="Health Check for Railway", tags=["System"])
async def health_check():
    """
    Health check endpoint for Railway and monitoring services.
    Returns 200 OK if the service is healthy.
    """
    try:
        # Optional: Add database connectivity check
        # supabase.table("users").select("id").limit(1).execute()
        return {
            "status": "healthy",
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unhealthy"
        )

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

app.include_router(integrations_router)
app.include_router(projects_router)
app.include_router(workspaces_router)
app.include_router(issues_router)
app.include_router(agents_router)
app.include_router(teams_router)
app.include_router(members_router)
app.include_router(subscribe_router)
app.include_router(auth_invite_router)
app.include_router(account_router)
app.include_router(dashboard_router)
app.include_router(slack_router)
