from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional, Any
from uuid import UUID, uuid4
import re
import logging
from app.core.dependencies import supabase, get_current_user, UserModel, get_workspace_member

logger = logging.getLogger("cognisim_ai")

router = APIRouter(prefix="/api/workspaces", tags=["Workspaces"], dependencies=[Depends(get_current_user)])

class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: Optional[str] = Field(None, max_length=500)

class Workspace(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    slug: Optional[str] = None
    plan: Optional[str] = None
    member_role: Optional[str] = None  # Role of current user within this workspace

class WorkspaceMember(BaseModel):
    id: UUID
    workspace_id: UUID
    user_id: Optional[UUID] = None
    invited_email: Optional[str] = None
    role: str
    status: str
    created_at: Optional[str] = None
    joined_at: Optional[str] = None

class InviteWorkspaceMember(BaseModel):
    email: str
    role: str = Field("member", pattern="^(owner|admin|member|viewer)$")

class UpdateWorkspaceMemberRole(BaseModel):
    role: str = Field(..., pattern="^(owner|admin|member|viewer)$")

class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    description: Optional[str] = Field(None, max_length=500)

class WorkspaceDetail(Workspace):
    members_count: int
    # Placeholder for future stats (projects_count, integrations_count, etc.)
    # Add fields as needed
    settings: Optional[dict] = None

class WorkspaceActivityEvent(BaseModel):
    id: UUID
    workspace_id: UUID
    action: str
    actor_user_id: Optional[UUID] = None
    created_at: Optional[str] = None
    meta: Optional[dict] = None

def _workspace_from_row(row: dict) -> Workspace:
    return Workspace(
        id=row["id"],
        name=row["name"],
        description=row.get("description"),
        slug=row.get("slug"),
    plan=row.get("plan"),
    member_role=row.get("member_role") or row.get("role")  # attempt to map role keys
    )

def _log_activity(workspace_id: str, actor_user_id: str, action: str, meta: Optional[dict] = None):
    """Best-effort activity logging; ignores errors if table not present."""
    try:
        supabase.table("workspace_activity").insert({
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
            "action": action,
            "meta": meta or {}
        }).execute()
    except Exception as e:
        logger.debug(f"Activity log skipped ({action}) for workspace {workspace_id}: {e}")

@router.get("", response_model=List[Workspace])
def list_workspaces(current_user: UserModel = Depends(get_current_user)):
    """List workspaces the current user is a member of (no auto-create)."""
    user_id = str(current_user.id)
    workspaces_data = []
    # Prefer RPC if migration added it
    # 1. Attempt RPC
    rpc_failed = False
    try:
        rpc_res = supabase.rpc("list_user_workspaces", {"p_user_id": user_id}).execute()
        workspaces_data = getattr(rpc_res, "data", []) or []
    except Exception as e:
        rpc_failed = True
        logger.warning(f"RPC list_user_workspaces failed, fallback to join: {e}")

    # 2. Fallback direct join if rpc failed or returned empty
    if rpc_failed or not workspaces_data:
        try:
            join_res = (
                supabase.table("workspaces")
                .select("id,name,description,slug,plan,workspace_members!inner(user_id,role,status)")
                .eq("workspace_members.user_id", user_id)
                .execute()
            )
            workspaces_data = getattr(join_res, "data", []) or []
            # Map nested join role into flat structure
            for row in workspaces_data:
                # Supabase join shape may embed workspace_members as list
                wm = row.get('workspace_members')
                if isinstance(wm, list) and wm:
                    row['member_role'] = wm[0].get('role')
        except Exception as inner_e:
            logger.error(f"Failed to list workspaces for user {user_id}: {inner_e}")
            raise HTTPException(status_code=500, detail="Failed to list workspaces")

    # 3. If still missing member_role, fetch roles in batch
    missing_roles = [w for w in workspaces_data if not w.get('member_role')]
    if missing_roles:
        try:
            ids = [w['id'] for w in missing_roles]
            mem_res = (
                supabase.table('workspace_members')
                .select('workspace_id,role')
                .in_('workspace_id', ids)  # type: ignore
                .eq('user_id', user_id)
                .execute()
            )
            mem_map = {m['workspace_id']: m.get('role') for m in (getattr(mem_res, 'data', []) or [])}
            for row in workspaces_data:
                if not row.get('member_role') and row['id'] in mem_map:
                    row['member_role'] = mem_map[row['id']]
        except Exception as e:
            logger.debug(f"Could not backfill workspace member roles: {e}")

    return [_workspace_from_row(w) for w in workspaces_data]

@router.post("", response_model=Workspace, status_code=status.HTTP_201_CREATED)
def create_workspace(body: WorkspaceCreate, current_user: UserModel = Depends(get_current_user)):
    """Create a new workspace and owner membership."""
    wid = str(uuid4())
    name_clean = body.name.strip()
    # Basic slug generation (lowercase, alphanum & dashes)
    base_slug = re.sub(r"[^a-z0-9-]", "-", name_clean.lower().replace(" ", "-"))
    try:
        insert_payload = {
            "id": wid,
            "name": name_clean,
            "description": (body.description or None),
            "slug": base_slug,
            "plan": "free",
            "created_by": str(current_user.id),
        }
        ins = supabase.table("workspaces").insert(insert_payload).execute()
        data = getattr(ins, "data", None)
        if not data:
            raise HTTPException(status_code=500, detail="Failed to create workspace")

        # Owner membership
        try:
            supabase.table("workspace_members").insert({
                "workspace_id": wid,
                "user_id": str(current_user.id),
                "role": "owner",
                "status": "active"
            }).execute()
        except Exception as member_e:
            logger.error(f"Failed to create owner membership for workspace {wid}: {member_e}")
            # Do not fail entire request; ownership can be repaired manually
        return _workspace_from_row(data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workspace creation failed: {e}")
        raise HTTPException(status_code=500, detail="Workspace creation failed")

    finally:
        try:
            _log_activity(wid, str(current_user.id), "workspace_created", {"name": name_clean})
        except Exception:
            pass

@router.get("/default", response_model=Workspace)
def get_default_workspace(current_user: UserModel = Depends(get_current_user)):
    """Return the first workspace for the user or 404 if none (no implicit creation)."""
    user_id = str(current_user.id)
    try:
        rpc_res = supabase.rpc("list_user_workspaces", {"p_user_id": user_id}).execute()
        data = getattr(rpc_res, "data", []) or []
    except Exception:
        # Fallback join
        join_res = (
            supabase.table("workspaces")
            .select("id,name,description,slug,plan,workspace_members!inner(user_id)")
            .eq("workspace_members.user_id", user_id)
            .limit(1)
            .execute()
        )
        data = getattr(join_res, "data", []) or []
    if not data:
        raise HTTPException(status_code=404, detail="No workspace found for user")
    return _workspace_from_row(data[0])


# -------- Membership Utilities & Endpoints (Phase 2) -------- #

def _require_workspace_member(workspace_id: str, user_id: str, allowed_roles: Optional[List[str]] = None):
    """Raises 403 if user not member or lacks required role."""
    try:
        res = (
            supabase.table("workspace_members")
            .select("role,status")
            .eq("workspace_id", workspace_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", []) or []
        if not data:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a workspace member")
        member = data[0]
        if member.get("status") != "active":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Membership inactive")
        if allowed_roles and member.get("role") not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return member
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Membership check failed for user {user_id} workspace {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Membership validation failed")


@router.get("/{workspace_id}/members", response_model=List[WorkspaceMember])
def list_workspace_members(workspace_id: UUID, current_user: UserModel = Depends(get_current_user)):
    """List members for a workspace."""
    _require_workspace_member(str(workspace_id), str(current_user.id))
    try:
        res = (
            supabase.table("workspace_members")
            .select("id,workspace_id,user_id,invited_email,role,status,created_at,joined_at")
            .eq("workspace_id", str(workspace_id))
            .order("created_at", desc=False)
            .execute()
        )
        rows = getattr(res, "data", []) or []
        return [WorkspaceMember(**r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to list members for workspace {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list members")


@router.post("/{workspace_id}/members/invite", response_model=WorkspaceMember, status_code=status.HTTP_201_CREATED)
def invite_workspace_member(workspace_id: UUID, body: InviteWorkspaceMember, current_user: UserModel = Depends(get_current_user)):
    """Invite a user (by email) to a workspace; creates pending membership."""
    _require_workspace_member(str(workspace_id), str(current_user.id), ["owner", "admin"])
    email_clean = body.email.strip().lower()
    try:
        insert_payload = {
            "workspace_id": str(workspace_id),
            "invited_email": email_clean,
            "role": body.role,
            "status": "invited",
        }
        ins = supabase.table("workspace_members").insert(insert_payload).execute()
        data = getattr(ins, "data", None)
        if not data:
            raise HTTPException(status_code=500, detail="Failed to create invitation")
        return WorkspaceMember(**data[0])
    except HTTPException:
        raise
    except Exception as e:
        # Handle duplicate invite
        logger.error(f"Invite failed for workspace {workspace_id} email {email_clean}: {e}")
        raise HTTPException(status_code=500, detail="Failed to invite member")


@router.patch("/{workspace_id}/members/{member_id}/role", response_model=WorkspaceMember)
def update_workspace_member_role(workspace_id: UUID, member_id: UUID, body: UpdateWorkspaceMemberRole, current_user: UserModel = Depends(get_current_user)):
    _require_workspace_member(str(workspace_id), str(current_user.id), ["owner", "admin"])
    try:
        upd = (
            supabase.table("workspace_members")
            .update({"role": body.role})
            .eq("id", str(member_id))
            .eq("workspace_id", str(workspace_id))
            .execute()
        )
        data = getattr(upd, "data", None) or []
        if not data:
            raise HTTPException(status_code=404, detail="Member not found")
        return WorkspaceMember(**data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Role update failed for member {member_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update role")


@router.delete("/{workspace_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_workspace_member(workspace_id: UUID, member_id: UUID, current_user: UserModel = Depends(get_current_user)):
    _require_workspace_member(str(workspace_id), str(current_user.id), ["owner", "admin"])
    try:
        del_res = (
            supabase.table("workspace_members")
            .delete()
            .eq("id", str(member_id))
            .eq("workspace_id", str(workspace_id))
            .execute()
        )
        return None
    except Exception as e:
        logger.error(f"Failed to remove member {member_id} from workspace {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove member")


# -------- Workspace Management Enhancements -------- #

@router.get("/{workspace_id}", response_model=WorkspaceDetail)
def get_workspace_detail(workspace_id: UUID, current_user: UserModel = Depends(get_current_user)):
    """Return workspace detail plus basic counts."""
    _require_workspace_member(str(workspace_id), str(current_user.id))
    # Fetch workspace core data
    ws_rows: List[dict] = []
    try:
        ws_res = supabase.table("workspaces").select("id,name,description,slug,plan").eq("id", str(workspace_id)).limit(1).execute()
        ws_rows = getattr(ws_res, "data", []) or []
    except Exception as e:
        logger.error(f"Workspace detail fetch failed (core) {workspace_id}: {e}")
    if not ws_rows:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Role for current user
    try:
        role_res = supabase.table("workspace_members").select("role").eq("workspace_id", str(workspace_id)).eq("user_id", str(current_user.id)).limit(1).execute()
        role_rows = getattr(role_res, "data", []) or []
        if role_rows:
            ws_rows[0]["member_role"] = role_rows[0].get("role")
    except Exception as e:
        logger.debug(f"Role fetch failed for workspace {workspace_id}: {e}")

    # Members count (manual only to avoid RPC dependency)
    members_count = 0
    try:
        mc_res = supabase.table("workspace_members").select("id", count='exact').eq("workspace_id", str(workspace_id)).execute()  # type: ignore
        members_count = int(getattr(mc_res, "count", 0) or 0)
    except Exception as e:
        logger.debug(f"Member count failed for workspace {workspace_id}: {e}")

    # Settings fetch
    settings_obj: Optional[dict] = None
    try:
        st_res = supabase.table("workspace_settings").select("estimation_scale,default_sprint_length,timezone").eq("workspace_id", str(workspace_id)).limit(1).execute()
        st_rows = getattr(st_res, 'data', []) or []
        if st_rows:
            settings_obj = st_rows[0]
    except Exception:
        pass
    base = _workspace_from_row(ws_rows[0])
    return WorkspaceDetail(**base.dict(), members_count=members_count, settings=settings_obj)

class WorkspaceSettingsUpdate(BaseModel):
    estimation_scale: Optional[str] = Field(None, max_length=32)
    default_sprint_length: Optional[int] = Field(None, ge=1, le=60)
    timezone: Optional[str] = Field(None, max_length=64)

@router.patch("/{workspace_id}/settings", status_code=status.HTTP_204_NO_CONTENT)
def update_workspace_settings(workspace_id: UUID, body: WorkspaceSettingsUpdate, current_user: UserModel = Depends(get_current_user)):
    _require_workspace_member(str(workspace_id), str(current_user.id), ["owner", "admin"])
    updates: dict[str, Any] = {}
    for field in ["estimation_scale", "default_sprint_length", "timezone"]:
        val = getattr(body, field)
        if val is not None:
            updates[field] = val
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    try:
        # Upsert style: attempt update; if no row insert
        existing = supabase.table("workspace_settings").select("workspace_id").eq("workspace_id", str(workspace_id)).limit(1).execute()
        rows = getattr(existing, 'data', []) or []
        if rows:
            supabase.table("workspace_settings").update(updates).eq("workspace_id", str(workspace_id)).execute()
        else:
            supabase.table("workspace_settings").insert({"workspace_id": str(workspace_id), **updates}).execute()
        _log_activity(str(workspace_id), str(current_user.id), "workspace_settings_updated", {"fields": list(updates.keys())})
    except Exception as e:
        logger.error(f"Settings update failed for workspace {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update settings")
    return None

class WorkspaceSwitchBody(BaseModel):
    workspace_id: UUID

@router.post("/switch", response_model=Workspace)
def switch_workspace(body: WorkspaceSwitchBody, current_user: UserModel = Depends(get_current_user)):
    """Validate membership and return workspace (client can persist id locally)."""
    ws_id = str(body.workspace_id)
    _require_workspace_member(ws_id, str(current_user.id))
    ws_res = supabase.table("workspaces").select("id,name,description,slug,plan").eq("id", ws_id).limit(1).execute()
    rows = getattr(ws_res, 'data', []) or []
    if not rows:
        raise HTTPException(status_code=404, detail="Workspace not found")
    row = rows[0]
    row['member_role'] = _require_workspace_member(ws_id, str(current_user.id)).get('role')
    return _workspace_from_row(row)


@router.patch("/{workspace_id}", response_model=Workspace)
def update_workspace(workspace_id: UUID, body: WorkspaceUpdate, current_user: UserModel = Depends(get_current_user)):
    """Update workspace name/description (owner or admin)."""
    _require_workspace_member(str(workspace_id), str(current_user.id), ["owner", "admin"])
    updates: dict[str, Any] = {}
    if body.name is not None:
        name_clean = body.name.strip()
        updates["name"] = name_clean
        # Optional: regenerate slug (simple)
        try:
            base_slug = re.sub(r"[^a-z0-9-]", "-", name_clean.lower().replace(" ", "-"))
            updates["slug"] = base_slug
        except Exception:
            pass
    if body.description is not None:
        updates["description"] = body.description.strip() or None
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    try:
        upd = supabase.table("workspaces").update(updates).eq("id", str(workspace_id)).execute()
        data = getattr(upd, "data", []) or []
        if not data:
            raise HTTPException(status_code=404, detail="Workspace not found")
        row = data[0]
        # Add current user role
        row["member_role"] = _require_workspace_member(str(workspace_id), str(current_user.id))["role"]
        _log_activity(str(workspace_id), str(current_user.id), "workspace_updated", {k: updates[k] for k in updates})
        return _workspace_from_row(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update workspace")


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(workspace_id: UUID, current_user: UserModel = Depends(get_current_user)):
    """Soft delete workspace (owner only)."""
    member = _require_workspace_member(str(workspace_id), str(current_user.id), ["owner"])
    try:
        # Soft delete: set deleted_at if column exists else hard delete
        try:
            upd = supabase.table("workspaces").update({"deleted_at": "now()"}).eq("id", str(workspace_id)).execute()
            data = getattr(upd, "data", [])
            if not data:
                # fallback hard delete
                supabase.table("workspaces").delete().eq("id", str(workspace_id)).execute()
        except Exception:
            supabase.table("workspaces").delete().eq("id", str(workspace_id)).execute()
        _log_activity(str(workspace_id), str(current_user.id), "workspace_deleted", {"by": str(current_user.id)})
        return None
    except Exception as e:
        logger.error(f"Failed to delete workspace {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete workspace")


@router.post("/{workspace_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_workspace(workspace_id: UUID, current_user: UserModel = Depends(get_current_user)):
    """Leave a workspace (non-owner OR owner only if another owner exists)."""
    # Get current membership
    try:
        mem_res = supabase.table("workspace_members").select("id,role").eq("workspace_id", str(workspace_id)).eq("user_id", str(current_user.id)).limit(1).execute()
        rows = getattr(mem_res, "data", []) or []
        if not rows:
            raise HTTPException(status_code=404, detail="Membership not found")
        role = rows[0].get("role")
        if role == "owner":
            # Count other owners
            owners_res = supabase.table("workspace_members").select("id", count='exact').eq("workspace_id", str(workspace_id)).eq("role", "owner").execute()  # type: ignore
            owners_count = getattr(owners_res, "count", 0) or 0
            if owners_count <= 1:
                raise HTTPException(status_code=400, detail="Transfer ownership before leaving")
        supabase.table("workspace_members").delete().eq("workspace_id", str(workspace_id)).eq("user_id", str(current_user.id)).execute()
        _log_activity(str(workspace_id), str(current_user.id), "member_left", {})
        return None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Leave workspace failed for {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to leave workspace")


class TransferOwnershipBody(BaseModel):
    new_owner_member_id: UUID

@router.post("/{workspace_id}/transfer-owner", status_code=status.HTTP_204_NO_CONTENT)
def transfer_ownership(workspace_id: UUID, body: TransferOwnershipBody, current_user: UserModel = Depends(get_current_user)):
    """Transfer ownership to another active member (current owner only)."""
    _require_workspace_member(str(workspace_id), str(current_user.id), ["owner"])
    try:
        # Verify target member exists & active
        target_res = supabase.table("workspace_members").select("id,role,status,user_id").eq("id", str(body.new_owner_member_id)).eq("workspace_id", str(workspace_id)).limit(1).execute()
        target_rows = getattr(target_res, "data", []) or []
        if not target_rows:
            raise HTTPException(status_code=404, detail="Target member not found")
        target = target_rows[0]
        if target.get("status") != "active":
            raise HTTPException(status_code=400, detail="Target member not active")
        # Demote current owner to admin
        supabase.table("workspace_members").update({"role": "admin"}).eq("workspace_id", str(workspace_id)).eq("user_id", str(current_user.id)).execute()
        # Promote target to owner
        supabase.table("workspace_members").update({"role": "owner"}).eq("id", str(body.new_owner_member_id)).execute()
        _log_activity(str(workspace_id), str(current_user.id), "ownership_transferred", {"to_member_id": str(body.new_owner_member_id)})
        return None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ownership transfer failed for workspace {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to transfer ownership")


@router.get("/{workspace_id}/activity", response_model=List[WorkspaceActivityEvent])
def list_workspace_activity(workspace_id: UUID, current_user: UserModel = Depends(get_current_user)):
    """List recent activity events (best-effort)."""
    _require_workspace_member(str(workspace_id), str(current_user.id))
    try:
        res = supabase.table("workspace_activity").select("id,workspace_id,action,actor_user_id,created_at,meta").eq("workspace_id", str(workspace_id)).order("created_at", desc=True).limit(50).execute()
        rows = getattr(res, "data", []) or []
        return [WorkspaceActivityEvent(**r) for r in rows]
    except Exception as e:
        logger.debug(f"Activity list failed for {workspace_id}: {e}")
        # Return empty list if table absent
        return []
