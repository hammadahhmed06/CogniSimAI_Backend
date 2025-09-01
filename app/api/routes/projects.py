from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from enum import Enum
from uuid import UUID, uuid4
from app.core.dependencies import supabase, get_current_user, UserModel
try:
    # postgrest APIError used for graceful fallback if legacy schema lacks column
    from postgrest.exceptions import APIError  # type: ignore
except Exception:  # pragma: no cover - safety import
    APIError = Exception  # type: ignore

router = APIRouter(prefix="/api/projects", tags=["Projects"], dependencies=[Depends(get_current_user)])

class ProjectType(str, Enum):
    scrum = "scrum"
    kanban = "kanban"

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    key: str = Field(..., min_length=2, max_length=12, pattern=r"^[A-Za-z0-9_-]+$")
    type: ProjectType = ProjectType.scrum

class Project(BaseModel):
    id: UUID
    name: str
    key: str
    type: ProjectType
    description: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    archived_at: Optional[str] = None
    slug: Optional[str] = None

class ProjectDetail(Project):
    items_count: Optional[int] = 0
    active_sprint_id: Optional[UUID] = None

class ProjectActivity(BaseModel):
    id: UUID
    project_id: UUID
    actor_user_id: UUID
    action: str
    meta: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[str] = Field(None, pattern=r"^(active|archived)$")

class ItemCreate(BaseModel):
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    status: str = Field("todo")
    priority: Optional[str] = None
    type: Optional[str] = None

class ItemUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    sprint_id: Optional[UUID] = Field(default=None)

class Item(BaseModel):
    id: UUID
    project_id: UUID
    item_key: str
    title: str
    status: str
    priority: Optional[str]
    sprint_id: Optional[UUID] = None

class SprintCreate(BaseModel):
    name: str = Field(..., min_length=1)
    goal: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None

