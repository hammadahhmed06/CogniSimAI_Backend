from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from app.core.dependencies import get_current_user, UserModel, supabase
from datetime import datetime
from app.agents import epic_decomposer
from app.models.agent_runs import AgentRunCreate, AgentRunUpdate, AgentRunItemCreate

MAX_STORIES = 10

router = APIRouter(prefix="/api/agents", tags=["Agents"], dependencies=[Depends(get_current_user)])

class EpicDecomposeRequest(BaseModel):
    epic_id: UUID
    max_stories: int = Field(6, ge=1, le=MAX_STORIES)
    dry_run: bool = True
    commit: bool = False
    stories: Optional[List[Dict[str, Any]]] = None  # allow client-edited stories for commit

class GeneratedStory(BaseModel):
    title: str
    acceptance_criteria: List[str]

class EpicDecomposeResponse(BaseModel):
    epic_id: UUID
    stories: List[GeneratedStory]
    warnings: List[str]
    model: str
    stub: bool
    dry_run: bool
    committed: bool
    created_issue_ids: Optional[List[UUID]] = None
    run_id: Optional[UUID] = None
    generated_at: str


class AgentRunSummary(BaseModel):
    id: UUID
    agent_type: str
    action: str
    mode: str
    epic_id: Optional[UUID]
    status: str
    started_at: str
    ended_at: Optional[str] = None
    model: Optional[str] = None
    stub: Optional[bool] = None
    created_issue_count: int = 0


class AgentRunDetail(AgentRunSummary):
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_issue_ids: Optional[List[UUID]] = None


class AgentRunItem(BaseModel):
    id: UUID
    run_id: UUID
    item_index: int
    title: str
    acceptance_criteria: List[str] = []
    created_issue_id: Optional[UUID] = None
    status: str

def _validate_and_fetch_epic(epic_id: UUID, user_id: UUID) -> Dict[str, Any]:
    epic_res = supabase.table("issues").select("id,title,type,description,project_id,workspace_id").eq("id", str(epic_id)).eq("owner_id", str(user_id)).maybe_single().execute()
    epic = getattr(epic_res, 'data', None)
    if not epic:
        raise HTTPException(status_code=404, detail="Epic not found")
    if (epic.get('type') or '').lower() != 'epic':
        raise HTTPException(status_code=400, detail="Not an epic")
    return epic


from typing import Tuple


def _normalize_stories(raw_stories: List[Dict[str, Any]], limit: int) -> Tuple[List[GeneratedStory], List[str]]:
    warnings: List[str] = []
    cleaned: List[GeneratedStory] = []
    seen_titles = set()
    # Bound limit within 1..MAX_STORIES
    if limit < 1:
        limit = 1
    if limit > MAX_STORIES:
        limit = MAX_STORIES
    for idx, st in enumerate(raw_stories):
        title = (st.get('title') or '').strip()
        if not title:
            warnings.append(f"Story {idx+1} removed: empty title")
            continue
        norm_title_key = title.lower()
        if norm_title_key in seen_titles:
            warnings.append(f"Story {idx+1} removed: duplicate title '{title}'")
            continue
        ac_list = st.get('acceptance_criteria') or []
        if isinstance(ac_list, str):
            ac_list = [ac_list]
        # Flatten newline separated criteria inside each entry
        expanded: List[str] = []
        for ac in ac_list:
            if not isinstance(ac, str):
                continue
            for line in ac.split('\n'):
                t = line.strip()
                if t:
                    expanded.append(t)
        if len(expanded) > 12:
            warnings.append(f"Story '{title}' acceptance criteria truncated to 12 from {len(expanded)}")
            expanded = expanded[:12]
        cleaned.append(GeneratedStory(title=title, acceptance_criteria=expanded))
        seen_titles.add(norm_title_key)
    if len(cleaned) > limit:
        warnings.append(f"Truncated stories to {limit} (received {len(cleaned)})")
        cleaned = cleaned[:limit]
    return cleaned, warnings


def _create_child_issue(epic: Dict[str, Any], story: GeneratedStory, owner_id: UUID, run_id: Optional[UUID]) -> Optional[UUID]:
    try:
        # Determine next sequence in project (reuse simplified logic)
        if epic.get('project_id'):
            count_res = supabase.table("issues").select("id").eq("project_id", epic['project_id']).execute()
        else:
            count_res = supabase.table("issues").select("id").eq("owner_id", str(owner_id)).execute()
        seq = (len(getattr(count_res, 'data', []) or []) + 1)
        issue_key = f"CH-{seq}"  # Simplified; future: project key or epic derived prefix
        payload = {
            "id": str(uuid4()),
            "issue_key": issue_key,
            "title": story.title,
            "status": "todo",
            "type": "task",
            "project_id": epic.get('project_id'),
            "workspace_id": epic.get('workspace_id'),
            "description": None,
            "epic_id": str(epic['id']),
            "acceptance_criteria": [{"text": c, "done": False} for c in story.acceptance_criteria],
            "owner_id": str(owner_id),
            "search_blob": '\n'.join([story.title] + story.acceptance_criteria)[:18000],
            "origin_run_id": str(run_id) if run_id else None,
            "origin_method": "agent_epic_decompose" if run_id else "manual",
        }
        ins = supabase.table("issues").insert(payload).execute()
        data = getattr(ins, 'data', None)
        if data:
            return UUID(data[0]['id'])
    except Exception:
        return None
    return None


