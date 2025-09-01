from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict
from uuid import UUID, uuid4
from app.core.dependencies import supabase, get_current_user, UserModel
try:
    from postgrest.exceptions import APIError  # type: ignore
except Exception:  # pragma: no cover
    APIError = Exception  # type: ignore

router = APIRouter(prefix="/api/issues", tags=["Issues"], dependencies=[Depends(get_current_user)])

class IssueCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    project_id: Optional[UUID] = None
    type: Optional[str] = Field("task", max_length=32)
    priority: Optional[str] = Field(None, max_length=16)
    status: Optional[str] = Field("todo", max_length=32)
    description: Optional[str] = None
    epic_id: Optional[UUID] = None

class IssueUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    project_id: Optional[UUID] = None
    type: Optional[str] = Field(None, max_length=32)
    priority: Optional[str] = Field(None, max_length=16)
    status: Optional[str] = Field(None, max_length=32)
    assignee_name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    epic_id: Optional[UUID] = None

class Issue(BaseModel):
    id: UUID
    issue_key: str
    title: str
    status: Optional[str] = None
    priority: Optional[str] = None
    type: Optional[str] = None
    project_id: Optional[UUID] = None
    assignee_name: Optional[str] = None
    description: Optional[str] = None
    epic_id: Optional[UUID] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class IssueComment(BaseModel):
    id: UUID
    issue_id: UUID
    author_user_id: UUID
    body: str
    created_at: Optional[str] = None

def _log_issue_activity(issue_id: UUID, user_id: UUID, action: str, meta: Optional[Dict[str, Any]] = None):
    try:  # pragma: no cover
        supabase.table("issue_activity").insert({
            "id": str(uuid4()),
            "issue_id": str(issue_id),
            "actor_user_id": str(user_id),
            "action": action,
            "meta": meta or {}
        }).execute()
    except Exception:
        pass

def _issue_from_row(row: dict) -> Issue:
    return Issue(
        id=row['id'],
        issue_key=row['issue_key'],
        title=row['title'],
        status=row.get('status'),
        priority=row.get('priority'),
        type=row.get('type'),
        project_id=row.get('project_id'),
    assignee_name=row.get('assignee_name'),
    description=row.get('description'),
    epic_id=row.get('epic_id'),
        created_at=row.get('created_at'),
        updated_at=row.get('updated_at'),
    )

class IssueListResponse(BaseModel):
    items: List[Issue]
    total: int
    limit: int
    offset: int

@router.get("", response_model=IssueListResponse)
def list_issues(q: Optional[str] = None, status: Optional[str] = None, project_id: Optional[UUID] = None, priority: Optional[str] = None, type: Optional[str] = None, epic_id: Optional[UUID] = None, limit: int = 50, offset: int = 0, current_user: UserModel = Depends(get_current_user)):
    if limit > 100:
        limit = 100
    if offset < 0:
        offset = 0
    try:
    query = supabase.table("issues").select("id,issue_key,title,status,priority,type,project_id,assignee_name,description,epic_id,created_at,updated_at").eq("owner_id", str(current_user.id))
        if status:
            query = query.eq("status", status)
        if project_id:
            query = query.eq("project_id", str(project_id))
        if priority:
            query = query.eq("priority", priority)
        if type:
            query = query.eq("type", type)
        if epic_id:
            query = query.eq("epic_id", str(epic_id))
        res = query.execute()
    except APIError:
        res = supabase.table("issues").select("id,issue_key,title,status,priority,type,project_id,assignee_name,description,epic_id,created_at,updated_at").eq("owner_id", str(current_user.id)).execute()
    rows = getattr(res, 'data', []) or []
    if q:
        q_low = q.lower()
        rows = [r for r in rows if q_low in (r.get('title') or '').lower() or q_low in (r.get('issue_key') or '').lower()]
    total = len(rows)
    sliced = rows[offset: offset + limit]
    return IssueListResponse(items=[_issue_from_row(r) for r in sliced], total=total, limit=limit, offset=offset)

