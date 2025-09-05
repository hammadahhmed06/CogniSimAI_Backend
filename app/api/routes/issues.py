from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict, Set, Tuple
from datetime import datetime, timezone
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
    workspace_id: Optional[UUID] = None
    type: Optional[str] = Field("task", max_length=32)
    priority: Optional[str] = Field(None, max_length=16)
    status: Optional[str] = Field("todo", max_length=32)
    description: Optional[str] = None
    epic_id: Optional[UUID] = None
    story_points: Optional[int] = Field(None, ge=0, le=1000)
    business_value: Optional[int] = Field(None, ge=0, le=100)
    effort_estimate: Optional[int] = Field(None, ge=0, le=100)
    risk_level: Optional[str] = Field(None, max_length=16)
    acceptance_criteria: Optional[List[Dict[str, Any]]] = None  # list of {text:str, done:bool}
    sprint_id: Optional[UUID] = None

class IssueUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    project_id: Optional[UUID] = None
    workspace_id: Optional[UUID] = None
    type: Optional[str] = Field(None, max_length=32)
    priority: Optional[str] = Field(None, max_length=16)
    status: Optional[str] = Field(None, max_length=32)
    assignee_name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    epic_id: Optional[UUID] = None
    story_points: Optional[int] = Field(None, ge=0, le=1000)
    business_value: Optional[int] = Field(None, ge=0, le=100)
    effort_estimate: Optional[int] = Field(None, ge=0, le=100)
    risk_level: Optional[str] = Field(None, max_length=16)
    acceptance_criteria: Optional[List[Dict[str, Any]]] = None
    sprint_id: Optional[UUID] = None

class Issue(BaseModel):
    id: UUID
    issue_key: str
    title: str
    status: Optional[str] = None
    priority: Optional[str] = None
    type: Optional[str] = None
    project_id: Optional[UUID] = None
    workspace_id: Optional[UUID] = None
    assignee_name: Optional[str] = None
    description: Optional[str] = None
    epic_id: Optional[UUID] = None
    story_points: Optional[int] = None
    business_value: Optional[int] = None
    effort_estimate: Optional[int] = None
    risk_level: Optional[str] = None
    acceptance_criteria: Optional[List[Dict[str, Any]]] = None
    sprint_id: Optional[UUID] = None
    backlog_rank: Optional[int] = None
    started_at: Optional[str] = None
    done_at: Optional[str] = None
    priority_score: Optional[float] = None
    priority_score_meta: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class IssueListResponse(BaseModel):
    items: List[Issue]
    total: int
    limit: int
    offset: int

class BulkIssueCreateRequest(BaseModel):
    items: List[IssueCreate]
    dry_run: Optional[bool] = False

class BulkIssueCreateResult(BaseModel):
    id: Optional[UUID]
    issue_key: str
    title: str
    errors: Optional[str] = None

class BulkIssueCreateResponse(BaseModel):
    created: int
    items: List[BulkIssueCreateResult]
    dry_run: bool

class IssueComment(BaseModel):
    id: UUID
    issue_id: UUID
    author_user_id: UUID
    body: str
    created_at: Optional[str] = None