def _persist_run(create: AgentRunCreate) -> Optional[UUID]:
    try:
        payload = create.model_dump()
        payload["id"] = str(uuid4())
        payload["user_id"] = str(payload["user_id"])
        if payload.get("epic_id"):
            payload["epic_id"] = str(payload["epic_id"])
        ins = supabase.table("agent_runs").insert(payload).execute()
        data = getattr(ins, 'data', None) or []
        if data:
            return UUID(data[0]['id'])
    except Exception:
        return None
    return None


def _update_run(run_id: UUID, update: AgentRunUpdate) -> None:
    try:
        patch = update.model_dump(exclude_none=True)
        if patch.get("created_issue_ids"):
            # cast UUID list to strings for supabase
            patch["created_issue_ids"] = [str(x) for x in patch["created_issue_ids"]]
        supabase.table("agent_runs").update(patch).eq("id", str(run_id)).execute()
    except Exception:
        pass


def _persist_run_items(run_id: UUID, stories: List['GeneratedStory']):  # forward ref simple
    try:
        rows = []
        for idx, st in enumerate(stories):
            rows.append({
                "id": str(uuid4()),
                "run_id": str(run_id),
                "item_index": idx,
                "title": st.title,
                "acceptance_criteria": st.acceptance_criteria,
                "status": "proposed",
            })
        if rows:
            supabase.table("agent_run_items").insert(rows).execute()
    except Exception:
        pass


@router.post("/epic/decompose", response_model=EpicDecomposeResponse)
async def epic_decompose(body: EpicDecomposeRequest, current_user: UserModel = Depends(get_current_user)):
    epic = _validate_and_fetch_epic(body.epic_id, current_user.id)

    # If commit without prior generation stories provided -> error
    if body.commit and body.dry_run:
        raise HTTPException(status_code=400, detail="Cannot commit with dry_run=true")

    model_used = "stub"
    stub = True
    warnings: List[str] = []
    stories: List[GeneratedStory] = []

    # Create run (always create for audit even dry run)
    run_mode = 'dry_run' if body.dry_run else 'commit'
    run_id = _persist_run(AgentRunCreate(
        agent_type="epic_decomposer",
        action="epic_decompose",
        mode=run_mode,
        epic_id=body.epic_id,
        user_id=current_user.id,
        input={
            "epic_id": str(body.epic_id),
            "max_stories": body.max_stories,
            "dry_run": body.dry_run,
            "commit": body.commit,
        }
    ))

    if body.dry_run:
        result = await epic_decomposer.decompose_epic(
            epic_description=epic.get('description') or epic.get('title') or 'Epic',
            max_stories=body.max_stories,
            epic_id=str(epic['id'])
        )
        raw = result.get('data') if result.get('success') else None
        candidate_stories: List[Dict[str, Any]]
        if raw and isinstance(raw, dict) and isinstance(raw.get('stories'), list):
            model_used = "gemini-2.0-flash"
            stub = False if not result.get('error') else True
            candidate_stories = list(raw.get('stories'))  # type: ignore[arg-type]
        else:
            base_title = (epic.get('title') or 'Epic').strip()
            candidate_stories = [
                {"title": f"{base_title} Story {i}", "acceptance_criteria": ["Criteria one", "Criteria two"]}
                for i in range(1, body.max_stories + 1)
            ]
            warnings.append("LLM unavailable or invalid JSON; using stub output")
        candidate_stories = candidate_stories[: body.max_stories]
        stories, norm_warnings = _normalize_stories(candidate_stories, body.max_stories)
        warnings.extend(norm_warnings)
    else:
        if not body.stories:
            raise HTTPException(status_code=400, detail="stories required when commit=true")
        trimmed = body.stories[: body.max_stories]
        stories, norm_warnings = _normalize_stories(trimmed, body.max_stories)
        warnings.extend(norm_warnings)

    created_ids: List[UUID] = []
    committed = False
    if body.commit and not body.dry_run:
        for idx, st in enumerate(stories):
            cid = _create_child_issue(epic, st, current_user.id, run_id)
            if cid:
                created_ids.append(cid)
                # update item row if persisted
                if run_id:
                    try:
                        supabase.table("agent_run_items").update({"created_issue_id": str(cid), "status": "created"}).eq("run_id", str(run_id)).eq("item_index", idx).execute()
                    except Exception:
                        pass
        committed = True

    # Persist run items for dry_run visualization (or commit pre-creation)
    if run_id and stories:
        _persist_run_items(run_id, stories)

    # Update run status
    if run_id:
        _update_run(run_id, AgentRunUpdate(
            status="succeeded",
            output={
                "stories": [
                    {"title": s.title, "acceptance_criteria": s.acceptance_criteria} for s in stories
                ],
                "warnings": warnings,
                "model": model_used,
                "stub": stub,
            },
            created_issue_ids=created_ids or None,
            ended_at=datetime.utcnow()
        ))

    return EpicDecomposeResponse(
        epic_id=body.epic_id,
        stories=stories,
        warnings=warnings,
        model=model_used,
        stub=stub,
        dry_run=body.dry_run,
        committed=committed,
        created_issue_ids=created_ids or None,
        run_id=run_id,
        generated_at=datetime.utcnow().isoformat() + 'Z'
    )


