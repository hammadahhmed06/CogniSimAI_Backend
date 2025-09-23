from uuid import UUID, uuid4
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app.core.dependencies import (
    supabase,
    get_current_user,
    UserModel,
    get_workspace_context,
    WorkspaceContext,
    get_team_context,
    team_role_required,
)

router = APIRouter(prefix="/api/teams", tags=["teams"])


# ---------- Models ----------

class Team(BaseModel):
    id: UUID
    name: str
    my_role: Optional[str] = None


class TeamDetail(Team):
    members_count: Optional[int] = None


class TeamMember(BaseModel):
    id: UUID
    user_id: UUID
    role: str
    status: str


class CreateTeamRequest(BaseModel):
    name: str


class UpdateTeamRequest(BaseModel):
    name: str


class AddMemberRequest(BaseModel):
    user_id: UUID
    role: str = "viewer"  # viewer|editor|admin|owner
    status: str = "active"  # active|invited|disabled


class UpdateMemberRequest(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = "viewer"
    redirect: Optional[str] = None

class BatchAddMembersRequest(BaseModel):
    users: List[UUID]
    role: str = "viewer"
    status: str = "active"


# ---------- Helpers ----------

ALLOWED_ROLES = {"viewer", "editor", "admin", "owner"}
ALLOWED_STATUS = {"active", "invited", "disabled"}


def assert_valid_role(role: str):
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")


def assert_valid_status(status: str):
    if status not in ALLOWED_STATUS:
        raise HTTPException(status_code=400, detail="Invalid status")


def ensure_not_last_owner(team_id: UUID, member_id: UUID):
    res = (
        supabase.table("team_members")
        .select("id,role")
        .eq("id", str(member_id))
        .eq("team_id", str(team_id))
        .maybe_single()
        .execute()
    )
    row = getattr(res, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="Member not found")
    if row.get("role") == "owner":
        owners_res = (
            supabase.table("team_members")
            .select("id")
            .eq("team_id", str(team_id))
            .eq("role", "owner")
            .execute()
        )
        owners = getattr(owners_res, "data", []) or []
        if len(owners) <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove/demote the last owner")


# ---------- Routes ----------

@router.get("", response_model=List[TeamDetail])
@router.get("/", response_model=List[TeamDetail])
async def list_teams(
    current_user: UserModel = Depends(get_current_user),
    wctx: WorkspaceContext = Depends(get_workspace_context),
):
    res = (
        supabase
        .table("team_members")
        .select("team_id,role,teams!inner(id,name,workspace_id)")
        .eq("user_id", str(current_user.id))
        .eq("teams.workspace_id", str(wctx.workspace_id))
        .execute()
    )
    rows = getattr(res, "data", []) or []
    teams_map = {}
    team_ids: List[str] = []
    for r in rows:
        t = r.get("teams") or {}
        tid = t.get("id")
        if tid and str(t.get("workspace_id")) == str(wctx.workspace_id):
            teams_map[str(tid)] = {"id": str(tid), "name": t.get("name") or "Team", "my_role": r.get("role")}
            team_ids.append(str(tid))

    counts: dict[str, int] = {}
    if team_ids:
        cm_res = (
            supabase
            .table("team_members")
            .select("id,team_id")
            .in_("team_id", team_ids)
            .execute()
        )
        cm_rows = getattr(cm_res, "data", []) or []
        for m in cm_rows:
            tid = str(m.get("team_id"))
            counts[tid] = counts.get(tid, 0) + 1

    out: List[TeamDetail] = []
    for tid, data in teams_map.items():
        out.append(TeamDetail(id=UUID(data["id"]), name=data["name"], my_role=data.get("my_role"), members_count=counts.get(tid, 0)))
    return out


@router.post("", response_model=Team)
@router.post("/", response_model=Team)
async def create_team(
    body: CreateTeamRequest,
    current_user: UserModel = Depends(get_current_user),
    wctx: WorkspaceContext = Depends(get_workspace_context),
):
    tid = uuid4()
    supabase.table("teams").insert({
        "id": str(tid),
        "name": body.name,
        "workspace_id": str(wctx.workspace_id),
    }).execute()
    supabase.table("team_members").insert({
        "id": str(uuid4()),
        "team_id": str(tid),
        "user_id": str(current_user.id),
        "role": "owner",
        "status": "active",
    }).execute()
    return Team(id=tid, name=body.name)


@router.get("/{team_id}", response_model=TeamDetail)
async def get_team(team_id: UUID, ctx=Depends(get_team_context)):
    res = (
        supabase.table("teams").select("id,name").eq("id", str(team_id)).maybe_single().execute()
    )
    row = getattr(res, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")
    count_res = supabase.table("team_members").select("id").eq("team_id", str(team_id)).execute()
    members_count = len(getattr(count_res, "data", []) or [])
    # fetch my_role from ctx if present
    my_role = None
    try:
        my_role = getattr(ctx, "role", None)
    except Exception:
        my_role = None
    return TeamDetail(id=UUID(row["id"]), name=row["name"], my_role=my_role, members_count=members_count)


@router.patch("/{team_id}", response_model=Team)
async def update_team(team_id: UUID, body: UpdateTeamRequest, ctx=Depends(team_role_required("admin", "owner"))):
    supabase.table("teams").update({"name": body.name}).eq("id", str(team_id)).execute()
    return Team(id=team_id, name=body.name)


@router.get("/{team_id}/members", response_model=List[TeamMember])
async def list_members(team_id: UUID, ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))):
    res = supabase.table("team_members").select("id,user_id,role,status").eq("team_id", str(team_id)).execute()
    rows = getattr(res, "data", []) or []
    return [TeamMember(id=UUID(r["id"]), user_id=UUID(r["user_id"]), role=r["role"], status=r.get("status", "active")) for r in rows]


