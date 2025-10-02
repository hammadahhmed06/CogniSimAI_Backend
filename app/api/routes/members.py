from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.dependencies import (
    supabase,
    get_current_user,
    UserModel,
    get_workspace_context,
    WorkspaceContext,
    team_role_required,
)

router = APIRouter(prefix="/api/members", tags=["members"])


class MemberProfile(BaseModel):
    user_id: UUID
    email: Optional[str] = None
    full_name: Optional[str] = None
    title: Optional[str] = None
    bio: Optional[str] = None
    timezone: Optional[str] = None
    location: Optional[str] = None
    avatar_url: Optional[str] = None
    capacity_hours_week: Optional[int] = None
    availability_status: Optional[str] = None
    availability_until: Optional[str] = None
    skills: List[str] = []


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    title: Optional[str] = None
    bio: Optional[str] = None
    timezone: Optional[str] = None
    location: Optional[str] = None
    avatar_url: Optional[str] = None
    capacity_hours_week: Optional[int] = None
    availability_status: Optional[str] = None
    availability_until: Optional[str] = None


class UpsertSkillsRequest(BaseModel):
    skills: List[dict]  # [{ name: str, level?: int, years_experience?: int }]


def _load_user_profiles(user_ids: List[str]) -> dict[str, dict]:
    """Best-effort fetch for user profile metadata supporting legacy schemas."""
    if not user_ids:
        return {}
    identifiers = ["user_id", "id", "profile_id"]
    normalized_ids = [str(u) for u in user_ids]
    for identifier in identifiers:
        try:
            res = (
                supabase
                .table("user_profiles")
                .select("*")
                .in_(identifier, normalized_ids)
                .execute()
            )
        except Exception as exc:
            # Skip identifiers that don't exist in this schema
            if f"user_profiles.{identifier}" in str(exc):
                continue
            # Any other failure: try the next identifier but remember to avoid crashing
            continue
        rows = getattr(res, "data", []) or []
        if not rows:
            return {}
        out: dict[str, dict] = {}
        for row in rows:
            key = row.get(identifier) or row.get("user_id") or row.get("id") or row.get("profile_id")
            if key:
                out[str(key)] = row
        return out
    return {}


def _workspace_user_ids(workspace_id: UUID) -> List[str]:
    # Robust approach: fetch team ids for the workspace, then collect member user_ids
    teams_res = (
        supabase
        .table("teams")
        .select("id")
        .eq("workspace_id", str(workspace_id))
        .execute()
    )
    team_ids = [str(t.get("id")) for t in (getattr(teams_res, "data", []) or []) if t.get("id")]
    if not team_ids:
        return []
    members_res = (
        supabase
        .table("team_members")
        .select("user_id,team_id")
        .in_("team_id", team_ids)
        .execute()
    )
    ids: set[str] = set()
    for r in (getattr(members_res, "data", []) or []):
        uid = r.get("user_id")
        if uid:
            try:
                ids.add(str(uid))
            except Exception:
                continue
    return list(ids)


@router.get("")
@router.get("/")
async def list_members(
    q: Optional[str] = Query(None),
    skill: Optional[str] = Query(None),
    team_id: Optional[UUID] = Query(None),
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort: Optional[str] = Query("name"),  # name|availability
    current_user: UserModel = Depends(get_current_user),
    wctx: WorkspaceContext = Depends(get_workspace_context),
):
    user_ids = _workspace_user_ids(wctx.workspace_id)
    if not user_ids:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}
    # Fetch basic identities (assuming auth schema mirrors users)
    # If you store users elsewhere, adjust this section accordingly.
    profiles_map = _load_user_profiles(user_ids)

    skills_rows = (
        supabase.table("user_skills")
        .select("user_id,skill:skills(name)")
        .in_("user_id", [str(u) for u in user_ids])
        .execute()
    )
    skills_map: dict[str, List[str]] = {}
    for r in (getattr(skills_rows, "data", []) or []):
        uid = r["user_id"]
        nm = (r.get("skill") or {}).get("name")
        if isinstance(nm, str) and nm:
            skills_map.setdefault(uid, []).append(nm)

    # Optional filter by team
    team_filter_ids = None
    if team_id:
        team_members = supabase.table("team_members").select("user_id").eq("team_id", str(team_id)).execute()
        team_filter_ids = {str(m.get("user_id")) for m in (getattr(team_members, "data", []) or []) if m.get("user_id")}

    result: List[MemberProfile] = []
    for uid in user_ids:
        if team_filter_ids is not None and uid not in team_filter_ids:
            continue
        prof = profiles_map.get(uid, {})
        sp = skills_map.get(uid, [])
        if skill and (skill not in sp):
            continue
        # Basic free-text query on name/title
        if q:
            t = (prof.get("full_name") or "") + " " + (prof.get("title") or "")
            if q.lower() not in t.lower():
                continue
        # parse capacity
        _cap_val = prof.get("capacity_hours_week")
        cap_val: Optional[int] = None
        try:
            if _cap_val is not None:
                cap_val = int(_cap_val)
        except Exception:
            cap_val = None
        result.append(MemberProfile(
            user_id=UUID(uid),
            full_name=prof.get("full_name"),
            title=prof.get("title"),
            bio=prof.get("bio"),
            timezone=prof.get("timezone"),
            location=prof.get("location"),
            avatar_url=prof.get("avatar_url"),
            capacity_hours_week=cap_val,
            availability_status=prof.get("availability_status"),
            availability_until=prof.get("availability_until"),
            skills=[s for s in sp if s],
        ))
    # Sorting
    sort_key = (sort or "name").lower()
    if sort_key not in ("name", "availability"):
        sort_key = "name"
    if sort_key == "availability":
        result.sort(key=lambda m: (m.availability_status or "zzz", (m.full_name or "").lower()))
    else:
        result.sort(key=lambda m: (m.full_name or "").lower())
    total = len(result)
    window = result[offset: offset + limit]
    return {"items": [m.model_dump() for m in window], "total": total, "limit": limit, "offset": offset}