@router.get("/runs", response_model=List[AgentRunSummary])
async def list_agent_runs(
    epic_id: Optional[UUID] = None,
    limit: int = 20,
    current_user: UserModel = Depends(get_current_user)
):
    if limit > 50:
        limit = 50
    q = supabase.table("agent_runs").select("*").eq("user_id", str(current_user.id)).order("started_at", desc=True).limit(limit)
    if epic_id:
        q = q.eq("epic_id", str(epic_id))
    res = q.execute()
    rows = getattr(res, 'data', []) or []
    summaries: List[AgentRunSummary] = []
    for r in rows:
        output = r.get('output') or {}
        model_name = output.get('model') if isinstance(output, dict) else None
        stub_flag = output.get('stub') if isinstance(output, dict) else None
        created_ids = r.get('created_issue_ids') or []
        summaries.append(AgentRunSummary(
            id=UUID(r['id']),
            agent_type=r.get('agent_type'),
            action=r.get('action'),
            mode=r.get('mode'),
            epic_id=UUID(r['epic_id']) if r.get('epic_id') else None,
            status=r.get('status'),
            started_at=r.get('started_at'),
            ended_at=r.get('ended_at'),
            model=model_name,
            stub=stub_flag,
            created_issue_count=len(created_ids) if isinstance(created_ids, list) else 0,
        ))
    return summaries


@router.get("/runs/{run_id}", response_model=AgentRunDetail)
async def get_agent_run(run_id: UUID, current_user: UserModel = Depends(get_current_user)):
    res = supabase.table("agent_runs").select("*").eq("id", str(run_id)).eq("user_id", str(current_user.id)).maybe_single().execute()
    row = getattr(res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    output = row.get('output') or {}
    model_name = output.get('model') if isinstance(output, dict) else None
    stub_flag = output.get('stub') if isinstance(output, dict) else None
    created_ids = row.get('created_issue_ids') or []
    return AgentRunDetail(
        id=UUID(row['id']),
        agent_type=row.get('agent_type'),
        action=row.get('action'),
        mode=row.get('mode'),
        epic_id=UUID(row['epic_id']) if row.get('epic_id') else None,
        status=row.get('status'),
        started_at=row.get('started_at'),
        ended_at=row.get('ended_at'),
        model=model_name,
        stub=stub_flag,
        created_issue_count=len(created_ids) if isinstance(created_ids, list) else 0,
        output=output if isinstance(output, dict) else None,
        error=row.get('error'),
        created_issue_ids=[UUID(x) for x in created_ids] if isinstance(created_ids, list) else None,
    )


@router.get("/runs/{run_id}/items", response_model=List[AgentRunItem])
async def list_agent_run_items(run_id: UUID, current_user: UserModel = Depends(get_current_user)):
    # Ensure run belongs to user
    res = supabase.table("agent_runs").select("id").eq("id", str(run_id)).eq("user_id", str(current_user.id)).maybe_single().execute()
    if not getattr(res, 'data', None):
        raise HTTPException(status_code=404, detail="Run not found")
    items_res = supabase.table("agent_run_items").select("*").eq("run_id", str(run_id)).order("item_index", desc=False).execute()
    rows = getattr(items_res, 'data', []) or []
    items: List[AgentRunItem] = []
    for r in rows:
        items.append(AgentRunItem(
            id=UUID(r['id']),
            run_id=run_id,
            item_index=r.get('item_index'),
            title=r.get('title'),
            acceptance_criteria=r.get('acceptance_criteria') or [],
            created_issue_id=UUID(r['created_issue_id']) if r.get('created_issue_id') else None,
            status=r.get('status') or 'proposed',
        ))
    return items
