from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from app.core.dependencies import get_current_user, UserModel, supabase
from datetime import datetime
from app.agents import epic_decomposer

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

def _validate_and_fetch_epic(epic_id: UUID, user_id: UUID) -> Dict[str, Any]:
    epic_res = supabase.table("issues").select("id,title,type,description,project_id,workspace_id").eq("id", str(epic_id)).eq("owner_id", str(user_id)).maybe_single().execute()
    epic = getattr(epic_res, 'data', None)
    if not epic:
        raise HTTPException(status_code=404, detail="Epic not found")
    if (epic.get('type') or '').lower() != 'epic':
        raise HTTPException(status_code=400, detail="Not an epic")
    return epic


from typing import Tuple


def _normalize_stories(raw_stories: List[Dict[str, Any]]) -> Tuple[List[GeneratedStory], List[str]]:
    warnings: List[str] = []
    cleaned: List[GeneratedStory] = []
    seen_titles = set()
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
    if len(cleaned) > MAX_STORIES:
        warnings.append(f"Truncated stories to {MAX_STORIES} (received {len(cleaned)})")
        cleaned = cleaned[:MAX_STORIES]
    return cleaned, warnings


def _create_child_issue(epic: Dict[str, Any], story: GeneratedStory, owner_id: UUID) -> Optional[UUID]:
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
            "search_blob": '\n'.join([story.title] + story.acceptance_criteria)[:18000]
        }
        ins = supabase.table("issues").insert(payload).execute()
        data = getattr(ins, 'data', None)
        if data:
            return UUID(data[0]['id'])
    except Exception:
        return None
    return None


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

    if body.dry_run:
        # Run model decomposition
        result = await epic_decomposer.decompose_epic(epic_description=epic.get('description') or epic.get('title') or 'Epic')
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
        stories, norm_warnings = _normalize_stories(candidate_stories)
        warnings.extend(norm_warnings)
    else:
        # Using client-provided (edited) stories for commit
        if not body.stories:
            raise HTTPException(status_code=400, detail="stories required when commit=true")
        stories, norm_warnings = _normalize_stories(body.stories)
        warnings.extend(norm_warnings)

    created_ids: List[UUID] = []
    committed = False
    if body.commit and not body.dry_run:
        for st in stories:
            cid = _create_child_issue(epic, st, current_user.id)
            if cid:
                created_ids.append(cid)
        committed = True

    return EpicDecomposeResponse(
        epic_id=body.epic_id,
        stories=stories,
        warnings=warnings,
        model=model_used,
        stub=stub,
        dry_run=body.dry_run,
        committed=committed,
        created_issue_ids=created_ids or None,
        run_id=None,  # reserved for future agent_runs integration
        generated_at=datetime.utcnow().isoformat() + 'Z'
    )