@router.post("", response_model=Issue, status_code=status.HTTP_201_CREATED)
def create_issue(body: IssueCreate, current_user: UserModel = Depends(get_current_user)):
    # Optional project ownership check
    if body.project_id:
        proj = supabase.table("projects").select("id, key").eq("id", str(body.project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
        if not getattr(proj, 'data', None):
            raise HTTPException(status_code=404, detail="Project not found")
        proj_key = getattr(proj, 'data', {}).get('key') if isinstance(getattr(proj, 'data', None), dict) else None
    else:
        proj_key = None
    # Sequence for issue key: global incremental count (fallback to random)
    count_res = supabase.table("issues").select("id").eq("owner_id", str(current_user.id)).execute()
    seq = (len(getattr(count_res, 'data', []) or []) + 1)
    base_prefix = proj_key or "ISS"
    issue_key = f"{base_prefix}-{seq}"
    payload = {
        "id": str(uuid4()),
        "issue_key": issue_key,
        "title": body.title.strip(),
        "status": body.status or 'todo',
        "priority": body.priority,
        "type": body.type or 'task',
        "project_id": str(body.project_id) if body.project_id else None,
        "owner_id": str(current_user.id)
    }
    ins = supabase.table("issues").insert(payload).execute()
    data = getattr(ins, 'data', None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to create issue")
    row = data[0]
    _log_issue_activity(row['id'], current_user.id, 'create', {"issue_key": issue_key})
    return _issue_from_row(row)

@router.get("/{issue_id}", response_model=Issue)
def get_issue(issue_id: UUID, current_user: UserModel = Depends(get_current_user)):
    res = supabase.table("issues").select("id,issue_key,title,status,priority,type,project_id,assignee_name,description,epic_id,created_at,updated_at").eq("id", str(issue_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    row = getattr(res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Issue not found")
    return _issue_from_row(row)

@router.patch("/{issue_id}", response_model=Issue)
def update_issue(issue_id: UUID, body: IssueUpdate, current_user: UserModel = Depends(get_current_user)):
    existing = supabase.table("issues").select("id,title,status,priority,type,project_id,issue_key,assignee_name").eq("id", str(issue_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    row = getattr(existing, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Issue not found")
    update_dict = {k: v for k, v in body.dict(exclude_unset=True).items()}
    if body.project_id:
        proj = supabase.table("projects").select("id").eq("id", str(body.project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
        if not getattr(proj, 'data', None):
            raise HTTPException(status_code=404, detail="Project not found")
    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")
    upd = supabase.table("issues").update(update_dict).eq("id", str(issue_id)).execute()
    data = getattr(upd, 'data', None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to update issue")
    new_row = data[0]
    # Diff
    try:
        changes = {}
    for field in ["title", "status", "priority", "type", "project_id", "assignee_name", "description", "epic_id"]:
            if row.get(field) != new_row.get(field):
                changes[field] = {"from": row.get(field), "to": new_row.get(field)}
        if changes:
            _log_issue_activity(issue_id, current_user.id, 'update', {"issue_key": row.get('issue_key'), **changes})
    except Exception:
        pass
    return _issue_from_row(new_row)

@router.delete("/{issue_id}")
def delete_issue(issue_id: UUID, current_user: UserModel = Depends(get_current_user)):
    existing = supabase.table("issues").select("id,issue_key").eq("id", str(issue_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(existing, 'data', None):
        raise HTTPException(status_code=404, detail="Issue not found")
    supabase.table("issues").delete().eq("id", str(issue_id)).execute()
    _log_issue_activity(issue_id, current_user.id, 'delete', {"issue_key": getattr(existing, 'data', {}).get('issue_key')})
    return {"success": True}

# Comments endpoints
class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1)

@router.get("/{issue_id}/comments", response_model=List[IssueComment])
def list_comments(issue_id: UUID, current_user: UserModel = Depends(get_current_user)):
    # ensure access
    issue = supabase.table("issues").select("id").eq("id", str(issue_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(issue, 'data', None):
        raise HTTPException(status_code=404, detail="Issue not found")
    res = supabase.table("issue_comments").select("id,issue_id,author_user_id,body,created_at").eq("issue_id", str(issue_id)).order("created_at", desc=False).execute()
    rows = getattr(res, 'data', []) or []
    return [IssueComment(**r) for r in rows]

@router.post("/{issue_id}/comments", response_model=IssueComment, status_code=status.HTTP_201_CREATED)
def create_comment(issue_id: UUID, body: CommentCreate, current_user: UserModel = Depends(get_current_user)):
    issue = supabase.table("issues").select("id,issue_key").eq("id", str(issue_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    row = getattr(issue, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Issue not found")
    payload = {"id": str(uuid4()), "issue_id": str(issue_id), "author_user_id": str(current_user.id), "body": body.body.strip()}
    ins = supabase.table("issue_comments").insert(payload).execute()
    data = getattr(ins, 'data', None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to create comment")
    _log_issue_activity(issue_id, current_user.id, 'comment', {"issue_key": row.get('issue_key')})
    r = data[0]
    return IssueComment(**r)

@router.delete("/{issue_id}/comments/{comment_id}")
def delete_comment(issue_id: UUID, comment_id: UUID, current_user: UserModel = Depends(get_current_user)):
    # ensure ownership of issue and comment author
    issue = supabase.table("issues").select("id").eq("id", str(issue_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    if not getattr(issue, 'data', None):
        raise HTTPException(status_code=404, detail="Issue not found")
    comment = supabase.table("issue_comments").select("id,author_user_id").eq("id", str(comment_id)).eq("issue_id", str(issue_id)).maybe_single().execute()
    c_row = getattr(comment, 'data', None)
    if not c_row:
        raise HTTPException(status_code=404, detail="Comment not found")
    if c_row.get('author_user_id') != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not allowed to delete this comment")
    supabase.table("issue_comments").delete().eq("id", str(comment_id)).execute()
    return {"success": True}
