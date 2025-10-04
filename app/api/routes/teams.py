from uuid import UUID, uuid4
from typing import List, Optional
from datetime import datetime, timedelta, date
import os
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
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
from app.services.email_service import send_invitation_email
from app.models.team_models import (
    VelocityResponse,
    CycleTimeResponse,
    WorkloadResponse,
    SprintCompletionResponse,
    TeamMetricsSummary,
    TeamCapacityResponse,
    SetCapacityRequest,
    TeamSettingsResponse,
    UpdateTeamSettingsRequest,
    TeamGoalResponse,
    CreateGoalRequest,
    UpdateGoalRequest,
    NotificationSettingsResponse,
    UpdateNotificationSettingsRequest,
    DefaultAssigneeResponse,
    SetDefaultAssigneeRequest,
    TeamLabelResponse,
    CreateLabelRequest,
    UpdateLabelRequest,
    ResourceCategoryResponse,
    CreateCategoryRequest,
    UpdateCategoryRequest,
    ResourceResponse,
    CreateResourceRequest,
    UpdateResourceRequest,
    ChatMessageResponse,
    CreateChatMessageRequest,
    UpdateChatMessageRequest,
    AddReactionRequest,
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
async def invite_member(
    team_id: UUID, 
    body: InviteMemberRequest, 
    ctx=Depends(team_role_required("admin", "owner")),
    current_user: UserModel = Depends(get_current_user)
):
    """Send email invitation to join a team."""
    
    # Get team details
    team_res = supabase.table("teams").select("id,name,workspace_id").eq("id", str(team_id)).maybe_single().execute()
    team = getattr(team_res, "data", None)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    # Check if user already exists and is a member
    existing_member = (
        supabase.table("team_members")
        .select("id,user_id")
        .eq("team_id", str(team_id))
        .execute()
    )
    member_rows = getattr(existing_member, "data", []) or []
    
    # Get user IDs from email - handle case where user doesn't exist yet
    existing_user = None
    try:
        user_res = supabase.table("user_profiles").select("user_id,email").eq("email", body.email).execute()
        user_data = getattr(user_res, "data", [])
        if user_data and len(user_data) > 0:
            existing_user = user_data[0]
    except Exception:
        existing_user = None
    
    if existing_user:
        # User exists, check if already a member
        user_id = existing_user.get("user_id")
        for m in member_rows:
            if str(m.get("user_id")) == str(user_id):
                raise HTTPException(status_code=400, detail="User is already a team member")
    
    # Create invitation token
    invitation_token = uuid4()
    expires_at = datetime.utcnow() + timedelta(days=7)
    
    # Store invitation in database
    invitation_data = {
        "id": str(uuid4()),
        "token": str(invitation_token),
        "email": body.email,
        "team_id": str(team_id),
        "workspace_id": team.get("workspace_id"),
        "invited_by": str(current_user.id),
        "role": body.role,
        "status": "pending",
        "expires_at": expires_at.isoformat(),
        "created_at": datetime.utcnow().isoformat()
    }
    
    try:
        supabase.table("invitations").insert(invitation_data).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create invitation: {str(e)}")
    
    # Generate invitation link
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:8080")
    invite_link = f"{frontend_url}/accept-invite?token={invitation_token}&team={team_id}"
    
    # Get inviter name
    inviter_name = "A team member"
    try:
        inviter_profile = supabase.table("user_profiles").select("full_name,email").eq("user_id", str(current_user.id)).execute()
        inviter_data_list = getattr(inviter_profile, "data", [])
        if inviter_data_list and len(inviter_data_list) > 0:
            inviter_data = inviter_data_list[0]
            inviter_name = inviter_data.get("full_name") or inviter_data.get("email") or "A team member"
    except Exception:
        pass
    
    # Send email
    email_sent = False
    email_result = None
    try:
        email_result = send_invitation_email(
            to_email=body.email,
            invite_link=invite_link,
            inviter_name=inviter_name,
            workspace_name=team.get("name", "a team")
        )
        email_sent = True
    except Exception as e:
        # Log error but don't fail the request
        print(f"Failed to send invitation email: {e}")
        email_result = {"error": str(e)}
    
    return {
        "message": "Invitation created" + (" and email sent" if email_sent else " but email failed"),
        "token": str(invitation_token),
        "invite_link": invite_link,
        "email_sent": email_sent,
        "email_result": email_result,
        "expires_at": expires_at.isoformat()
    }


# ============= TEAM METRICS ENDPOINTS (Sprint 1) =============

@router.get("/{team_id}/metrics/velocity", response_model=VelocityResponse)
async def get_team_velocity(
    team_id: UUID,
    days: int = Query(30, ge=1, le=365),
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Get team velocity over time (story points completed per sprint/week)"""
    try:
        from_date = datetime.now().date() - timedelta(days=days)
        
        # Query team metrics
        result = supabase.table("team_metrics")\
            .select("metric_date, velocity, stories_completed")\
            .eq("team_id", str(team_id))\
            .gte("metric_date", from_date.isoformat())\
            .order("metric_date", desc=False)\
            .execute()
        
        data_points = []
        total_velocity = Decimal(0)
        count = 0
        
        for item in result.data:
            velocity = Decimal(str(item.get("velocity") or 0))
            data_points.append({
                "date": item["metric_date"],
                "velocity": velocity,
                "stories_completed": item.get("stories_completed", 0)
            })
            if velocity > 0:
                total_velocity += velocity
                count += 1
        
        avg_velocity = total_velocity / count if count > 0 else None
        
        # Calculate trend
        trend = "stable"
        if len(data_points) >= 3:
            first_half = [d["velocity"] for d in data_points[:len(data_points)//2] if d["velocity"]]
            second_half = [d["velocity"] for d in data_points[len(data_points)//2:] if d["velocity"]]
            if first_half and second_half:
                avg_first = Decimal(str(sum(first_half) / len(first_half)))
                avg_second = Decimal(str(sum(second_half) / len(second_half)))
                if avg_second > avg_first * Decimal("1.1"):
                    trend = "increasing"
                elif avg_second < avg_first * Decimal("0.9"):
                    trend = "decreasing"
        
        return {
            "team_id": team_id,
            "period_days": days,
            "data_points": data_points,
            "average_velocity": avg_velocity,
            "trend": trend
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch velocity metrics: {str(e)}")


@router.get("/{team_id}/metrics/cycle-time", response_model=CycleTimeResponse)
async def get_team_cycle_time(
    team_id: UUID,
    days: int = Query(30, ge=1, le=365),
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Get average cycle time (start to done) for issues"""
    try:
        from_date = datetime.now().date() - timedelta(days=days)
        
        result = supabase.table("team_metrics")\
            .select("metric_date, avg_cycle_time_hours, stories_completed")\
            .eq("team_id", str(team_id))\
            .gte("metric_date", from_date.isoformat())\
            .order("metric_date", desc=False)\
            .execute()
        
        data_points = []
        total_cycle_time = Decimal(0)
        count = 0
        
        for item in result.data:
            cycle_time = Decimal(str(item.get("avg_cycle_time_hours") or 0))
            data_points.append({
                "date": item["metric_date"],
                "avg_cycle_time_hours": cycle_time if cycle_time > 0 else None,
                "issues_count": item.get("stories_completed", 0)
            })
            if cycle_time > 0:
                total_cycle_time += cycle_time
                count += 1
        
        avg_cycle_time = total_cycle_time / count if count > 0 else None
        
        # Calculate trend (lower is better for cycle time)
        trend = "stable"
        if len(data_points) >= 3:
            first_half = [d["avg_cycle_time_hours"] for d in data_points[:len(data_points)//2] if d["avg_cycle_time_hours"]]
            second_half = [d["avg_cycle_time_hours"] for d in data_points[len(data_points)//2:] if d["avg_cycle_time_hours"]]
            if first_half and second_half:
                avg_first = Decimal(str(sum(first_half) / len(first_half)))
                avg_second = Decimal(str(sum(second_half) / len(second_half)))
                if avg_second < avg_first * Decimal("0.9"):
                    trend = "decreasing"
                elif avg_second > avg_first * Decimal("1.1"):
                    trend = "increasing"
        
        return {
            "team_id": team_id,
            "period_days": days,
            "data_points": data_points,
            "average_cycle_time_hours": avg_cycle_time,
            "trend": trend
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch cycle time metrics: {str(e)}")


@router.get("/{team_id}/metrics/workload", response_model=WorkloadResponse)
async def get_team_workload(
    team_id: UUID,
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Get current workload distribution across team members"""
    try:
        # Get team members
        members_result = supabase.table("team_members")\
            .select("user_id")\
            .eq("team_id", str(team_id))\
            .eq("status", "active")\
            .execute()
        
        if not members_result.data:
            return {
                "team_id": team_id,
                "members": [],
                "total_issues": 0,
                "total_in_progress": 0,
                "average_workload": Decimal(0)
            }
        
        members = []
        total_issues = 0
        total_in_progress = 0
        
        for member in members_result.data:
            user_id = member["user_id"]
            
            # Get user details
            user_result = supabase.auth.admin.get_user_by_id(user_id)
            user_email = user_result.user.email if user_result.user else "Unknown"
            user_name = user_result.user.user_metadata.get("full_name", user_email) if user_result.user else user_email
            
            # Get assigned issues count
            issues_result = supabase.table("issues")\
                .select("id, status, story_points")\
                .eq("team_id", str(team_id))\
                .eq("assignee_id", user_id)\
                .neq("status", "done")\
                .execute()
            
            assigned_count = len(issues_result.data or [])
            in_progress_count = len([i for i in (issues_result.data or []) if i.get("status") == "in_progress"])
            
            # Sum story points
            story_points = sum([Decimal(str(i.get("story_points") or 0)) for i in (issues_result.data or [])])
            
            members.append({
                "user_id": user_id,
                "user_name": user_name,
                "user_email": user_email,
                "assigned_issues": assigned_count,
                "in_progress_issues": in_progress_count,
                "story_points": story_points,
                "capacity_utilization": None  # Can be enhanced with capacity data
            })
            
            total_issues += assigned_count
            total_in_progress += in_progress_count
        
        avg_workload = Decimal(total_issues) / len(members) if members else Decimal(0)
        
        return {
            "team_id": team_id,
            "members": members,
            "total_issues": total_issues,
            "total_in_progress": total_in_progress,
            "average_workload": avg_workload
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch workload metrics: {str(e)}")


@router.get("/{team_id}/metrics/sprint-completion", response_model=SprintCompletionResponse)
async def get_sprint_completion_rate(
    team_id: UUID,
    sprints: int = Query(5, ge=1, le=20),
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Get sprint completion rates (committed vs completed)"""
    try:
        # Get recent sprints for the team
        sprints_result = supabase.table("sprints")\
            .select("id, name, start_date, end_date")\
            .eq("team_id", str(team_id))\
            .order("start_date", desc=True)\
            .limit(sprints)\
            .execute()
        
        sprint_data = []
        total_completion_rate = Decimal(0)
        
        for sprint in sprints_result.data:
            sprint_id = sprint["id"]
            
            # Get issues for this sprint
            issues_result = supabase.table("issues")\
                .select("status, story_points")\
                .eq("sprint_id", sprint_id)\
                .execute()
            
            committed_points = sum([Decimal(str(i.get("story_points") or 0)) for i in issues_result.data])
            completed_points = sum([
                Decimal(str(i.get("story_points") or 0)) 
                for i in issues_result.data 
                if i.get("status") == "done"
            ])
            
            completion_rate = Decimal(str((completed_points / committed_points * 100))) if committed_points > 0 else Decimal(0)
            
            sprint_data.append({
                "sprint_id": sprint_id,
                "sprint_name": sprint.get("name", "Unnamed Sprint"),
                "start_date": sprint.get("start_date"),
                "end_date": sprint.get("end_date"),
                "committed_points": committed_points,
                "completed_points": completed_points,
                "completion_rate": completion_rate
            })
            
            total_completion_rate += completion_rate
        
        avg_completion_rate = total_completion_rate / len(sprint_data) if sprint_data else Decimal(0)
        
        # Calculate trend
        trend = "stable"
        if len(sprint_data) >= 3:
            first_half_rates = [s["completion_rate"] for s in sprint_data[:len(sprint_data)//2]]
            second_half_rates = [s["completion_rate"] for s in sprint_data[len(sprint_data)//2:]]
            avg_first = Decimal(str(sum(first_half_rates) / len(first_half_rates)))
            avg_second = Decimal(str(sum(second_half_rates) / len(second_half_rates)))
            if avg_second > avg_first * Decimal("1.1"):
                trend = "increasing"
            elif avg_second < avg_first * Decimal("0.9"):
                trend = "decreasing"
        
        return {
            "team_id": team_id,
            "sprints": sprint_data,
            "average_completion_rate": avg_completion_rate,
            "trend": trend
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch sprint completion metrics: {str(e)}")


@router.get("/{team_id}/metrics/summary", response_model=TeamMetricsSummary)
async def get_team_metrics_summary(
    team_id: UUID,
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Get comprehensive team metrics summary"""
    try:
        # Get team name
        team_result = supabase.table("teams")\
            .select("name")\
            .eq("id", str(team_id))\
            .single()\
            .execute()
        team_name = team_result.data.get("name", "Unknown Team")
        
        # Get current sprint
        current_sprint = None
        current_sprint_progress = None
        try:
            sprint_result = supabase.table("sprints")\
                .select("id, name, start_date, end_date")\
                .eq("team_id", str(team_id))\
                .lte("start_date", datetime.now().date().isoformat())\
                .gte("end_date", datetime.now().date().isoformat())\
                .single()\
                .execute()
            current_sprint = sprint_result.data
            
            # Calculate sprint progress
            if current_sprint:
                start = datetime.fromisoformat(current_sprint["start_date"]).date()
                end = datetime.fromisoformat(current_sprint["end_date"]).date()
                today = datetime.now().date()
                total_days = (end - start).days
                elapsed_days = (today - start).days
                current_sprint_progress = Decimal(elapsed_days) / Decimal(total_days) * 100 if total_days > 0 else Decimal(0)
        except:
            pass
        
        # Get velocity metrics (last 30 days)
        velocity_response = await get_team_velocity(team_id, 30, ctx)
        
        # Get cycle time metrics
        cycle_time_response = await get_team_cycle_time(team_id, 30, ctx)
        
        # Get workload
        workload_response = await get_team_workload(team_id, ctx)
        
        # Get sprint completion
        sprint_completion_response = await get_sprint_completion_rate(team_id, 5, ctx)
        
        # Get bug metrics (last 30 days)
        from_date = datetime.now().date() - timedelta(days=30)
        bugs_result = supabase.table("team_metrics")\
            .select("bugs_fixed, bugs_created")\
            .eq("team_id", str(team_id))\
            .gte("metric_date", from_date.isoformat())\
            .execute()
        
        bugs_fixed = sum([b.get("bugs_fixed", 0) for b in bugs_result.data])
        bugs_created = sum([b.get("bugs_created", 0) for b in bugs_result.data])
        bug_fix_rate = Decimal(bugs_fixed) / Decimal(bugs_created) * 100 if bugs_created > 0 else None
        
        return {
            "team_id": team_id,
            "team_name": team_name,
            "current_sprint_id": current_sprint.get("id") if current_sprint else None,
            "current_sprint_name": current_sprint.get("name") if current_sprint else None,
            "current_sprint_progress": current_sprint_progress,
            "current_velocity": velocity_response["data_points"][-1]["velocity"] if velocity_response["data_points"] else None,
            "average_velocity_30d": velocity_response["average_velocity"],
            "velocity_trend": velocity_response["trend"],
            "avg_cycle_time_hours": cycle_time_response["average_cycle_time_hours"],
            "cycle_time_trend": cycle_time_response["trend"],
            "total_active_issues": workload_response["total_issues"],
            "total_in_progress": workload_response["total_in_progress"],
            "team_member_count": len(workload_response["members"]),
            "avg_workload_per_member": workload_response["average_workload"],
            "last_sprint_completion_rate": sprint_completion_response["sprints"][0]["completion_rate"] if sprint_completion_response["sprints"] else None,
            "avg_sprint_completion_rate": sprint_completion_response["average_completion_rate"],
            "bugs_fixed_30d": bugs_fixed,
            "bugs_created_30d": bugs_created,
            "bug_fix_rate": bug_fix_rate,
            "calculated_at": datetime.now()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch metrics summary: {str(e)}")


# ============= TEAM CAPACITY ENDPOINTS (Sprint 1) =============

@router.get("/{team_id}/capacity", response_model=TeamCapacityResponse)
async def get_team_capacity(
    team_id: UUID,
    sprint_id: Optional[UUID] = None,
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Get team capacity for a sprint or current sprint"""
    try:
        # If no sprint_id, get current sprint
        target_sprint_id = sprint_id
        sprint_name = None
        
        if not target_sprint_id:
            sprint_result = supabase.table("sprints")\
                .select("id, name")\
                .eq("team_id", str(team_id))\
                .lte("start_date", datetime.now().date().isoformat())\
                .gte("end_date", datetime.now().date().isoformat())\
                .single()\
                .execute()
            if sprint_result.data:
                target_sprint_id = sprint_result.data["id"]
                sprint_name = sprint_result.data["name"]
        else:
            sprint_result = supabase.table("sprints")\
                .select("name")\
                .eq("id", str(sprint_id))\
                .single()\
                .execute()
            sprint_name = sprint_result.data.get("name") if sprint_result.data else None
        
        # Get capacity data
        capacity_result = supabase.table("team_capacity")\
            .select("*")\
            .eq("team_id", str(team_id))
        
        if target_sprint_id:
            capacity_result = capacity_result.eq("sprint_id", str(target_sprint_id))
        
        capacity_result = capacity_result.execute()
        
        members = []
        total_capacity = Decimal(0)
        total_committed = Decimal(0)
        total_completed = Decimal(0)
        
        for cap in capacity_result.data:
            user_id = cap["user_id"]
            
            # Get user details
            user_result = supabase.auth.admin.get_user_by_id(user_id)
            user_email = user_result.user.email if user_result.user else "Unknown"
            user_name = user_result.user.user_metadata.get("full_name", user_email) if user_result.user else user_email
            
            capacity_points = Decimal(str(cap.get("capacity_points", 0)))
            committed_points = Decimal(str(cap.get("committed_points", 0)))
            completed_points = Decimal(str(cap.get("completed_points", 0)))
            
            members.append({
                "user_id": user_id,
                "user_name": user_name,
                "user_email": user_email,
                "capacity_points": capacity_points,
                "committed_points": committed_points,
                "completed_points": completed_points,
                "availability_percent": cap.get("availability_percent", 100),
                "notes": cap.get("notes")
            })
            
            total_capacity += capacity_points
            total_committed += committed_points
            total_completed += completed_points
        
        capacity_utilization = (total_committed / total_capacity * 100) if total_capacity > 0 else Decimal(0)
        
        return {
            "team_id": team_id,
            "sprint_id": target_sprint_id,
            "sprint_name": sprint_name,
            "members": members,
            "total_capacity": total_capacity,
            "total_committed": total_committed,
            "total_completed": total_completed,
            "capacity_utilization": capacity_utilization
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch team capacity: {str(e)}")


@router.post("/{team_id}/capacity")
async def set_team_capacity(
    team_id: UUID,
    body: SetCapacityRequest,
    ctx=Depends(team_role_required("admin", "owner"))
):
    """Set capacity for team members in a sprint"""
    try:
        # Get sprint_id
        target_sprint_id = body.sprint_id
        
        if not target_sprint_id:
            # Get current or next sprint
            sprint_result = supabase.table("sprints")\
                .select("id")\
                .eq("team_id", str(team_id))\
                .gte("end_date", datetime.now().date().isoformat())\
                .order("start_date", desc=False)\
                .limit(1)\
                .execute()
            
            if not sprint_result.data:
                raise HTTPException(status_code=404, detail="No active or upcoming sprint found")
            target_sprint_id = sprint_result.data[0]["id"]
        
        # Upsert capacity for each member
        for member in body.members:
            # Check if record exists
            existing = supabase.table("team_capacity")\
                .select("id")\
                .eq("team_id", str(team_id))\
                .eq("sprint_id", str(target_sprint_id))\
                .eq("user_id", str(member.user_id))\
                .execute()
            
            capacity_data = {
                "team_id": str(team_id),
                "sprint_id": str(target_sprint_id),
                "user_id": str(member.user_id),
                "capacity_points": float(member.capacity_points),
                "availability_percent": member.availability_percent,
                "notes": member.notes
            }
            
            if existing.data:
                # Update
                supabase.table("team_capacity")\
                    .update(capacity_data)\
                    .eq("id", existing.data[0]["id"])\
                    .execute()
            else:
                # Insert
                supabase.table("team_capacity")\
                    .insert(capacity_data)\
                    .execute()
        
        return {"message": "Team capacity updated successfully", "sprint_id": str(target_sprint_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set team capacity: {str(e)}")


# ============= TEAM SETTINGS ENDPOINTS (Sprint 2) =============

@router.get("/{team_id}/settings", response_model=TeamSettingsResponse)
async def get_team_settings(
    team_id: UUID,
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Get team configuration settings"""
    try:
        result = supabase.table("team_settings")\
            .select("*")\
            .eq("team_id", str(team_id))\
            .single()\
            .execute()
        
        if not result.data:
            # Create default settings if not exists
            default_settings = {
                "team_id": str(team_id),
                "timezone": "UTC",
                "working_hours_start": "09:00:00",
                "working_hours_end": "17:00:00",
                "working_days": [1, 2, 3, 4, 5],
                "sprint_length_days": 14,
                "velocity_tracking_enabled": True
            }
            result = supabase.table("team_settings")\
                .insert(default_settings)\
                .execute()
            return result.data[0]
        
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch team settings: {str(e)}")


@router.patch("/{team_id}/settings", response_model=TeamSettingsResponse)
async def update_team_settings(
    team_id: UUID,
    body: UpdateTeamSettingsRequest,
    ctx=Depends(team_role_required("admin", "owner"))
):
    """Update team settings (timezone, working hours, sprint length)"""
    try:
        # Build update dict with only provided fields
        update_data = {}
        if body.timezone is not None:
            update_data["timezone"] = body.timezone
        if body.working_hours_start is not None:
            update_data["working_hours_start"] = body.working_hours_start.isoformat()
        if body.working_hours_end is not None:
            update_data["working_hours_end"] = body.working_hours_end.isoformat()
        if body.working_days is not None:
            update_data["working_days"] = body.working_days
        if body.sprint_length_days is not None:
            update_data["sprint_length_days"] = body.sprint_length_days
        if body.velocity_tracking_enabled is not None:
            update_data["velocity_tracking_enabled"] = body.velocity_tracking_enabled
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        result = supabase.table("team_settings")\
            .update(update_data)\
            .eq("team_id", str(team_id))\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Team settings not found")
        
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update team settings: {str(e)}")


# ============= TEAM GOALS/OKRs ENDPOINTS (Sprint 2) =============

@router.get("/{team_id}/goals")
async def list_team_goals(
    team_id: UUID,
    quarter: Optional[str] = None,
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """List team goals/OKRs"""
    try:
        query = supabase.table("team_goals")\
            .select("*")\
            .eq("team_id", str(team_id))
        
        if quarter:
            query = query.eq("quarter", quarter)
        
        result = query.order("created_at", desc=True).execute()
        
        # Enrich with owner names and calculate progress
        goals = []
        for goal in result.data:
            owner_name = None
            if goal.get("owner_user_id"):
                try:
                    user_result = supabase.auth.admin.get_user_by_id(goal["owner_user_id"])
                    if user_result.user:
                        owner_name = user_result.user.user_metadata.get("full_name") or user_result.user.email
                except:
                    pass
            
            # Calculate progress percentage
            progress_percentage = None
            if goal.get("target_value") and float(goal["target_value"]) > 0:
                current = float(goal.get("current_value", 0))
                target = float(goal["target_value"])
                progress_percentage = min(100, (current / target) * 100)
            
            goals.append({
                **goal,
                "owner_name": owner_name,
                "progress_percentage": progress_percentage
            })
        
        return goals
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list team goals: {str(e)}")


@router.post("/{team_id}/goals", response_model=TeamGoalResponse)
async def create_team_goal(
    team_id: UUID,
    body: CreateGoalRequest,
    ctx=Depends(team_role_required("admin", "owner")),
    current_user: UserModel = Depends(get_current_user)
):
    """Create new team goal/OKR"""
    try:
        goal_data = {
            "team_id": str(team_id),
            "title": body.title,
            "description": body.description,
            "goal_type": body.goal_type,
            "target_value": float(body.target_value) if body.target_value else None,
            "current_value": float(body.current_value),
            "unit": body.unit,
            "quarter": body.quarter,
            "owner_user_id": str(body.owner_user_id) if body.owner_user_id else None,
            "due_date": body.due_date.isoformat() if body.due_date else None,
            "created_by": current_user.id,
            "status": "active"
        }
        
        result = supabase.table("team_goals")\
            .insert(goal_data)\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create goal")
        
        goal = result.data[0]
        
        # Enrich with owner name
        owner_name = None
        if goal.get("owner_user_id"):
            try:
                user_result = supabase.auth.admin.get_user_by_id(goal["owner_user_id"])
                if user_result.user:
                    owner_name = user_result.user.user_metadata.get("full_name") or user_result.user.email
            except:
                pass
        
        progress_percentage = None
        if goal.get("target_value") and float(goal["target_value"]) > 0:
            progress_percentage = (float(goal["current_value"]) / float(goal["target_value"])) * 100
        
        return {**goal, "owner_name": owner_name, "progress_percentage": progress_percentage}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create team goal: {str(e)}")


@router.patch("/{team_id}/goals/{goal_id}", response_model=TeamGoalResponse)
async def update_team_goal(
    team_id: UUID,
    goal_id: UUID,
    body: UpdateGoalRequest,
    ctx=Depends(team_role_required("admin", "owner"))
):
    """Update team goal progress"""
    try:
        # Build update dict
        update_data = {}
        if body.title is not None:
            update_data["title"] = body.title
        if body.description is not None:
            update_data["description"] = body.description
        if body.target_value is not None:
            update_data["target_value"] = float(body.target_value)
        if body.current_value is not None:
            update_data["current_value"] = float(body.current_value)
        if body.unit is not None:
            update_data["unit"] = body.unit
        if body.quarter is not None:
            update_data["quarter"] = body.quarter
        if body.status is not None:
            update_data["status"] = body.status
        if body.owner_user_id is not None:
            update_data["owner_user_id"] = str(body.owner_user_id)
        if body.due_date is not None:
            update_data["due_date"] = body.due_date.isoformat()
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        result = supabase.table("team_goals")\
            .update(update_data)\
            .eq("id", str(goal_id))\
            .eq("team_id", str(team_id))\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Goal not found")
        
        goal = result.data[0]
        
        # Enrich with owner name
        owner_name = None
        if goal.get("owner_user_id"):
            try:
                user_result = supabase.auth.admin.get_user_by_id(goal["owner_user_id"])
                if user_result.user:
                    owner_name = user_result.user.user_metadata.get("full_name") or user_result.user.email
            except:
                pass
        
        progress_percentage = None
        if goal.get("target_value") and float(goal["target_value"]) > 0:
            progress_percentage = (float(goal["current_value"]) / float(goal["target_value"])) * 100
        
        return {**goal, "owner_name": owner_name, "progress_percentage": progress_percentage}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update team goal: {str(e)}")


@router.delete("/{team_id}/goals/{goal_id}")
async def delete_team_goal(
    team_id: UUID,
    goal_id: UUID,
    ctx=Depends(team_role_required("admin", "owner"))
):
    """Delete a team goal"""
    try:
        result = supabase.table("team_goals")\
            .delete()\
            .eq("id", str(goal_id))\
            .eq("team_id", str(team_id))\
            .execute()
        
        return {"message": "Goal deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete goal: {str(e)}")


# ============= NOTIFICATION SETTINGS ENDPOINTS (Sprint 2) =============

@router.get("/{team_id}/notifications/settings", response_model=NotificationSettingsResponse)
async def get_notification_settings(
    team_id: UUID,
    current_user: UserModel = Depends(get_current_user),
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Get user's notification preferences for this team"""
    try:
        result = supabase.table("team_notification_settings")\
            .select("*")\
            .eq("team_id", str(team_id))\
            .eq("user_id", current_user.id)\
            .single()\
            .execute()
        
        if not result.data:
            # Create default settings
            default_settings = {
                "team_id": str(team_id),
                "user_id": current_user.id,
                "email_daily_digest": True,
                "email_sprint_summary": True,
                "email_mentions": True,
                "email_assignments": True,
                "slack_notifications": False,
                "slack_webhook_url": None
            }
            result = supabase.table("team_notification_settings")\
                .insert(default_settings)\
                .execute()
            return result.data[0]
        
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch notification settings: {str(e)}")


@router.patch("/{team_id}/notifications/settings", response_model=NotificationSettingsResponse)
async def update_notification_settings(
    team_id: UUID,
    body: UpdateNotificationSettingsRequest,
    current_user: UserModel = Depends(get_current_user),
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Update notification preferences"""
    try:
        update_data = {}
        if body.email_daily_digest is not None:
            update_data["email_daily_digest"] = body.email_daily_digest
        if body.email_sprint_summary is not None:
            update_data["email_sprint_summary"] = body.email_sprint_summary
        if body.email_mentions is not None:
            update_data["email_mentions"] = body.email_mentions
        if body.email_assignments is not None:
            update_data["email_assignments"] = body.email_assignments
        if body.slack_notifications is not None:
            update_data["slack_notifications"] = body.slack_notifications
        if body.slack_webhook_url is not None:
            update_data["slack_webhook_url"] = body.slack_webhook_url
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        result = supabase.table("team_notification_settings")\
            .update(update_data)\
            .eq("team_id", str(team_id))\
            .eq("user_id", current_user.id)\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Notification settings not found")
        
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update notification settings: {str(e)}")


# ============= DEFAULT ASSIGNEES ENDPOINTS (Sprint 2) =============

@router.get("/{team_id}/default-assignees")
async def get_default_assignees(
    team_id: UUID,
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Get default assignee rules"""
    try:
        result = supabase.table("team_default_assignees")\
            .select("*")\
            .eq("team_id", str(team_id))\
            .execute()
        
        # Enrich with user details
        assignees = []
        for rule in result.data:
            try:
                user_result = supabase.auth.admin.get_user_by_id(rule["assignee_user_id"])
                user_email = user_result.user.email if user_result.user else "Unknown"
                user_name = user_result.user.user_metadata.get("full_name", user_email) if user_result.user else user_email
                
                assignees.append({
                    **rule,
                    "assignee_name": user_name,
                    "assignee_email": user_email
                })
            except:
                assignees.append({
                    **rule,
                    "assignee_name": "Unknown",
                    "assignee_email": "Unknown"
                })
        
        return assignees
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch default assignees: {str(e)}")


@router.post("/{team_id}/default-assignees", response_model=DefaultAssigneeResponse)
async def set_default_assignee(
    team_id: UUID,
    body: SetDefaultAssigneeRequest,
    ctx=Depends(team_role_required("admin", "owner"))
):
    """Set default assignee for issue type/priority combo"""
    try:
        # Check if rule already exists
        existing = supabase.table("team_default_assignees")\
            .select("id")\
            .eq("team_id", str(team_id))
        
        if body.issue_type:
            existing = existing.eq("issue_type", body.issue_type)
        else:
            existing = existing.is_("issue_type", "null")
        
        if body.priority:
            existing = existing.eq("priority", body.priority)
        else:
            existing = existing.is_("priority", "null")
        
        existing = existing.execute()
        
        rule_data = {
            "team_id": str(team_id),
            "issue_type": body.issue_type,
            "priority": body.priority,
            "assignee_user_id": str(body.assignee_user_id)
        }
        
        if existing.data:
            # Update existing
            result = supabase.table("team_default_assignees")\
                .update(rule_data)\
                .eq("id", existing.data[0]["id"])\
                .execute()
        else:
            # Insert new
            result = supabase.table("team_default_assignees")\
                .insert(rule_data)\
                .execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to set default assignee")
        
        rule = result.data[0]
        
        # Get user details
        user_result = supabase.auth.admin.get_user_by_id(rule["assignee_user_id"])
        user_email = user_result.user.email if user_result.user else "Unknown"
        user_name = user_result.user.user_metadata.get("full_name", user_email) if user_result.user else user_email
        
        return {
            **rule,
            "assignee_name": user_name,
            "assignee_email": user_email
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set default assignee: {str(e)}")


@router.delete("/{team_id}/default-assignees")
async def delete_default_assignee(
    team_id: UUID,
    issue_type: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    ctx=Depends(team_role_required("admin", "owner"))
):
    """Delete a default assignee rule"""
    try:
        query = supabase.table("team_default_assignees")\
            .delete()\
            .eq("team_id", str(team_id))
        
        if issue_type:
            query = query.eq("issue_type", issue_type)
        else:
            query = query.is_("issue_type", "null")
        
        if priority:
            query = query.eq("priority", priority)
        else:
            query = query.is_("priority", "null")
        
        query.execute()
        
        return {"message": "Default assignee rule deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete default assignee: {str(e)}")


# ============= TEAM LABELS ENDPOINTS (Sprint 2) =============

@router.get("/{team_id}/labels")
async def list_team_labels(
    team_id: UUID,
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """List team labels"""
    try:
        result = supabase.table("team_labels")\
            .select("*")\
            .eq("team_id", str(team_id))\
            .order("created_at", desc=True)\
            .execute()
        
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list team labels: {str(e)}")


@router.post("/{team_id}/labels", response_model=TeamLabelResponse)
async def create_team_label(
    team_id: UUID,
    body: CreateLabelRequest,
    ctx=Depends(team_role_required("editor", "admin", "owner"))
):
    """Create a new team label"""
    try:
        label_data = {
            "team_id": str(team_id),
            "name": body.name,
            "color": body.color,
            "description": body.description
        }
        
        result = supabase.table("team_labels")\
            .insert(label_data)\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create label")
        
        return result.data[0]
    except Exception as e:
        if "duplicate key" in str(e).lower():
            raise HTTPException(status_code=409, detail="Label with this name already exists")
        raise HTTPException(status_code=500, detail=f"Failed to create team label: {str(e)}")


@router.patch("/{team_id}/labels/{label_id}", response_model=TeamLabelResponse)
async def update_team_label(
    team_id: UUID,
    label_id: UUID,
    body: UpdateLabelRequest,
    ctx=Depends(team_role_required("editor", "admin", "owner"))
):
    """Update a team label"""
    try:
        update_data = {}
        if body.name is not None:
            update_data["name"] = body.name
        if body.color is not None:
            update_data["color"] = body.color
        if body.description is not None:
            update_data["description"] = body.description
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        result = supabase.table("team_labels")\
            .update(update_data)\
            .eq("id", str(label_id))\
            .eq("team_id", str(team_id))\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Label not found")
        
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        if "duplicate key" in str(e).lower():
            raise HTTPException(status_code=409, detail="Label with this name already exists")
        raise HTTPException(status_code=500, detail=f"Failed to update team label: {str(e)}")


@router.delete("/{team_id}/labels/{label_id}")
async def delete_team_label(
    team_id: UUID,
    label_id: UUID,
    ctx=Depends(team_role_required("editor", "admin", "owner"))
):
    """Delete a team label"""
    try:
        result = supabase.table("team_labels")\
            .delete()\
            .eq("id", str(label_id))\
            .eq("team_id", str(team_id))\
            .execute()
        
        return {"message": "Label deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete label: {str(e)}")


# =====================================================
# Sprint 3: Collaboration & Resources Endpoints
# =====================================================

# -------------------- Resource Categories --------------------

@router.get("/{team_id}/categories", response_model=List[ResourceCategoryResponse])
async def list_resource_categories(
    team_id: UUID,
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """List all resource categories for a team"""
    try:
        result = supabase.table("resource_categories")\
            .select("*")\
            .eq("team_id", str(team_id))\
            .order("display_order")\
            .order("name")\
            .execute()
        
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch categories: {str(e)}")


@router.post("/{team_id}/categories", response_model=ResourceCategoryResponse)
async def create_resource_category(
    team_id: UUID,
    category: CreateCategoryRequest,
    ctx=Depends(team_role_required("admin", "owner"))
):
    """Create a new resource category"""
    try:
        new_category = {
            "team_id": str(team_id),
            "name": category.name,
            "description": category.description,
            "color": category.color,
            "icon": category.icon,
            "parent_category_id": str(category.parent_category_id) if category.parent_category_id else None,
            "display_order": category.display_order,
        }
        
        result = supabase.table("resource_categories")\
            .insert(new_category)\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0]
        raise HTTPException(status_code=500, detail="Failed to create category")
    except Exception as e:
        if "unique_category_name_per_team" in str(e):
            raise HTTPException(status_code=409, detail="Category with this name already exists")
        raise HTTPException(status_code=500, detail=f"Failed to create category: {str(e)}")


@router.patch("/{team_id}/categories/{category_id}", response_model=ResourceCategoryResponse)
async def update_resource_category(
    team_id: UUID,
    category_id: UUID,
    updates: UpdateCategoryRequest,
    ctx=Depends(team_role_required("admin", "owner"))
):
    """Update a resource category"""
    try:
        update_data = {k: v for k, v in updates.dict(exclude_unset=True).items()}
        if "parent_category_id" in update_data and update_data["parent_category_id"]:
            update_data["parent_category_id"] = str(update_data["parent_category_id"])
        
        result = supabase.table("resource_categories")\
            .update(update_data)\
            .eq("id", str(category_id))\
            .eq("team_id", str(team_id))\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0]
        raise HTTPException(status_code=404, detail="Category not found")
    except Exception as e:
        if "unique_category_name_per_team" in str(e):
            raise HTTPException(status_code=409, detail="Category with this name already exists")
        raise HTTPException(status_code=500, detail=f"Failed to update category: {str(e)}")


@router.delete("/{team_id}/categories/{category_id}")
async def delete_resource_category(
    team_id: UUID,
    category_id: UUID,
    ctx=Depends(team_role_required("admin", "owner"))
):
    """Delete a resource category"""
    try:
        result = supabase.table("resource_categories")\
            .delete()\
            .eq("id", str(category_id))\
            .eq("team_id", str(team_id))\
            .execute()
        
        return {"message": "Category deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete category: {str(e)}")


# -------------------- Team Resources --------------------

@router.get("/{team_id}/resources", response_model=List[ResourceResponse])
async def list_team_resources(
    team_id: UUID,
    category_id: Optional[UUID] = None,
    resource_type: Optional[str] = None,
    tags: Optional[str] = None,
    include_archived: bool = False,
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """
    List team resources with optional filtering
    
    - **category_id**: Filter by category
    - **resource_type**: Filter by type (link, document, file, etc.)
    - **tags**: Comma-separated tag list
    - **include_archived**: Include archived resources
    """
    try:
        query = supabase.table("team_resources")\
            .select("*")\
            .eq("team_id", str(team_id))
        
        if not include_archived:
            query = query.eq("is_archived", False)
        
        if category_id:
            query = query.eq("category_id", str(category_id))
        
        if resource_type:
            query = query.eq("resource_type", resource_type)
        
        if tags:
            tag_list = [t.strip() for t in tags.split(",")]
            query = query.contains("tags", tag_list)
        
        result = query.order("is_pinned", desc=True)\
            .order("created_at", desc=True)\
            .execute()
        
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch resources: {str(e)}")


@router.post("/{team_id}/resources", response_model=ResourceResponse)
async def create_team_resource(
    team_id: UUID,
    resource: CreateResourceRequest,
    user: UserModel = Depends(get_current_user),
    ctx=Depends(team_role_required("editor", "admin", "owner"))
):
    """Create a new team resource"""
    try:
        new_resource = {
            "team_id": str(team_id),
            "title": resource.title,
            "description": resource.description,
            "resource_type": resource.resource_type,
            "url": resource.url,
            "content": resource.content,
            "category_id": str(resource.category_id) if resource.category_id else None,
            "tags": resource.tags,
            "is_pinned": resource.is_pinned,
            "file_size_bytes": resource.file_size_bytes,
            "mime_type": resource.mime_type,
            "created_by": str(user.id),
            "created_by_name": user.email,
            "created_by_email": user.email,
        }
        
        result = supabase.table("team_resources")\
            .insert(new_resource)\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0]
        raise HTTPException(status_code=500, detail="Failed to create resource")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create resource: {str(e)}")


@router.patch("/{team_id}/resources/{resource_id}", response_model=ResourceResponse)
async def update_team_resource(
    team_id: UUID,
    resource_id: UUID,
    updates: UpdateResourceRequest,
    user: UserModel = Depends(get_current_user),
    ctx=Depends(team_role_required("editor", "admin", "owner"))
):
    """Update a team resource"""
    try:
        update_data = {k: v for k, v in updates.dict(exclude_unset=True).items()}
        if "category_id" in update_data and update_data["category_id"]:
            update_data["category_id"] = str(update_data["category_id"])
        
        update_data["updated_by"] = str(user.id)
        
        result = supabase.table("team_resources")\
            .update(update_data)\
            .eq("id", str(resource_id))\
            .eq("team_id", str(team_id))\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0]
        raise HTTPException(status_code=404, detail="Resource not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update resource: {str(e)}")


@router.delete("/{team_id}/resources/{resource_id}")
async def delete_team_resource(
    team_id: UUID,
    resource_id: UUID,
    ctx=Depends(team_role_required("editor", "admin", "owner"))
):
    """Delete a team resource"""
    try:
        result = supabase.table("team_resources")\
            .delete()\
            .eq("id", str(resource_id))\
            .eq("team_id", str(team_id))\
            .execute()
        
        return {"message": "Resource deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete resource: {str(e)}")


@router.post("/{team_id}/resources/{resource_id}/view")
async def track_resource_view(
    team_id: UUID,
    resource_id: UUID,
    user: UserModel = Depends(get_current_user),
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Track a resource view (increment view count)"""
    try:
        # Increment view count
        result = supabase.rpc("increment_resource_view_count", {
            "resource_id": str(resource_id)
        }).execute()
        
        # Log access (optional)
        try:
            supabase.table("resource_access_log").insert({
                "resource_id": str(resource_id),
                "user_id": str(user.id),
                "access_source": "web"
            }).execute()
        except:
            pass  # Don't fail if logging fails
        
        return {"message": "View tracked successfully"}
    except Exception as e:
        # Fallback to manual increment if RPC doesn't exist
        try:
            resource = supabase.table("team_resources")\
                .select("view_count")\
                .eq("id", str(resource_id))\
                .single()\
                .execute()
            
            new_count = (resource.data.get("view_count", 0) or 0) + 1
            supabase.table("team_resources")\
                .update({"view_count": new_count, "last_viewed_at": "now()"})\
                .eq("id", str(resource_id))\
                .execute()
            
            return {"message": "View tracked successfully"}
        except Exception as fallback_e:
            raise HTTPException(status_code=500, detail=f"Failed to track view: {str(fallback_e)}")


# -------------------- Team Chat Messages --------------------

@router.get("/{team_id}/chat", response_model=List[ChatMessageResponse])
async def list_chat_messages(
    team_id: UUID,
    limit: int = 50,
    before_id: Optional[UUID] = None,
    parent_message_id: Optional[UUID] = None,
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """
    List team chat messages with pagination
    
    - **limit**: Number of messages to return (default 50, max 100)
    - **before_id**: Get messages before this message ID (for pagination)
    - **parent_message_id**: Get thread replies for a specific message
    """
    try:
        limit = min(limit, 100)  # Cap at 100
        
        query = supabase.table("team_chat_messages")\
            .select("*")\
            .eq("team_id", str(team_id))\
            .eq("is_deleted", False)
        
        if parent_message_id:
            query = query.eq("parent_message_id", str(parent_message_id))
        else:
            query = query.is_("parent_message_id", "null")  # Only top-level messages
        
        if before_id:
            # Get messages created before the specified message
            before_msg = supabase.table("team_chat_messages")\
                .select("created_at")\
                .eq("id", str(before_id))\
                .single()\
                .execute()
            
            if before_msg.data:
                query = query.lt("created_at", before_msg.data["created_at"])
        
        result = query.order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        
        # Reverse to get chronological order
        messages = list(reversed(result.data)) if result.data else []
        return messages
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch messages: {str(e)}")


@router.post("/{team_id}/chat", response_model=ChatMessageResponse)
async def create_chat_message(
    team_id: UUID,
    message: CreateChatMessageRequest,
    user: UserModel = Depends(get_current_user),
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Send a new chat message"""
    try:
        new_message = {
            "team_id": str(team_id),
            "message": message.message,
            "message_type": message.message_type,
            "parent_message_id": str(message.parent_message_id) if message.parent_message_id else None,
            "mentioned_user_ids": [str(uid) for uid in message.mentioned_user_ids],
            "user_id": str(user.id),
            "user_name": user.email,
            "user_email": user.email,
        }
        
        result = supabase.table("team_chat_messages")\
            .insert(new_message)\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0]
        raise HTTPException(status_code=500, detail="Failed to create message")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create message: {str(e)}")


@router.patch("/{team_id}/chat/{message_id}", response_model=ChatMessageResponse)
async def update_chat_message(
    team_id: UUID,
    message_id: UUID,
    updates: UpdateChatMessageRequest,
    user: UserModel = Depends(get_current_user),
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Update a chat message (edit or delete)"""
    try:
        # Verify ownership unless admin/owner
        message_data = supabase.table("team_chat_messages")\
            .select("user_id")\
            .eq("id", str(message_id))\
            .single()\
            .execute()
        
        if message_data.data["user_id"] != str(user.id):
            # Check if user is admin/owner
            member = supabase.table("team_members")\
                .select("role")\
                .eq("team_id", str(team_id))\
                .eq("user_id", str(user.id))\
                .single()\
                .execute()
            
            if member.data["role"] not in ["admin", "owner"]:
                raise HTTPException(status_code=403, detail="Can only edit your own messages")
        
        update_data = {k: v for k, v in updates.dict(exclude_unset=True).items()}
        
        if "message" in update_data:
            update_data["is_edited"] = True
            update_data["edited_at"] = "now()"
        
        if "is_deleted" in update_data and update_data["is_deleted"]:
            update_data["deleted_at"] = "now()"
        
        result = supabase.table("team_chat_messages")\
            .update(update_data)\
            .eq("id", str(message_id))\
            .eq("team_id", str(team_id))\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0]
        raise HTTPException(status_code=404, detail="Message not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update message: {str(e)}")


@router.delete("/{team_id}/chat/{message_id}")
async def delete_chat_message(
    team_id: UUID,
    message_id: UUID,
    user: UserModel = Depends(get_current_user),
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Delete a chat message (soft delete)"""
    try:
        # Verify ownership unless admin/owner
        message_data = supabase.table("team_chat_messages")\
            .select("user_id")\
            .eq("id", str(message_id))\
            .single()\
            .execute()
        
        if message_data.data["user_id"] != str(user.id):
            # Check if user is admin/owner
            member = supabase.table("team_members")\
                .select("role")\
                .eq("team_id", str(team_id))\
                .eq("user_id", str(user.id))\
                .single()\
                .execute()
            
            if member.data["role"] not in ["admin", "owner"]:
                raise HTTPException(status_code=403, detail="Can only delete your own messages")
        
        result = supabase.table("team_chat_messages")\
            .update({"is_deleted": True, "deleted_at": "now()"})\
            .eq("id", str(message_id))\
            .eq("team_id", str(team_id))\
            .execute()
        
        return {"message": "Message deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete message: {str(e)}")


@router.post("/{team_id}/chat/{message_id}/react")
async def add_message_reaction(
    team_id: UUID,
    message_id: UUID,
    reaction: AddReactionRequest,
    user: UserModel = Depends(get_current_user),
    ctx=Depends(team_role_required("viewer", "editor", "admin", "owner"))
):
    """Add or remove a reaction to a message"""
    try:
        # Get current reactions
        message = supabase.table("team_chat_messages")\
            .select("reactions")\
            .eq("id", str(message_id))\
            .eq("team_id", str(team_id))\
            .single()\
            .execute()
        
        reactions = message.data.get("reactions", {}) or {}
        emoji = reaction.emoji
        user_id = str(user.id)
        
        # Toggle reaction
        if emoji in reactions:
            if user_id in reactions[emoji]:
                reactions[emoji].remove(user_id)
                if not reactions[emoji]:
                    del reactions[emoji]
            else:
                reactions[emoji].append(user_id)
        else:
            reactions[emoji] = [user_id]
        
        # Update message
        result = supabase.table("team_chat_messages")\
            .update({"reactions": reactions})\
            .eq("id", str(message_id))\
            .execute()
        
        return {"message": "Reaction updated", "reactions": reactions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update reaction: {str(e)}")