@router.get("/{user_id}")
async def get_member(user_id: UUID, current_user: UserModel = Depends(get_current_user), wctx: WorkspaceContext = Depends(get_workspace_context)):
    # Ensure user is in workspace
    if str(user_id) not in _workspace_user_ids(wctx.workspace_id):
        raise HTTPException(status_code=404, detail="User not in this workspace")
    prof_res = (
        supabase.table("user_profiles")
        .select("user_id,full_name,title,bio,timezone,location,avatar_url,capacity_hours_week,availability_status,availability_until")
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    prof = getattr(prof_res, "data", None) or {"user_id": str(user_id)}
    skills_rows = supabase.table("user_skills").select("skill:skills(name)").eq("user_id", str(user_id)).execute()
    skills = []
    for r in (getattr(skills_rows, "data", []) or []):
        nm = (r.get("skill") or {}).get("name")
        if isinstance(nm, str) and nm:
            skills.append(nm)
    # parse capacity
    _cap = prof.get("capacity_hours_week")
    cap_int: Optional[int] = None
    try:
        if _cap is not None:
            cap_int = int(_cap)
    except Exception:
        cap_int = None
    return MemberProfile(
        user_id=user_id,
        full_name=prof.get("full_name"),
        title=prof.get("title"),
        bio=prof.get("bio"),
        timezone=prof.get("timezone"),
        location=prof.get("location"),
        avatar_url=prof.get("avatar_url"),
        capacity_hours_week=cap_int,
        availability_status=prof.get("availability_status"),
        availability_until=prof.get("availability_until"),
        skills=skills,
    )


@router.patch("/{user_id}/profile")
async def update_profile(user_id: UUID, body: UpdateProfileRequest, current_user: UserModel = Depends(get_current_user)):
    if str(current_user.id) != str(user_id):
        # For Phase 1, only allow self-update; later allow admin/PM
        raise HTTPException(status_code=403, detail="Forbidden")
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not data:
        return {"success": True}
    # Upsert profile
    existing = supabase.table("user_profiles").select("user_id").eq("user_id", str(user_id)).maybe_single().execute()
    if getattr(existing, "data", None):
        supabase.table("user_profiles").update(data).eq("user_id", str(user_id)).execute()
    else:
        supabase.table("user_profiles").insert({"user_id": str(user_id), **data}).execute()
    return {"success": True}


@router.put("/{user_id}/skills")
async def upsert_skills(user_id: UUID, body: UpsertSkillsRequest, current_user: UserModel = Depends(get_current_user)):
    if str(current_user.id) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    # Ensure skills exist, then upsert relations
    names = [s.get("name") for s in body.skills if s.get("name")]
    for name in names:
        supabase.table("skills").upsert({"name": name}).execute()
    # Fetch skill ids
    rows = supabase.table("skills").select("id,name").in_("name", names).execute()
    id_by_name = {r["name"]: r["id"] for r in (getattr(rows, "data", []) or [])}
    # Replace user skills
    supabase.table("user_skills").delete().eq("user_id", str(user_id)).execute()
    for s in body.skills:
        nm = s.get("name")
        if not nm or nm not in id_by_name:
            continue
        payload = {
            "user_id": str(user_id),
            "skill_id": id_by_name[nm],
        }
        if s.get("level") is not None:
            payload["level"] = s["level"]
        if s.get("years_experience") is not None:
            payload["years_experience"] = s["years_experience"]
        supabase.table("user_skills").upsert(payload).execute()
    return {"success": True}