def _insert_issue_activity(issue_id: UUID, user_id: UUID, action: str, meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
    try:
        payload = {
            "id": str(uuid4()),
            "issue_id": str(issue_id),
            "actor_user_id": str(user_id),
            "action": action,
            "meta": meta or {}
        }
        res = supabase.table("issue_activity").insert(payload).execute()
        data = getattr(res, 'data', None)
        if data:
            return data[0].get('id')
    except Exception:
        return None
    return None

def _log_issue_activity(issue_id: UUID, user_id: UUID, action: str, meta: Optional[Dict[str, Any]] = None):
    _insert_issue_activity(issue_id, user_id, action, meta)

def _build_search_blob(title: Optional[str], description: Optional[str], acceptance: Optional[List[Dict[str, Any]]]) -> str:
    parts: List[str] = []
    if title:
        parts.append(title)
    if description:
        parts.append(description)
    if acceptance:
        for ac in acceptance:
            if isinstance(ac, dict):
                txt = ac.get('text')
                if txt:
                    parts.append(txt)
    blob = '\n'.join(parts)
    return blob[:18000]

def _issue_from_row(row: dict) -> Issue:
    return Issue(
        id=row['id'],
        issue_key=row['issue_key'],
        title=row['title'],
        status=row.get('status'),
        priority=row.get('priority'),
        type=row.get('type'),
        project_id=row.get('project_id'),
        workspace_id=row.get('workspace_id'),
    assignee_name=row.get('assignee_name'),
    description=row.get('description'),
    epic_id=row.get('epic_id'),
    story_points=row.get('story_points'),
    business_value=row.get('business_value'),
    effort_estimate=row.get('effort_estimate'),
    risk_level=row.get('risk_level'),
    acceptance_criteria=row.get('acceptance_criteria'),
    sprint_id=row.get('sprint_id'),
    backlog_rank=row.get('backlog_rank'),
    started_at=row.get('started_at'),
    done_at=row.get('done_at'),
    priority_score=row.get('priority_score'),
    priority_score_meta=row.get('priority_score_meta'),
        created_at=row.get('created_at'),
        updated_at=row.get('updated_at'),
    )

class IssueSearchResponse(IssueListResponse):
    query: Optional[str] = None

@router.get("", response_model=IssueListResponse)
def list_issues(q: Optional[str] = None, status: Optional[str] = None, project_id: Optional[UUID] = None, workspace_id: Optional[UUID] = None, priority: Optional[str] = None, type: Optional[str] = None, epic_id: Optional[UUID] = None, sprint_id: Optional[UUID] = None, limit: int = 50, offset: int = 0, current_user: UserModel = Depends(get_current_user)):
    if limit > 100:
        limit = 100
    if offset < 0:
        offset = 0
    try:
        query = supabase.table("issues").select(
            "id,issue_key,title,status,priority,type,project_id,workspace_id,assignee_name,description,epic_id,story_points,business_value,effort_estimate,risk_level,acceptance_criteria,sprint_id,backlog_rank,started_at,done_at,priority_score,priority_score_meta,created_at,updated_at"
        ).eq("owner_id", str(current_user.id))
        if status:
            query = query.eq("status", status)
        if project_id:
            query = query.eq("project_id", str(project_id))
        if workspace_id:
            query = query.eq("workspace_id", str(workspace_id))
        if priority:
            query = query.eq("priority", priority)
        if type:
            query = query.eq("type", type)
        if epic_id:
            query = query.eq("epic_id", str(epic_id))
        if sprint_id:
            query = query.eq("sprint_id", str(sprint_id))
        res = query.execute()
    except APIError:
        res = supabase.table("issues").select(
            "id,issue_key,title,status,priority,type,project_id,workspace_id,assignee_name,description,epic_id,story_points,business_value,effort_estimate,risk_level,acceptance_criteria,sprint_id,backlog_rank,started_at,done_at,priority_score,priority_score_meta,created_at,updated_at"
        ).eq("owner_id", str(current_user.id)).execute()
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
    # Determine workspace_id (priority: explicit -> project.workspace_id)
    workspace_id: Optional[str] = None
    if body.workspace_id:
        workspace_id = str(body.workspace_id)
    elif body.project_id and proj_key:
        try:
            pws = supabase.table("projects").select("workspace_id").eq("id", str(body.project_id)).maybe_single().execute()
            if getattr(pws, 'data', None):
                workspace_id = getattr(pws, 'data').get('workspace_id')  # type: ignore
        except Exception:
            pass
    # Sequence for issue key: per-project if available else global
    if body.project_id:
        count_res = supabase.table("issues").select("id").eq("project_id", str(body.project_id)).execute()
    else:
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
        "workspace_id": workspace_id,
        "description": body.description,
        "epic_id": str(body.epic_id) if body.epic_id else None,
        "story_points": body.story_points,
        "business_value": body.business_value,
        "effort_estimate": body.effort_estimate,
        "risk_level": body.risk_level,
        "acceptance_criteria": body.acceptance_criteria,
        "sprint_id": str(body.sprint_id) if body.sprint_id else None,
        "search_blob": _build_search_blob(body.title, body.description, body.acceptance_criteria),
        "owner_id": str(current_user.id)
    }
    # Compute initial priority score (best-effort; ignore failures silently)
    try:
        score, meta = _compute_priority(payload)
        payload["priority_score"] = score
        payload["priority_score_meta"] = meta
    except Exception:  # pragma: no cover
        pass
    ins = supabase.table("issues").insert(payload).execute()
    data = getattr(ins, 'data', None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to create issue")
    row = data[0]
    _log_issue_activity(row['id'], current_user.id, 'create', {"issue_key": issue_key})
    return _issue_from_row(row)

@router.get("/{issue_id}", response_model=Issue)
def get_issue(issue_id: UUID, current_user: UserModel = Depends(get_current_user)):
    # Expanded select to include new planning / scoring fields
    res = supabase.table("issues").select("id,issue_key,title,status,priority,type,project_id,workspace_id,assignee_name,description,epic_id,story_points,business_value,effort_estimate,risk_level,acceptance_criteria,sprint_id,backlog_rank,started_at,done_at,priority_score,priority_score_meta,created_at,updated_at").eq("id", str(issue_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    row = getattr(res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Issue not found")
    return _issue_from_row(row)

@router.patch("/{issue_id}", response_model=Issue)
def update_issue(issue_id: UUID, body: IssueUpdate, current_user: UserModel = Depends(get_current_user)):
    existing = supabase.table("issues").select("id,title,status,priority,type,project_id,workspace_id,issue_key,assignee_name,description,epic_id,story_points,business_value,effort_estimate,risk_level,acceptance_criteria,sprint_id,backlog_rank,started_at,done_at").eq("id", str(issue_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    row = getattr(existing, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Issue not found")
    update_dict = {k: v for k, v in body.dict(exclude_unset=True).items()}
    # Prevent setting epic_id to itself & simple cycle prevention (walk ancestors)
    if 'epic_id' in update_dict and update_dict['epic_id']:
        if isinstance(update_dict['epic_id'], UUID):
            new_epic_id = update_dict['epic_id']
        else:
            try:
                new_epic_id = UUID(str(update_dict['epic_id']))
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid epic_id")
        if new_epic_id == issue_id:
            raise HTTPException(status_code=400, detail="Epic cannot be itself")
        # Walk up parent chain to detect loops (depth limit 20)
        depth = 0
        cur = new_epic_id
        visited: Set[UUID] = set()
        while cur and depth < 20:
            if cur == issue_id:
                raise HTTPException(status_code=400, detail="Epic assignment would create a cycle")
            if cur in visited:
                break
            visited.add(cur)
            parent_res = supabase.table("issues").select("epic_id").eq("id", str(cur)).eq("owner_id", str(current_user.id)).maybe_single().execute()
            parent_row = getattr(parent_res, 'data', None)
            if not parent_row:
                break
            parent_epic = parent_row.get('epic_id')
            if not parent_epic:
                break
            try:
                cur = UUID(parent_epic)
            except Exception:
                break
            depth += 1
    # Convert UUID fields to strings for storage
    if 'project_id' in update_dict and isinstance(update_dict['project_id'], UUID):
        update_dict['project_id'] = str(update_dict['project_id'])
    if 'epic_id' in update_dict and isinstance(update_dict['epic_id'], UUID):
        update_dict['epic_id'] = str(update_dict['epic_id'])
    if 'sprint_id' in update_dict and isinstance(update_dict['sprint_id'], UUID):
        update_dict['sprint_id'] = str(update_dict['sprint_id'])
    if body.project_id:
        proj = supabase.table("projects").select("id").eq("id", str(body.project_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
        if not getattr(proj, 'data', None):
            raise HTTPException(status_code=404, detail="Project not found")
    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")
    # Auto timestamps for cycle metrics
    now_iso = datetime.now(timezone.utc).isoformat()
    status_target = update_dict.get('status')
    if status_target and status_target == 'in_progress' and not row.get('started_at'):
        update_dict['started_at'] = now_iso
    if status_target and status_target == 'done' and not row.get('done_at'):
        update_dict['done_at'] = now_iso
    # Recompute priority score if relevant fields changed
    recompute_fields = {"business_value", "effort_estimate", "story_points", "risk_level", "priority"}
    if recompute_fields.intersection(update_dict.keys()):
        try:
            merged = {**row, **update_dict}
            score, meta = _compute_priority(merged)
            update_dict["priority_score"] = score
            update_dict["priority_score_meta"] = meta
        except Exception:  # pragma: no cover
            pass
    # If text fields changed update search blob
    if any(f in update_dict for f in ["title", "description", "acceptance_criteria"]):
        try:
            update_dict["search_blob"] = _build_search_blob(
                update_dict.get('title') or row.get('title'),
                update_dict.get('description') or row.get('description'),
                update_dict.get('acceptance_criteria') or row.get('acceptance_criteria')
            )
        except Exception:
            pass
    upd = supabase.table("issues").update(update_dict).eq("id", str(issue_id)).execute()
    data = getattr(upd, 'data', None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to update issue")
    new_row = data[0]
    # Diff
    try:  # pragma: no cover
        changes: Dict[str, Any] = {}
        for field in ["title", "status", "priority", "type", "project_id", "workspace_id", "assignee_name", "description", "epic_id", "story_points", "business_value", "effort_estimate", "risk_level", "acceptance_criteria", "sprint_id", "backlog_rank", "started_at", "done_at"]:
            if row.get(field) != new_row.get(field):
                changes[field] = {"from": row.get(field), "to": new_row.get(field)}
        if changes:
            act_id = _insert_issue_activity(issue_id, current_user.id, 'update', {"issue_key": row.get('issue_key'), "fields": list(changes.keys())})
            # Insert typed diffs if dedicated table exists
            if act_id:
                try:
                    diffs_payload = []
                    for field, diff in changes.items():
                        diffs_payload.append({
                            "id": str(uuid4()),
                            "activity_id": act_id,
                            "field": field,
                            "from_value": diff.get('from'),
                            "to_value": diff.get('to')
                        })
                    if diffs_payload:
                        supabase.table("issue_activity_diffs").insert(diffs_payload).execute()
                except Exception:
                    pass
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

# ---------------- Dependency Management -----------------
class DependencyCreate(BaseModel):
    depends_on_id: UUID

class IssueDependency(BaseModel):
    id: UUID
    issue_id: UUID
    depends_on_id: UUID
    created_at: Optional[str] = None
    depends_on_issue: Optional[Dict[str, Any]] = None

def _fetch_issue(issue_id: UUID, owner_id: UUID):
    res = supabase.table("issues").select("id,issue_key,title,status,project_id,type,story_points,business_value,effort_estimate,risk_level").eq("id", str(issue_id)).eq("owner_id", str(owner_id)).maybe_single().execute()
    return getattr(res, 'data', None)

def _build_dependency_graph(owner_id: UUID) -> Dict[str, Set[str]]:
    """Return adjacency list issue_id -> set(depends_on_id) for all issues owned by user."""
    # Fetch all dependencies (cannot filter by owner directly; rely on only own issues referencing own issues)
    dep_res = supabase.table("issue_dependencies").select("issue_id,depends_on_id").execute()
    rows = getattr(dep_res, 'data', []) or []
    graph: Dict[str, Set[str]] = {}
    # Build set of issue ids owned by user for pruning
    owned_res = supabase.table("issues").select("id").eq("owner_id", str(owner_id)).execute()
    owned_ids = {r['id'] for r in (getattr(owned_res, 'data', []) or []) if 'id' in r}
    for r in rows:
        a = r.get('issue_id')
        b = r.get('depends_on_id')
        if a in owned_ids and b in owned_ids:
            graph.setdefault(a, set()).add(b)
    return graph

def _detect_cycle(graph: Dict[str, Set[str]], start: str, target: str) -> bool:
    """Return True if target is reachable from start via directed edges (DFS)."""
    stack = [start]
    visited: Set[str] = set()
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in visited:
            continue
        visited.add(cur)
        for nxt in graph.get(cur, set()):
            if nxt not in visited:
                stack.append(nxt)
    return False

@router.get("/{issue_id}/dependencies", response_model=List[IssueDependency])
def list_dependencies(issue_id: UUID, current_user: UserModel = Depends(get_current_user)):
    # Ensure ownership of base issue
    base_issue = _fetch_issue(issue_id, current_user.id)
    if not base_issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    dep_res = supabase.table("issue_dependencies").select("id,issue_id,depends_on_id,created_at").eq("issue_id", str(issue_id)).execute()
    deps_rows = getattr(dep_res, 'data', []) or []
    if not deps_rows:
        return []
    depends_on_ids = [r['depends_on_id'] for r in deps_rows if r.get('depends_on_id')]
    related_res = supabase.table("issues").select("id,issue_key,title,status").in_("id", depends_on_ids).execute()
    related_map = {r['id']: r for r in (getattr(related_res, 'data', []) or []) if 'id' in r}
    out: List[IssueDependency] = []
    for r in deps_rows:
        out.append(IssueDependency(
            id=r['id'],
            issue_id=r['issue_id'],
            depends_on_id=r['depends_on_id'],
            created_at=r.get('created_at'),
            depends_on_issue=related_map.get(r['depends_on_id'])
        ))
    return out

@router.post("/{issue_id}/dependencies", response_model=IssueDependency, status_code=status.HTTP_201_CREATED)
def create_dependency(issue_id: UUID, body: DependencyCreate, current_user: UserModel = Depends(get_current_user)):
    if issue_id == body.depends_on_id:
        raise HTTPException(status_code=400, detail="Issue cannot depend on itself")
    base_issue = _fetch_issue(issue_id, current_user.id)
    if not base_issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    dep_issue = _fetch_issue(body.depends_on_id, current_user.id)
    if not dep_issue:
        raise HTTPException(status_code=404, detail="Dependency issue not found")
    # Cycle detection: adding edge issue_id -> depends_on_id cannot create path depends_on_id -> issue_id
    graph = _build_dependency_graph(current_user.id)
    # Temporarily add proposed edge to graph for detection
    graph.setdefault(str(issue_id), set()).add(str(body.depends_on_id))
    if _detect_cycle(graph, str(body.depends_on_id), str(issue_id)):
        raise HTTPException(status_code=400, detail="Dependency would create a cycle")
    payload = {"id": str(uuid4()), "issue_id": str(issue_id), "depends_on_id": str(body.depends_on_id)}
    try:
        ins = supabase.table("issue_dependencies").insert(payload).execute()
    except APIError as e:  # Unique violation or similar
        if 'duplicate' in str(e).lower():
            raise HTTPException(status_code=409, detail="Dependency already exists")
        raise
    data = getattr(ins, 'data', None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to create dependency")
    row = data[0]
    return IssueDependency(
        id=row['id'],
        issue_id=row['issue_id'],
        depends_on_id=row['depends_on_id'],
        created_at=row.get('created_at'),
        depends_on_issue={k: dep_issue.get(k) for k in ['id','issue_key','title','status']}
    )

@router.delete("/{issue_id}/dependencies/{dependency_id}")
def delete_dependency(issue_id: UUID, dependency_id: UUID, current_user: UserModel = Depends(get_current_user)):
    base_issue = _fetch_issue(issue_id, current_user.id)
    if not base_issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    existing = supabase.table("issue_dependencies").select("id").eq("id", str(dependency_id)).eq("issue_id", str(issue_id)).maybe_single().execute()
    if not getattr(existing, 'data', None):
        raise HTTPException(status_code=404, detail="Dependency not found")
    supabase.table("issue_dependencies").delete().eq("id", str(dependency_id)).execute()
    return {"success": True}

# ---------------- Epic Progress -----------------
class EpicProgress(BaseModel):
    epic_id: UUID
    total: int
    todo: int
    in_progress: int
    done: int
    story_points_total: int
    story_points_done: int

@router.get("/{issue_id}/progress", response_model=EpicProgress)
def epic_progress(issue_id: UUID, current_user: UserModel = Depends(get_current_user)):
    epic = _fetch_issue(issue_id, current_user.id)
    if not epic:
        raise HTTPException(status_code=404, detail="Issue not found")
    if (epic.get('type') or '').lower() != 'epic':
        raise HTTPException(status_code=400, detail="Not an epic")
    rows_res = supabase.table("issues").select("status,story_points").eq("epic_id", str(issue_id)).eq("owner_id", str(current_user.id)).execute()
    rows = getattr(rows_res, 'data', []) or []
    counts = {"todo": 0, "in_progress": 0, "done": 0}
    sp_total = 0
    sp_done = 0
    for r in rows:
        status_val = (r.get('status') or 'todo').lower()
        if status_val not in counts:
            status_val = 'todo'
        counts[status_val] += 1
        sp = r.get('story_points') or 0
        sp_total += sp
        if status_val == 'done':
            sp_done += sp
    return EpicProgress(epic_id=issue_id, total=len(rows), todo=counts['todo'], in_progress=counts['in_progress'], done=counts['done'], story_points_total=sp_total, story_points_done=sp_done)

# ---------------- Priority Scoring -----------------
class PriorityRecomputeResponse(BaseModel):
    issue: Issue

def _compute_priority(row: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """Compute a heuristic priority score and meta data.
    Formula (simple heuristic):
      base = business_value (0-100, default 0)
      effort_penalty = 0.5 * effort_estimate (0-100) or 0.3 * story_points (if effort not provided)
      risk_adjust: low:+5, med:0, high:-10
      score = base - effort_penalty + risk_adjust
    """
    bv = row.get('business_value') or 0
    effort = row.get('effort_estimate')
    sp = row.get('story_points')
    risk = (row.get('risk_level') or '').lower()
    risk_adjust = 0
    if risk == 'low':
        risk_adjust = 5
    elif risk == 'med' or risk == 'medium':
        risk_adjust = 0
    elif risk == 'high':
        risk_adjust = -10
    if effort is not None:
        effort_penalty = 0.5 * effort
    else:
        effort_penalty = 0.3 * (sp or 0)
    score = float(bv) - float(effort_penalty) + float(risk_adjust)
    meta = {
        "business_value": bv,
        "effort_component": effort_penalty,
        "risk_adjust": risk_adjust,
        "story_points": sp,
        "effort_estimate": effort,
        "formula": "score = business_value - effort_component + risk_adjust"
    }
    return score, meta

@router.post("/{issue_id}/score/recompute", response_model=PriorityRecomputeResponse)
def recompute_issue_score(issue_id: UUID, current_user: UserModel = Depends(get_current_user)):
    row_res = supabase.table("issues").select("id,issue_key,title,status,priority,type,project_id,assignee_name,description,epic_id,story_points,business_value,effort_estimate,risk_level,acceptance_criteria,sprint_id,started_at,done_at,priority_score,priority_score_meta,created_at,updated_at").eq("id", str(issue_id)).eq("owner_id", str(current_user.id)).maybe_single().execute()
    row = getattr(row_res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Issue not found")
    try:
        score, meta = _compute_priority(row)
        upd = supabase.table("issues").update({"priority_score": score, "priority_score_meta": meta}).eq("id", str(issue_id)).execute()
        data = getattr(upd, 'data', None)
        if data:
            row = data[0]
    except Exception:  # pragma: no cover
        raise HTTPException(status_code=500, detail="Failed to compute score")
    return PriorityRecomputeResponse(issue=_issue_from_row(row))

@router.post("/score/recompute-all")
def recompute_all_scores(current_user: UserModel = Depends(get_current_user)):
    # Fetch all issues for user (could paginate if large)
    res = supabase.table("issues").select("id,business_value,effort_estimate,story_points,risk_level").eq("owner_id", str(current_user.id)).execute()
    rows = getattr(res, 'data', []) or []
    updates: List[Dict[str, Any]] = []
    for r in rows:
        try:
            score, meta = _compute_priority(r)
            updates.append({"id": r['id'], "priority_score": score, "priority_score_meta": meta})
        except Exception:
            continue
    # Batch update (chunk if large)
    if updates:
        supabase.table("issues").upsert(updates).execute()
    return {"updated": len(updates)}

# ---------------- Bulk Create -----------------
@router.post("/bulk", response_model=BulkIssueCreateResponse)
def bulk_create_issues(body: BulkIssueCreateRequest, current_user: UserModel = Depends(get_current_user)):
    # Pre-fetch existing count to seed sequence
    count_res = supabase.table("issues").select("id").eq("owner_id", str(current_user.id)).execute()
    seq_base = len(getattr(count_res, 'data', []) or []) + 1
    project_key_cache: Dict[str, Optional[str]] = {}
    created_rows: List[Dict[str, Any]] = []
    results: List[BulkIssueCreateResult] = []
    seq = seq_base
    for item in body.items:
        try:
            proj_key = None
            if item.project_id:
                pid = str(item.project_id)
                if pid not in project_key_cache:
                    proj_res = supabase.table("projects").select("id,key").eq("id", pid).eq("owner_id", str(current_user.id)).maybe_single().execute()
                    project_key_cache[pid] = getattr(proj_res, 'data', {}).get('key') if getattr(proj_res, 'data', None) else None
                proj_key = project_key_cache.get(pid)
            base_prefix = proj_key or 'ISS'
            issue_key = f"{base_prefix}-{seq}"
            seq += 1
            started_at = None
            done_at = None
            if item.status == 'in_progress':
                started_at = datetime.utcnow().isoformat()
            if item.status == 'done':
                started_at = started_at or datetime.utcnow().isoformat()
                done_at = datetime.utcnow().isoformat()
            # Priority score
            try:
                score, meta = _compute_priority({
                    'business_value': item.business_value,
                    'effort_estimate': item.effort_estimate,
                    'risk_level': item.risk_level,
                    'story_points': item.story_points
                })
            except Exception:
                score, meta = None, None
            row = {
                "id": str(uuid4()),
                "issue_key": issue_key,
                "title": item.title.strip(),
                "status": item.status or 'todo',
                "priority": item.priority,
                "type": item.type or 'task',
                "project_id": str(item.project_id) if item.project_id else None,
                "description": item.description,
                "epic_id": str(item.epic_id) if item.epic_id else None,
                "story_points": item.story_points,
                "business_value": item.business_value,
                "effort_estimate": item.effort_estimate,
                "risk_level": item.risk_level,
                "acceptance_criteria": item.acceptance_criteria,
                "sprint_id": str(item.sprint_id) if item.sprint_id else None,
                "started_at": started_at,
                "done_at": done_at,
                "search_blob": _build_search_blob(item.title, item.description, item.acceptance_criteria),
                "priority_score": score,
                "priority_score_meta": meta,
                "owner_id": str(current_user.id)
            }
            created_rows.append(row)
            results.append(BulkIssueCreateResult(id=UUID(row['id']), issue_key=issue_key, title=item.title))
        except Exception as e:
            results.append(BulkIssueCreateResult(id=None, issue_key="ERROR", title=getattr(item, 'title', 'unknown'), errors=str(e)))
    if not body.dry_run and created_rows:
        CHUNK = 100
        for i in range(0, len(created_rows), CHUNK):
            supabase.table("issues").insert(created_rows[i:i+CHUNK]).execute()
        for r in created_rows:
            try:
                _log_issue_activity(UUID(r['id']), current_user.id, 'create', {"bulk": True, "issue_key": r['issue_key']})
            except Exception:
                pass
    return BulkIssueCreateResponse(created=0 if body.dry_run else len(created_rows), items=results, dry_run=bool(body.dry_run))

# ---------------- Basic Search -----------------
@router.get("/search", response_model=IssueSearchResponse)
def search_issues(q: str, limit: int = 50, offset: int = 0, project_id: Optional[UUID] = None, current_user: UserModel = Depends(get_current_user)):
    if limit > 100:
        limit = 100
    base_query = supabase.table("issues").select(
        "id,issue_key,title,status,priority,type,project_id,assignee_name,description,epic_id,story_points,business_value,effort_estimate,risk_level,acceptance_criteria,sprint_id,started_at,done_at,priority_score,priority_score_meta,created_at,updated_at,search_blob"
    ).eq("owner_id", str(current_user.id))
    if project_id:
        base_query = base_query.eq("project_id", str(project_id))
    res = base_query.execute()
    rows = getattr(res, 'data', []) or []
    q_low = q.lower()
    filtered = []
    for r in rows:
        hay = ' '.join([
            (r.get('title') or ''),
            (r.get('description') or ''),
            (r.get('issue_key') or ''),
            (r.get('search_blob') or '')
        ]).lower()
        if q_low in hay:
            filtered.append(r)
    total = len(filtered)
    window = filtered[offset: offset + limit]
    return IssueSearchResponse(items=[_issue_from_row(r) for r in window], total=total, limit=limit, offset=offset, query=q)

# ---------------- Dependency Graph API -----------------
class IssueGraphNode(BaseModel):
    id: UUID
    issue_key: str
    title: str
    status: Optional[str] = None
    priority: Optional[str] = None
    out_degree: int
    in_degree: int
    story_points: Optional[int] = None
    sprint_id: Optional[UUID] = None

class IssueGraphEdge(BaseModel):
    source: UUID  # issue that depends on target
    target: UUID  # issue it depends on

class IssueGraphResponse(BaseModel):
    nodes: List[IssueGraphNode]
    edges: List[IssueGraphEdge]
    topological_order: Optional[List[UUID]] = None
    cycle_detected: bool
    total: int

@router.get("/graph", response_model=IssueGraphResponse)
def dependency_graph(project_id: Optional[UUID] = None, current_user: UserModel = Depends(get_current_user)):
    # Fetch issues (minimal fields)
    issue_query = supabase.table("issues").select("id,issue_key,title,status,priority,story_points,sprint_id").eq("owner_id", str(current_user.id))
    if project_id:
        issue_query = issue_query.eq("project_id", str(project_id))
    issue_res = issue_query.execute()
    issue_rows = getattr(issue_res, 'data', []) or []
    issues_map: Dict[str, dict] = {r['id']: r for r in issue_rows if 'id' in r}
    if not issues_map:
        return IssueGraphResponse(nodes=[], edges=[], topological_order=[], cycle_detected=False, total=0)
    # Fetch dependencies and filter to included issues
    dep_res = supabase.table("issue_dependencies").select("issue_id,depends_on_id").execute()
    dep_rows = getattr(dep_res, 'data', []) or []
    edges: List[IssueGraphEdge] = []
    adj: Dict[str, Set[str]] = {iid: set() for iid in issues_map.keys()}
    incoming: Dict[str, Set[str]] = {iid: set() for iid in issues_map.keys()}
    for r in dep_rows:
        a = r.get('issue_id')
        b = r.get('depends_on_id')
        if a in issues_map and b in issues_map:
            adj[a].add(b)
            incoming[b].add(a)
            edges.append(IssueGraphEdge(source=a, target=b))
    # Topological sort (Kahn). Note: edges direction issue->depends_on so for ordering we need reverse (depends first).
    from collections import deque
    indeg = {nid: len(adj[nid]) for nid in adj}  # number of dependencies each issue has
    # Actually indeg currently out-degree; adjust: we want indegree as count of dependents or dependencies? For topo we treat dependency edges as source->target. indegree[target]++
    indegree: Dict[str, int] = {nid: 0 for nid in adj}
    for s, targets in adj.items():
        for t in targets:
            indegree[t] += 1
    q = deque([n for n, d in indegree.items() if d == 0])
    topo: List[str] = []
    indegree_mut = indegree.copy()
    while q:
        n = q.popleft()
        topo.append(n)
        for t in adj.get(n, set()):
            indegree_mut[t] -= 1
            if indegree_mut[t] == 0:
                q.append(t)
    cycle_detected = len(topo) != len(adj)
    topo_out: Optional[List[UUID]] = None if cycle_detected else [UUID(x) for x in topo]
    # Build nodes list
    nodes: List[IssueGraphNode] = []
    for iid, data in issues_map.items():
        nodes.append(IssueGraphNode(
            id=data['id'],
            issue_key=data['issue_key'],
            title=data.get('title') or '',
            status=data.get('status'),
            priority=data.get('priority'),
            out_degree=len(adj.get(iid, set())),
            in_degree=len(incoming.get(iid, set())),
            story_points=data.get('story_points'),
            sprint_id=data.get('sprint_id')
        ))
    return IssueGraphResponse(nodes=nodes, edges=edges, topological_order=topo_out, cycle_detected=cycle_detected, total=len(nodes))

# ---------------- Reorder Backlog (assign sequential backlog_rank) -----------------
class ReorderIssuesRequest(BaseModel):
    issue_ids: List[UUID]

@router.post("/reorder")
def reorder_issues(body: ReorderIssuesRequest, current_user: UserModel = Depends(get_current_user)):
    if not body.issue_ids:
        return {"success": True, "updated": 0}
    # Fetch existing ranks for these issues (ownership enforced)
    ids_str = [str(i) for i in body.issue_ids]
    existing_res = supabase.table("issues").select("id").in_("id", ids_str).eq("owner_id", str(current_user.id)).execute()
    existing_rows = getattr(existing_res, 'data', []) or []
    existing_ids = {r['id'] for r in existing_rows if 'id' in r}
    order = [iid for iid in ids_str if iid in existing_ids]
    updates = []
    rank = 1
    for iid in order:
        updates.append({"id": iid, "backlog_rank": rank})
        rank += 1
    if updates:
        # Upsert updates (chunk if large)
        CHUNK = 200
        for i in range(0, len(updates), CHUNK):
            supabase.table("issues").upsert(updates[i:i+CHUNK]).execute()
    return {"success": True, "updated": len(updates)}