class Sprint(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    state: str
    goal: Optional[str]
    start_date: Optional[str]
    end_date: Optional[str]

class AssignItems(BaseModel):
    item_ids: List[UUID]

def _normalize_key(key: str) -> str:
    return key.upper().replace(" ", "_")[:12]

def _item_from_row(row: dict) -> Item:
    return Item(
        id=row["id"],
        project_id=row["project_id"],
        item_key=row["item_key"],
        title=row["title"],
        status=row["status"],
        priority=row.get("priority"),
        sprint_id=row.get("sprint_id"),
    )

def _sprint_from_row(row: dict) -> Sprint:
    return Sprint(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        state=row["state"],
        goal=row.get("goal"),
        start_date=row.get("start_date"),
        end_date=row.get("end_date"),
    )

def _row_with_type(row: dict) -> dict:
    # Ensure 'type' key exists for legacy rows if column missing
    if 'type' not in row:
        row = {**row, 'type': 'scrum'}
    return row

def _project_from_row(row: dict) -> Project:
    row = _row_with_type(row)
    return Project(
        id=row['id'],
        name=row['name'],
        key=row['key'],
        type=row['type'],
        description=row.get('description'),
        status=row.get('status'),
        created_at=row.get('created_at'),
    updated_at=row.get('updated_at'),
    archived_at=row.get('archived_at'),
    slug=row.get('slug'),
    )

def _log_project_activity(project_id: UUID, user_id: UUID, action: str, meta: Optional[dict] = None) -> None:
    """Best-effort insert into project_activity; swallow if table absent."""
    try:  # pragma: no cover - side effect only
        supabase.table("project_activity").insert({
            "id": str(uuid4()),
            "project_id": str(project_id),
            "actor_user_id": str(user_id),
            "action": action,
            "meta": meta or {},
        }).execute()
    except Exception:
        pass

@router.get("", response_model=List[Project])
def list_projects(q: Optional[str] = None, status: Optional[str] = None, current_user: UserModel = Depends(get_current_user)):
    """List projects with optional text search (name/key) and status filter.
    status may be 'active' or 'archived'. If omitted returns all owned projects."""
    try:
        query = supabase.table("projects").select(
            "id,name,key,type,description,status,created_at,updated_at,archived_at,slug"
        ).eq("owner_id", str(current_user.id))
        if status in {"active", "archived"}:
            query = query.eq("status", status)
        res = query.execute()
    except APIError as e:  # legacy schema missing 'type'
        if 'type' in str(e):
            try:
                query = supabase.table("projects").select("id,name,key,description,status,created_at,updated_at,archived_at").eq("owner_id", str(current_user.id))
                if status in {"active", "archived"}:
                    query = query.eq("status", status)
                res = query.execute()
            except Exception:
                res = supabase.table("projects").select("id,name,key").eq("owner_id", str(current_user.id)).execute()
        else:
            raise
    data = getattr(res, 'data', []) or []
    if q:
        q_low = q.lower()
        data = [p for p in data if (p.get('name','').lower().find(q_low) != -1) or (p.get('key','').lower().find(q_low) != -1)]
    return [_project_from_row(p) for p in data]

@router.get("/paginated")
def list_projects_paginated(q: Optional[str] = None, status: Optional[str] = None, limit: int = 20, offset: int = 0, current_user: UserModel = Depends(get_current_user)):
    """Paginated projects list returning metadata. Maintains same filtering semantics as list_projects.
    Returns: { items: Project[], total: int, limit: int, offset: int }"""
    if limit > 100:
        limit = 100
    if offset < 0:
        offset = 0
    try:
        query = supabase.table("projects").select(
            "id,name,key,type,description,status,created_at,updated_at,archived_at,slug"
        ).eq("owner_id", str(current_user.id))
        if status in {"active", "archived"}:
            query = query.eq("status", status)
        res = query.execute()
    except APIError as e:
        if 'type' in str(e):
            try:
                query = supabase.table("projects").select("id,name,key,description,status,created_at,updated_at,archived_at").eq("owner_id", str(current_user.id))
                if status in {"active", "archived"}:
                    query = query.eq("status", status)
                res = query.execute()
            except Exception:
                res = supabase.table("projects").select("id,name,key").eq("owner_id", str(current_user.id)).execute()
        else:
            raise
    data = getattr(res, 'data', []) or []
    if q:
        q_low = q.lower()
        data = [p for p in data if (p.get('name','').lower().find(q_low) != -1) or (p.get('key','').lower().find(q_low) != -1)]
    total = len(data)
    window = data[offset: offset + limit]
    return {
        "items": [_project_from_row(p).dict() for p in window],
        "total": total,
        "limit": limit,
        "offset": offset
    }

@router.get("/stats-batch")
def batch_project_stats(ids: str, current_user: UserModel = Depends(get_current_user)):
    """Return lightweight category_counts for multiple projects.
    Query param ids is comma-separated project UUIDs.
    Response shape: { project_id: { todo: int, in_progress: int, done: int } }"""
    import re
    id_list = [i for i in ids.split(',') if re.fullmatch(r"[0-9a-fA-F-]{36}", i)]
    if not id_list:
        return {}
    # Ensure ownership: fetch allowed ids
    allowed_res = supabase.table("projects").select("id").in_("id", id_list).eq("owner_id", str(current_user.id)).execute()
    allowed_rows = getattr(allowed_res, 'data', []) or []
    allowed_ids = {r.get('id') for r in allowed_rows if r.get('id')}
    if not allowed_ids:
        return {}
    # Fetch statuses for all allowed items in a single query
    items_res = supabase.table("items").select("project_id,status").in_("project_id", list(allowed_ids)).execute()
    rows = getattr(items_res, 'data', []) or []
    category_map = {"todo": "todo", "in_progress": "in_progress", "doing": "in_progress", "done": "done", "completed": "done"}
    out: Dict[str, Dict[str, int]] = {pid: {"todo": 0, "in_progress": 0, "done": 0} for pid in allowed_ids}
    for r in rows:
        pid = r.get('project_id')
        if pid not in out:
            continue
        s = (r.get('status') or 'todo').lower()
        cat = category_map.get(s, 'todo')
        out[pid][cat] += 1
    return out

@router.get("/by-slug/{slug}", response_model=ProjectDetail)
def get_project_by_slug(slug: str, current_user: UserModel = Depends(get_current_user)):
    res = supabase.table("projects").select("id,name,key,type,description,status,created_at,updated_at,archived_at,slug").eq("slug", slug).eq("owner_id", str(current_user.id)).maybe_single().execute()
    row = getattr(res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    project_id = row['id']
    items_res = supabase.table("items").select("id").eq("project_id", project_id).execute()
    items_count = len(getattr(items_res, 'data', []) or [])
    sprint_res = supabase.table("sprints").select("id").eq("project_id", project_id).eq("state", "active").limit(1).execute()
    active_sprint_id = None
    sdata = getattr(sprint_res, 'data', []) or []
    if sdata:
        active_sprint_id = sdata[0].get('id')
    proj = _project_from_row(row)
    return ProjectDetail(**proj.dict(), items_count=items_count, active_sprint_id=active_sprint_id)

@router.post("", response_model=Project, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, current_user: UserModel = Depends(get_current_user)):
    key = _normalize_key(body.key)
    existing = supabase.table("projects").select("id").eq("owner_id", str(current_user.id)).eq("key", key).maybe_single().execute()
    if getattr(existing, "data", None):
        raise HTTPException(status_code=400, detail="Project key already exists")
    
    # Get or create default workspace
    workspace_res = supabase.table("workspaces").select("id").limit(1).execute()
    workspace_data = getattr(workspace_res, "data", [])
    
    if not workspace_data:
        # Create default workspace
        workspace_payload = {
            "id": str(uuid4()),
            "name": f"{current_user.email}'s Workspace"
        }
        workspace_ins = supabase.table("workspaces").insert(workspace_payload).execute()
        workspace_data = getattr(workspace_ins, "data", [])
        if not workspace_data:
            raise HTTPException(status_code=500, detail="Failed to create workspace")
    
    workspace_id = workspace_data[0]["id"]
    
    base_payload = {
        "id": str(uuid4()), 
        "name": body.name.strip(), 
        "key": key, 
        "owner_id": str(current_user.id),
        "workspace_id": str(workspace_id)
    }
    # Slug generation (best-effort unique on name; fallback to key)
    import re
    base_slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip('-')[:40]
    candidate_slug = base_slug or key.lower()
    try:
        i = 1
        original = candidate_slug
        while True:
            existing_slug = supabase.table("projects").select("id").eq("slug", candidate_slug).limit(1).execute()
            if not getattr(existing_slug, 'data', None):
                break
            candidate_slug = f"{original}-{i}"[:48]
            i += 1
    except Exception:
        pass  # slug column may not exist yet
    insert_payload = {**base_payload, "type": body.type, "slug": candidate_slug}
    try:
        ins = supabase.table("projects").insert(insert_payload).execute()
    except APIError as e:
        if 'type' in str(e):  # fallback insert without column
            # Remove slug if slug column also missing
            fallback_payload = {k: v for k, v in base_payload.items()}
            try:
                ins = supabase.table("projects").insert({**fallback_payload, "slug": candidate_slug}).execute()
            except Exception:
                ins = supabase.table("projects").insert(fallback_payload).execute()
        else:
            raise
    data = getattr(ins, "data", None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to create project")
    row = data[0]
    proj = _project_from_row(row)
    _log_project_activity(proj.id, current_user.id, "create", {"key": proj.key, "type": proj.type})
    return proj

@router.get("/{project_id}", response_model=ProjectDetail)
def get_project_detail(project_id: UUID, current_user: UserModel = Depends(get_current_user)):
    res = supabase.table("projects").select("id,name,key,type,description,status,created_at,updated_at,archived_at,slug").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    row = getattr(res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    items_res = supabase.table("items").select("id").eq("project_id", str(project_id)).execute()
    items_count = len(getattr(items_res, 'data', []) or [])
    # Active sprint (if any)
    sprint_res = supabase.table("sprints").select("id").eq("project_id", str(project_id)).eq("state", "active").limit(1).execute()
    active_sprint_id = None
    sdata = getattr(sprint_res, 'data', []) or []
    if sdata:
        active_sprint_id = sdata[0].get('id')
    proj = _project_from_row(row)
    return ProjectDetail(**proj.dict(), items_count=items_count, active_sprint_id=active_sprint_id)

@router.patch("/{project_id}", response_model=Project)
def update_project(project_id: UUID, body: ProjectUpdate, current_user: UserModel = Depends(get_current_user)):
    # Fetch existing
    existing = supabase.table("projects").select("id,name,key,type,description,status,created_at,updated_at,slug").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    row = getattr(existing, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    update_data: dict[str, Any] = {}
    name_changed = False
    if body.name is not None:
        new_name = body.name.strip()
        if new_name and new_name != row.get('name'):
            name_changed = True
        update_data['name'] = new_name
    if body.description is not None:
        update_data['description'] = body.description.strip() if body.description else None
    if body.status is not None:
        update_data['status'] = body.status
        if body.status == 'archived':
            update_data['archived_at'] = 'now()'
        else:
            update_data['archived_at'] = None
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    # Handle archived_at raw function vs string
    archived_at_value = update_data.pop('archived_at', None)
    if archived_at_value == 'now()':
        # Supabase RPC style update for now() isn't directly available; store timestamp via Python
        from datetime import datetime, timezone
        update_data['archived_at'] = datetime.now(timezone.utc).isoformat()
    # Slug regeneration if name changed (best-effort; ignore if column missing)
    if name_changed and update_data.get('name'):
        try:
            import re
            base_slug = re.sub(r"[^a-z0-9]+", "-", update_data['name'].lower()).strip('-')[:40]
            candidate_slug = base_slug or row.get('key', '').lower()
            i = 1
            original = candidate_slug
            while True:
                existing_slug = supabase.table("projects").select("id").eq("slug", candidate_slug).neq("id", str(project_id)).limit(1).execute()
                if not getattr(existing_slug, 'data', None):
                    break
                candidate_slug = f"{original}-{i}"[:48]
                i += 1
            update_data['slug'] = candidate_slug
        except Exception:
            pass
    upd = supabase.table("projects").update(update_data).eq("id", str(project_id)).execute()
    data = getattr(upd, 'data', None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to update project")
    proj = _project_from_row(data[0])
    _log_project_activity(project_id, current_user.id, "update", {k: update_data.get(k) for k in update_data})
    return proj

@router.post("/{project_id}/archive", response_model=Project)
def archive_project(project_id: UUID, current_user: UserModel = Depends(get_current_user)):
    upd = update_project(project_id, ProjectUpdate(status='archived'), current_user)  # type: ignore
    _log_project_activity(project_id, current_user.id, "archive")
    return upd

@router.post("/{project_id}/unarchive", response_model=Project)
def unarchive_project(project_id: UUID, current_user: UserModel = Depends(get_current_user)):
    upd = update_project(project_id, ProjectUpdate(status='active'), current_user)  # type: ignore
    _log_project_activity(project_id, current_user.id, "unarchive")
    return upd

@router.get("/{project_id}/activity", response_model=List[ProjectActivity])
def get_project_activity(project_id: UUID, limit: int = 50, current_user: UserModel = Depends(get_current_user)):
    # Confirm ownership
    proj = supabase.table("projects").select("id,owner_id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(proj, 'data', None):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        res = supabase.table("project_activity").select("id,project_id,actor_user_id,action,meta,created_at").eq("project_id", str(project_id)).order("created_at", desc=True).limit(limit).execute()
    except Exception:
        # If table missing or error, return empty list silently
        return []
    rows = getattr(res, 'data', []) or []
    activities: List[ProjectActivity] = []
    for r in rows:
        try:
            activities.append(ProjectActivity(
                id=r.get('id'),
                project_id=r.get('project_id'),
                actor_user_id=r.get('actor_user_id'),
                action=r.get('action'),
                meta=r.get('meta'),
                created_at=r.get('created_at'),
            ))
        except Exception:
            continue
    return activities

@router.get("/{project_id}/items", response_model=List[Item])
def list_items(project_id: UUID, current_user: UserModel = Depends(get_current_user)):
    proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(proj, "data", None):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        res = supabase.table("items").select("id,project_id,item_key,title,status,priority,sprint_id,backlog_rank").eq("project_id", str(project_id)).order("backlog_rank", desc=False).order("created_at", desc=False).execute()
    except APIError as e:
        # fallback if backlog_rank missing
        res = supabase.table("items").select("id,project_id,item_key,title,status,priority,sprint_id").eq("project_id", str(project_id)).order("created_at", desc=True).execute()
    return [_item_from_row(r) for r in (getattr(res, "data", []) or [])]

@router.post("/{project_id}/items", response_model=Item, status_code=status.HTTP_201_CREATED)
def create_item(project_id: UUID, body: ItemCreate, current_user: UserModel = Depends(get_current_user)):
    proj = supabase.table("projects").select("id,key").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    proj_data = getattr(proj, "data", None)
    if not proj_data:
        raise HTTPException(status_code=404, detail="Project not found")
    count_res = supabase.table("items").select("id").eq("project_id", str(project_id)).execute()
    seq = (len(getattr(count_res, "data", []) or []) + 1)
    issue_key = f"{proj_data['key']}-{seq}"
    # Compute next backlog_rank if column exists
    backlog_rank_val = None
    try:
        max_res = supabase.table("items").select("backlog_rank").eq("project_id", str(project_id)).order("backlog_rank", desc=True).limit(1).execute()
        max_data = getattr(max_res, 'data', []) or []
        if max_data and max_data[0].get('backlog_rank') is not None:
            backlog_rank_val = (max_data[0].get('backlog_rank') or 0) + 1
        else:
            backlog_rank_val = 1
    except Exception:
        backlog_rank_val = None
    insert_payload = {"id": str(uuid4()), "project_id": str(project_id), "item_key": issue_key, "title": body.title.strip() or issue_key, "status": body.status, "priority": body.priority}
    if backlog_rank_val is not None:
        insert_payload['backlog_rank'] = backlog_rank_val
    ins = supabase.table("items").insert(insert_payload).execute()
    data = getattr(ins, "data", None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to create item")
    try:  # best-effort activity log
        _log_project_activity(project_id, current_user.id, "item_create", {"item_key": issue_key, "title": insert_payload["title"], "status": insert_payload["status"]})
    except Exception:
        pass
    return _item_from_row(data[0])

@router.patch("/{project_id}/items/{item_id}", response_model=Item)
def update_item(project_id: UUID, item_id: UUID, body: ItemUpdate, current_user: UserModel = Depends(get_current_user)):
    proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(proj, "data", None):
        raise HTTPException(status_code=404, detail="Project not found")
    # Fetch existing for diff (best-effort)
    prev_res = supabase.table("items").select("id,status,title,item_key,priority,sprint_id").eq("id", str(item_id)).eq("project_id", str(project_id)).maybe_single().execute()
    prev = getattr(prev_res, "data", None)
    update_dict = {k: v for k, v in body.dict(exclude_unset=True).items() if v is not None}
    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")
    upd = supabase.table("items").update(update_dict).eq("id", str(item_id)).eq("project_id", str(project_id)).execute()
    data = getattr(upd, "data", None)
    if not data:
        raise HTTPException(status_code=404, detail="Item not found")
    row = data[0]
    # Activity diff summary
    try:
        changes = {}
        if prev:
            for field in ["title", "status", "priority", "sprint_id"]:
                old_v = prev.get(field) if isinstance(prev, dict) else None
                new_v = row.get(field)
                if old_v != new_v:
                    changes[field] = {"from": old_v, "to": new_v}
        if changes:
            _log_project_activity(project_id, current_user.id, "item_update", {"item_key": row.get("item_key"), **changes})
    except Exception:
        pass
    return _item_from_row(row)

class ItemsReorderPayload(BaseModel):
    item_ids: List[UUID]

@router.post("/{project_id}/items/reorder")
def reorder_items(project_id: UUID, payload: ItemsReorderPayload, current_user: UserModel = Depends(get_current_user)):
    # Validate project ownership
    proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(proj, 'data', None):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        # Assign ranks incrementally top->bottom for natural ascending ordering
        updates: List[Dict[str, Any]] = []
        for idx, iid in enumerate(payload.item_ids):
            updates.append({"id": str(iid), "backlog_rank": idx + 1})
        if updates:
            supabase.table("items").upsert(updates).execute()
    except APIError as e:
        if 'backlog_rank' in str(e):
            # Column missing; ignore silently
            pass
        else:
            raise
    _log_project_activity(project_id, current_user.id, "items_reorder", {"count": len(payload.item_ids)})
    return {"success": True}

@router.get("/{project_id}/stats")
def project_stats(project_id: UUID, current_user: UserModel = Depends(get_current_user)):
    proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(proj, 'data', None):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        res = supabase.table("items").select("status").eq("project_id", str(project_id)).execute()
        rows = getattr(res, 'data', []) or []
    except Exception:
        rows = []
    counts: Dict[str, int] = {}
    for r in rows:
        s = (r.get('status') or 'unknown').lower()
        counts[s] = counts.get(s, 0) + 1
    category_map = {"todo": "todo", "in_progress": "in_progress", "doing": "in_progress", "done": "done", "completed": "done"}
    cat_counts: Dict[str, int] = {"todo": 0, "in_progress": 0, "done": 0}
    for status, c in counts.items():
        cat = category_map.get(status, 'todo')
        cat_counts[cat] += c
    return {"status_counts": counts, "category_counts": cat_counts, "total": sum(counts.values())}

@router.get("/{project_id}/sprints", response_model=List[Sprint])
def list_sprints(project_id: UUID, current_user: UserModel = Depends(get_current_user)):
    try:
        proj = supabase.table("projects").select("id,type").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    except APIError as e:
        if 'type' in str(e):
            proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
        else:
            raise
    if not getattr(proj, "data", None):
        raise HTTPException(status_code=404, detail="Project not found")
    res = supabase.table("sprints").select("id,project_id,name,state,goal,start_date,end_date").eq("project_id", str(project_id)).order("created_at", desc=True).execute()
    return [_sprint_from_row(r) for r in (getattr(res, "data", []) or [])]

@router.post("/{project_id}/sprints", response_model=Sprint, status_code=status.HTTP_201_CREATED)
def create_sprint(project_id: UUID, body: SprintCreate, current_user: UserModel = Depends(get_current_user)):
    try:
        proj = supabase.table("projects").select("id,type").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    except APIError as e:
        if 'type' in str(e):
            proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
        else:
            raise
    proj_data = getattr(proj, "data", None)
    if not proj_data:
        raise HTTPException(status_code=404, detail="Project not found")
    # If legacy schema missing 'type', treat as scrum by default
    project_type = (proj_data.get('type') if isinstance(proj_data, dict) else None) or 'scrum'
    if project_type != 'scrum':
        raise HTTPException(status_code=400, detail="Cannot create sprints for a non-scrum project")
    payload = {"id": str(uuid4()), "project_id": str(project_id), "name": body.name.strip(), "state": "future", "goal": body.goal, "start_date": body.startDate, "end_date": body.endDate}
    ins = supabase.table("sprints").insert(payload).execute()
    data = getattr(ins, "data", None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to create sprint")
    return _sprint_from_row(data[0])

@router.patch("/{project_id}/sprints/{sprint_id}/start", response_model=Sprint)
def start_sprint(project_id: UUID, sprint_id: UUID, current_user: UserModel = Depends(get_current_user)):
    try:
        proj = supabase.table("projects").select("id,type").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    except APIError as e:
        if 'type' in str(e):
            proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
        else:
            raise
    if not getattr(proj, "data", None):
        raise HTTPException(status_code=404, detail="Project not found")
    supabase.table("sprints").update({"state": "closed"}).eq("project_id", str(project_id)).eq("state", "active").execute()
    upd = supabase.table("sprints").update({"state": "active"}).eq("id", str(sprint_id)).eq("project_id", str(project_id)).execute()
    data = getattr(upd, "data", None)
    if not data:
        raise HTTPException(status_code=404, detail="Sprint not found")
    return _sprint_from_row(data[0])

@router.patch("/{project_id}/sprints/{sprint_id}/complete", response_model=Sprint)
def complete_sprint(project_id: UUID, sprint_id: UUID, current_user: UserModel = Depends(get_current_user)):
    try:
        proj = supabase.table("projects").select("id,type").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    except APIError as e:
        if 'type' in str(e):
            proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
        else:
            raise
    if not getattr(proj, "data", None):
        raise HTTPException(status_code=404, detail="Project not found")
    upd = supabase.table("sprints").update({"state": "closed"}).eq("id", str(sprint_id)).eq("project_id", str(project_id)).execute()
    data = getattr(upd, "data", None)
    if not data:
        raise HTTPException(status_code=404, detail="Sprint not found")
    return _sprint_from_row(data[0])

@router.post("/{project_id}/sprints/{sprint_id}/items")
def assign_items_to_sprint(project_id: UUID, sprint_id: UUID, body: AssignItems, current_user: UserModel = Depends(get_current_user)):
    proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(proj, "data", None):
        raise HTTPException(status_code=404, detail="Project not found")
    sp = supabase.table("sprints").select("id").eq("id", str(sprint_id)).eq("project_id", str(project_id)).maybe_single().execute()
    if not getattr(sp, "data", None):
        raise HTTPException(status_code=404, detail="Sprint not found")
    for iid in body.item_ids:
        supabase.table("items").update({"sprint_id": str(sprint_id)}).eq("id", str(iid)).eq("project_id", str(project_id)).execute()
    return {"success": True, "count": len(body.item_ids)}
