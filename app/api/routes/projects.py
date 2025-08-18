from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
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

@router.get("", response_model=List[Project])
def list_projects(current_user: UserModel = Depends(get_current_user)):
    try:
        res = supabase.table("projects").select("id,name,key,type").eq("owner_id", str(current_user.id)).execute()
    except APIError as e:  # legacy schema missing 'type'
        if 'type' in str(e):
            res = supabase.table("projects").select("id,name,key").eq("owner_id", str(current_user.id)).execute()
            data = [ _row_with_type(p) for p in (getattr(res, 'data', []) or []) ]
            return [Project(**p) for p in data]
        raise
    data = [ _row_with_type(p) for p in (getattr(res, "data", []) or []) ]
    return [Project(**p) for p in data]

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
    insert_payload = {**base_payload, "type": body.type}
    try:
        ins = supabase.table("projects").insert(insert_payload).execute()
    except APIError as e:
        if 'type' in str(e):  # fallback insert without column
            ins = supabase.table("projects").insert(base_payload).execute()
        else:
            raise
    data = getattr(ins, "data", None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to create project")
    row = _row_with_type(data[0])
    return Project(id=row["id"], name=row["name"], key=row["key"], type=row["type"])

@router.get("/{project_id}/items", response_model=List[Item])
def list_items(project_id: UUID, current_user: UserModel = Depends(get_current_user)):
    proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(proj, "data", None):
        raise HTTPException(status_code=404, detail="Project not found")
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
    insert_payload = {"id": str(uuid4()), "project_id": str(project_id), "item_key": issue_key, "title": body.title.strip() or issue_key, "status": body.status, "priority": body.priority}
    ins = supabase.table("items").insert(insert_payload).execute()
    data = getattr(ins, "data", None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to create item")
    return _item_from_row(data[0])

@router.patch("/{project_id}/items/{item_id}", response_model=Item)
def update_item(project_id: UUID, item_id: UUID, body: ItemUpdate, current_user: UserModel = Depends(get_current_user)):
    proj = supabase.table("projects").select("id").eq("id", str(project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(proj, "data", None):
        raise HTTPException(status_code=404, detail="Project not found")
    update_dict = {k: v for k, v in body.dict(exclude_unset=True).items() if v is not None}
    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")
    upd = supabase.table("items").update(update_dict).eq("id", str(item_id)).eq("project_id", str(project_id)).execute()
    data = getattr(upd, "data", None)
    if not data:
        raise HTTPException(status_code=404, detail="Item not found")
    return _item_from_row(data[0])

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
