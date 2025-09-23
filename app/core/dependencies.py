# app/core/dependencies.py
# Shared dependencies to avoid circular imports

import logging
from uuid import UUID
from fastapi import Depends, HTTPException, status, Header
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

# Lazy Supabase client to avoid import-time config errors
class _SupabaseLazy:
    _client: Client | None = None

    def _init(self) -> Client:
        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
            logger.error("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not configured in settings")
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        client = create_client(
            str(settings.SUPABASE_URL),
            settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value()
        )
        self._client = client
        logger.info("Supabase client initialized successfully.")
        return client

    def __getattr__(self, name: str):
        client = self._client or self._init()
        return getattr(client, name)


supabase: Client = _SupabaseLazy()  # type: ignore[assignment]

# Pydantic Models
class UserModel(BaseModel):
    id: UUID
    email: EmailStr

class TeamContext(BaseModel):
    team_id: UUID
    role: str

class ErrorResponse(BaseModel):
    detail: str

# Authentication dependencies
bearer_scheme = HTTPBearer()
optional_bearer_scheme = HTTPBearer(auto_error=False)

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

async def get_team_context(team_id: UUID | None = None, x_team_id: UUID | None = Header(default=None, alias="X-Team-Id"), current_user: UserModel = Depends(get_current_user)) -> TeamContext:
    if team_id is None:
        team_id = x_team_id
    if team_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing team_id (query or X-Team-Id header)")
    try:
        res = supabase.table("team_members").select("role").eq("team_id", str(team_id)).eq("user_id", str(current_user.id)).limit(1).execute()
        rows = getattr(res, 'data', []) or []
        if not rows:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a team member")
        row = rows[0]
        return TeamContext(team_id=team_id, role=row.get('role') or 'viewer')
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Team context resolution failed: {e}")
        raise HTTPException(status_code=500, detail="Team context resolution failed")

def team_role_required(*allowed: str):
    async def checker(ctx: TeamContext = Depends(get_team_context)):
        if allowed and ctx.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient team role")
        return ctx
    return checker

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

# Optional auth: returns None when missing/invalid instead of raising
async def get_optional_user(credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer_scheme)) -> UserModel | None:
    if not credentials:
        return None
    token = credentials.credentials
    try:
        user_response = supabase.auth.get_user(token)
        user = getattr(user_response, "user", None)
        if not user or not getattr(user, "id", None) or not getattr(user, "email", None):
            return None
        return UserModel(id=UUID(user.id), email=user.email)
    except Exception:
        return None

# Workspace RBAC helpers
class WorkspaceContext(BaseModel):
    workspace_id: UUID
    role: str

async def get_workspace_member(workspace_id: UUID, current_user: UserModel = Depends(get_current_user)) -> WorkspaceContext:
    try:
        res = supabase.table("workspace_members").select("role,status").eq("workspace_id", str(workspace_id)).eq("user_id", str(current_user.id)).limit(1).execute()
        rows = getattr(res, 'data', []) or []
        if not rows:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a workspace member")
        r = rows[0]
        if r.get('status') != 'active':
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Membership inactive")
        return WorkspaceContext(workspace_id=workspace_id, role=r.get('role') or 'member')
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workspace membership lookup failed: {e}")
        raise HTTPException(status_code=500, detail="Workspace membership validation failed")

def workspace_role_required(*allowed: str):
    async def checker(ctx: WorkspaceContext = Depends(get_workspace_member)):
        if allowed and ctx.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return ctx
    return checker

def enforce_workspace_scoped_query(table: str, field: str = "workspace_id"):
    """Factory returning a helper to assert a record belongs to a workspace before proceeding."""
    async def validator(record_id: UUID, ctx: WorkspaceContext = Depends(get_workspace_member)):
        try:
            res = supabase.table(table).select(f"id,{field}").eq("id", str(record_id)).limit(1).execute()
            rows = getattr(res, 'data', []) or []
            if not rows:
                raise HTTPException(status_code=404, detail="Resource not found")
            row = rows[0]
            if str(row.get(field)) != str(ctx.workspace_id):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-workspace access denied")
            return row
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Workspace scope validation failed: {e}")
            raise HTTPException(status_code=500, detail="Workspace scope validation failed")
    return validator

# Convenience: resolve workspace context from query or X-Workspace-Id header
async def get_workspace_context(workspace_id: UUID | None = None, x_workspace_id: UUID | None = Header(default=None, alias="X-Workspace-Id"), current_user: UserModel = Depends(get_current_user)) -> WorkspaceContext:
    if workspace_id is None:
        workspace_id = x_workspace_id
    if workspace_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing workspace_id (query or X-Workspace-Id header)")
    try:
        res = supabase.table("workspace_members").select("role,status").eq("workspace_id", str(workspace_id)).eq("user_id", str(current_user.id)).limit(1).execute()
        rows = getattr(res, 'data', []) or []
        if not rows:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a workspace member")
        r = rows[0]
        if r.get('status') != 'active':
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Membership inactive")
        return WorkspaceContext(workspace_id=workspace_id, role=r.get('role') or 'member')
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workspace context resolution failed: {e}")
        raise HTTPException(status_code=500, detail="Workspace context resolution failed")