@router.post("/{team_id}/members", response_model=TeamMember)
async def add_member(team_id: UUID, body: AddMemberRequest, ctx=Depends(team_role_required("admin", "owner"))):
    assert_valid_role(body.role)
    assert_valid_status(body.status)
    existing_res = (
        supabase.table("team_members")
        .select("id")
        .eq("team_id", str(team_id))
        .eq("user_id", str(body.user_id))
        .maybe_single()
        .execute()
    )
    existing = getattr(existing_res, "data", None)
    if existing:
        raise HTTPException(status_code=400, detail="User already a member")
    mid = uuid4()
    supabase.table("team_members").insert({
        "id": str(mid),
        "team_id": str(team_id),
        "user_id": str(body.user_id),
        "role": body.role,
        "status": body.status,
    }).execute()
    return TeamMember(id=mid, user_id=body.user_id, role=body.role, status=body.status)

@router.post("/{team_id}/members/batch")
async def add_members_batch(team_id: UUID, body: BatchAddMembersRequest, ctx=Depends(team_role_required("admin", "owner"))):
    assert_valid_role(body.role)
    assert_valid_status(body.status)
    added = 0
    for uid in body.users:
        try:
            existing_res = (
                supabase.table("team_members")
                .select("id")
                .eq("team_id", str(team_id))
                .eq("user_id", str(uid))
                .maybe_single()
                .execute()
            )
            existing = getattr(existing_res, "data", None)
            if existing:
                continue
            supabase.table("team_members").insert({
                "id": str(uuid4()),
                "team_id": str(team_id),
                "user_id": str(uid),
                "role": body.role,
                "status": body.status,
            }).execute()
            added += 1
        except Exception:
            continue
    return {"added": added}


@router.patch("/{team_id}/members/{member_id}", response_model=TeamMember)
async def update_member(team_id: UUID, member_id: UUID, body: UpdateMemberRequest, ctx=Depends(team_role_required("admin", "owner"))):
    row_res = (
        supabase.table("team_members").select("id,user_id,role,status").eq("id", str(member_id)).eq("team_id", str(team_id)).maybe_single().execute()
    )
    row = getattr(row_res, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="Member not found")
    patch: dict = {}
    if body.role is not None:
        assert_valid_role(body.role)
        if body.role in {"viewer", "editor", "admin"} and row.get("role") == "owner":
            ensure_not_last_owner(team_id, member_id)
        patch["role"] = body.role
    if body.status is not None:
        assert_valid_status(body.status)
        patch["status"] = body.status
    if not patch:
        return TeamMember(id=UUID(row["id"]), user_id=UUID(row["user_id"]), role=row["role"], status=row.get("status", "active"))
    supabase.table("team_members").update(patch).eq("id", str(member_id)).eq("team_id", str(team_id)).execute()
    fr_res = (
        supabase.table("team_members").select("id,user_id,role,status").eq("id", str(member_id)).maybe_single().execute()
    )
    fr = getattr(fr_res, "data", None)
    if not fr:
        raise HTTPException(status_code=404, detail="Member not found after update")
    return TeamMember(id=UUID(fr["id"]), user_id=UUID(fr["user_id"]), role=fr["role"], status=fr.get("status", "active"))


@router.delete("/{team_id}/members/{member_id}")
async def remove_member(team_id: UUID, member_id: UUID, ctx=Depends(team_role_required("admin", "owner"))):
    ensure_not_last_owner(team_id, member_id)
    supabase.table("team_members").delete().eq("id", str(member_id)).eq("team_id", str(team_id)).execute()
    return {"success": True}


@router.post("/{team_id}/invite")
async def invite_member(team_id: UUID, body: InviteMemberRequest, ctx=Depends(team_role_required("admin", "owner"))):
    admin = getattr(getattr(supabase, "auth", None), "admin", None)
    if admin is None or not hasattr(admin, "invite_user_by_email"):
        raise HTTPException(status_code=500, detail="Auth provider not available")
    try:
        if body.redirect:
            admin.invite_user_by_email(body.email, options={"redirect_to": body.redirect})
        else:
            admin.invite_user_by_email(body.email)
        return {"message": "Invite sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Invite failed: {e}")
