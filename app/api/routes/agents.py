from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from app.core.dependencies import get_current_user, UserModel, supabase, get_team_context, TeamContext
from datetime import datetime, date
import time
from app.agents import epic_decomposer
from app.core.config import settings
try:
    from app.services.tokenizer import estimate_tokens
except Exception:  # pragma: no cover
    def estimate_tokens(text: str, model: str | None = None) -> int:  # type: ignore
        return max(1, len(text)//4)
from app.models.agent_runs import AgentRunCreate, AgentRunUpdate, AgentRunItemCreate
try:
    from app.services.embeddings import embed_texts, upsert_issue_embeddings, compute_quality_score, fetch_issue_embeddings, cosine_sim
except Exception:  # pragma: no cover
    def embed_texts(texts): return []  # type: ignore
    def upsert_issue_embeddings(pairs): return None  # type: ignore
    def compute_quality_score(distinctness, criteria_density, warning_penalty, structure_valid): return 0.0  # type: ignore
    def fetch_issue_embeddings(issue_ids): return {}  # type: ignore
    def cosine_sim(a, b): return 0.0  # type: ignore

MAX_STORIES = 10
DAILY_REGEN_LIMIT = 100  # per user per UTC day
REGEN_TOKEN_COST_USD = 0.000002  # same heuristic per token
MODEL_NAME = "gemini-2.5-flash"
_DRY_RUN_CACHE: dict[str, tuple[float, dict]] = {}

router = APIRouter(prefix="/api/agents", tags=["Agents"], dependencies=[Depends(get_current_user)])

class EpicDecomposeRequest(BaseModel):
    epic_id: str
    max_stories: int = Field(6, ge=1, le=MAX_STORIES)
    # Back-compat: client may still send these; ignored for logic.
    dry_run: Optional[bool] = None
    commit: Optional[bool] = None
    # Optional guidance to refine outputs without expanding scope
    user_prompt: Optional[str] = None
    # When committing, client sends stories; otherwise it's a generate.
    stories: Optional[List[Dict[str, Any]]] = None

class GeneratedStory(BaseModel):
    title: str
    acceptance_criteria: List[str]

class EpicDecomposeResponse(BaseModel):
    epic_id: UUID
    stories: List[GeneratedStory]
    warnings: List[str]
    model: str
    stub: bool
    dry_run: bool  # kept for UI but no gating
    committed: bool
    created_issue_ids: Optional[List[UUID]] = None
    run_id: Optional[UUID] = None
    generated_at: str
    quality_score: Optional[float] = None
    warnings_count: Optional[int] = None
    duplicate_matches: Optional[List[Dict[str, Any]]] = None
    epic_issue_key: Optional[str] = None


class AgentRunSummary(BaseModel):
    id: UUID
    agent_type: str
    action: str
    epic_id: Optional[UUID]
    status: str  # kept for observability but no gating
    started_at: str
    ended_at: Optional[str] = None
    model: Optional[str] = None
    stub: Optional[bool] = None
    created_issue_count: int = 0
    # Observability
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    cost_usd_estimate: Optional[float] = None
    quality_score: Optional[float] = None
    warnings_count: Optional[int] = None
    prompt_version: Optional[str] = None
    regen_count: Optional[int] = None


class AgentRunDetail(AgentRunSummary):
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_issue_ids: Optional[List[UUID]] = None
    duplicate_matches: Optional[List[Dict[str, Any]]] = None
    remaining_regenerations_today: Optional[int] = None


# Prompt variant schemas removed as feature is deprecated.


class AgentRunItem(BaseModel):
    id: UUID
    run_id: UUID
    item_index: int
    title: str
    acceptance_criteria: List[str] = []
    created_issue_id: Optional[UUID] = None
    status: str
    regen_count: Optional[int] = None
    last_feedback: Optional[str] = None


"""
All regenerate/estimate/feedback endpoints removed as per simplification.
"""

def _validate_and_fetch_epic(epic_ref: str, user_id: UUID) -> tuple[Dict[str, Any], UUID]:
    normalized = (epic_ref or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Epic identifier is required")

    epic: Optional[Dict[str, Any]] = None
    epic_uuid: Optional[UUID] = None

    # First try to resolve as UUID
    try:
        candidate_uuid = UUID(normalized)
        epic_res = (
            supabase
            .table("issues")
            .select("id,title,type,description,project_id,workspace_id,issue_key")
            .eq("id", str(candidate_uuid))
            .eq("owner_id", str(user_id))
            .maybe_single()
            .execute()
        )
        epic = getattr(epic_res, "data", None)
        if epic:
            epic_uuid = candidate_uuid
    except (ValueError, TypeError):
        epic = None
        epic_uuid = None

    # Fall back to issue key lookup (e.g. "OR-2")
    if not epic:
        key_normalized = normalized.upper()
        epic_res = (
            supabase
            .table("issues")
            .select("id,title,type,description,project_id,workspace_id,issue_key")
            .eq("issue_key", key_normalized)
            .eq("owner_id", str(user_id))
            .maybe_single()
            .execute()
        )
        epic = getattr(epic_res, "data", None)
        if epic:
            try:
                epic_uuid = UUID(str(epic.get("id")))
            except Exception as exc:  # pragma: no cover - unexpected data shape
                raise HTTPException(status_code=500, detail="Epic record missing valid ID") from exc

    if not epic or not epic_uuid:
        raise HTTPException(status_code=404, detail="Epic not found")
    if (epic.get("type") or "").lower() != "epic":
        raise HTTPException(status_code=400, detail="Not an epic")

    return epic, epic_uuid


from typing import Tuple


def _normalize_stories(raw_stories: List[Dict[str, Any]], limit: int) -> Tuple[List[GeneratedStory], List[str]]:
    """Legacy wrapper retained for compatibility. Core normalization now handled in agent layer.
    We only enforce max length and convert dict->GeneratedStory."""
    out: List[GeneratedStory] = []
    for st in raw_stories[: limit]:
        title = (st.get('title') or '').strip()
        ac = st.get('acceptance_criteria') or []
        if isinstance(ac, str):
            ac = [ac]
        if not isinstance(ac, list):
            ac = []
        cleaned_ac = []
        for item in ac:
            if isinstance(item, str):
                cleaned_ac.append(item.strip())
        if title:
            out.append(GeneratedStory(title=title, acceptance_criteria=cleaned_ac))
    return out, []


def _create_child_issue(epic: Dict[str, Any], story: GeneratedStory, owner_id: UUID, run_id: Optional[UUID]) -> UUID:
    if not supabase:
        raise HTTPException(status_code=500, detail="Issue service unavailable")

    # Determine next sequence in project (reuse simplified logic)
    try:
        if epic.get('project_id'):
            count_res = supabase.table("issues").select("id").eq("project_id", epic['project_id']).execute()
        else:
            count_res = supabase.table("issues").select("id").eq("owner_id", str(owner_id)).execute()
        seq = (len(getattr(count_res, 'data', []) or []) + 1)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to inspect existing issues: {exc}")

    base_issue_key = f"CH-{seq}"
    payload = {
        "id": str(uuid4()),
        "issue_key": base_issue_key,
        "title": story.title,
        "status": "todo",
        "type": "story",
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

    def _apply_unique_suffix(attempt: int) -> None:
        if attempt == 0:
            payload["issue_key"] = base_issue_key
        else:
            payload["issue_key"] = f"{base_issue_key}-{uuid4().hex[:4].upper()}"

    attempts = 0
    last_error: Optional[Any] = None
    while attempts < 3:
        _apply_unique_suffix(attempts)
        try:
            ins = supabase.table("issues").insert(payload).execute()
        except Exception as exc:
            last_error = exc
            attempts += 1
            continue

        error = getattr(ins, 'error', None)
        if error:
            last_error = error
            attempts += 1
            continue

        data = getattr(ins, 'data', None) or []
        if data and data[0].get('id'):
            return UUID(str(data[0]['id']))
        # Some Supabase configurations return no data for insert. Since we control the ID, assume success.
        return UUID(payload["id"])

    detail = f"Failed to create story: {last_error}" if last_error else "Failed to create story"
    raise HTTPException(status_code=502, detail=detail)


def _compute_quality_and_duplicates(epic_id: str, stories: List[GeneratedStory]) -> tuple[Optional[float], int, List[Dict[str, Any]]]:
    """Re-run semantic duplicate detection vs existing children and compute quality score.

    Returns (quality_score, warnings_count (duplicates only), duplicate_matches)
    Note: We only warn on duplicates here; acceptance criteria linting already done during generation.
    """
    try:
        existing_children, _ = epic_decomposer._fetch_existing_children(epic_id)  # type: ignore[attr-defined]
    except Exception:
        existing_children = []
    duplicate_matches: List[Dict[str, Any]] = []
    if stories:
        try:
            existing_texts = []
            for c in existing_children:
                title_part = (c.get('title') or '')
                ac_items_raw = c.get('acceptance_criteria') or []
                ac_texts: List[str] = []
                if isinstance(ac_items_raw, list):
                    for ac in ac_items_raw[:6]:
                        if isinstance(ac, dict):
                            t = ac.get('text')
                            if isinstance(t, str) and t.strip():
                                ac_texts.append(t.strip())
                        elif isinstance(ac, str) and ac.strip():
                            ac_texts.append(ac.strip())
                combined = title_part + ('\n' + '\n'.join(ac_texts) if ac_texts else '')
                existing_texts.append((c.get('id'), combined))
            existing_vectors = embed_texts([t[1] for t in existing_texts]) if existing_texts else []
            story_vectors = embed_texts([s.title + '\n' + '\n'.join(s.acceptance_criteria[:6]) for s in stories])
            existing_vec_map = [ev.vector for ev in existing_vectors]
            from app.services.embeddings import cosine_sim  # local import to avoid circular earlier
            for idx, sv in enumerate(story_vectors):
                best_sim = 0.0
                best_title = None
                for eidx, ev in enumerate(existing_vec_map):
                    sim = cosine_sim(sv.vector, ev)
                    if sim > best_sim:
                        best_sim = sim
                        best_title = existing_children[eidx].get('title') if eidx < len(existing_children) else None
                if best_sim >= 0.85 and best_title:
                    duplicate_matches.append({
                        "story_index": idx,
                        "story_title": stories[idx].title,
                        "existing_title": best_title,
                        "similarity": round(best_sim, 3)
                    })
        except Exception:
            pass
    total = len(stories)
    dup_count = len(duplicate_matches)
    distinctness = 1 - (dup_count / total) if total else 0
    avg_criteria = (sum(len(s.acceptance_criteria) for s in stories) / total) if total else 0
    criteria_density = min(1.0, avg_criteria / 6.0)
    # For regeneration we don't re-run full lint; warning_penalty only from duplicates here.
    warning_penalty = 1 - min(1.0, (dup_count / total) / 5.0) if total else 1.0
    structure_valid = 1.0
    quality_score = compute_quality_score(distinctness, criteria_density, warning_penalty, structure_valid) if total else None
    return quality_score, dup_count, duplicate_matches


    # (regen/estimate/feedback functionality removed)


def _persist_run(create: AgentRunCreate) -> Optional[UUID]:
    try:
        payload = create.model_dump()
        generated_id = uuid4()
        payload["id"] = str(generated_id)
        payload["user_id"] = str(payload["user_id"])
        if payload.get("epic_id"):
            payload["epic_id"] = str(payload["epic_id"])
        if payload.get("team_id"):
            payload["team_id"] = str(payload["team_id"])
        ins = supabase.table("agent_runs").insert(payload).execute()
        data = getattr(ins, 'data', None) or []
        if data and data[0].get('id'):
            return UUID(str(data[0]['id']))
        # Fallback to the generated UUID when Supabase returns no data
        return generated_id
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
                "regen_count": 0,
            })
        if rows:
            supabase.table("agent_run_items").insert(rows).execute()
    except Exception:
        pass


# (prompt variant helpers and endpoints removed)


@router.post("/epic/decompose", response_model=EpicDecomposeResponse)
async def epic_decompose(body: EpicDecomposeRequest, current_user: UserModel = Depends(get_current_user), ctx: TeamContext = Depends(get_team_context)):
    epic, epic_uuid = _validate_and_fetch_epic(body.epic_id, current_user.id)

    # Simplified: if stories provided -> commit; else -> generate.

    model_used = "stub"
    stub = True
    warnings: List[str] = []
    stories: List[GeneratedStory] = []

    # Enforce per-team daily run limit (reuse DAILY_REGEN_LIMIT as placeholder team limit)
    from datetime import timezone, timedelta
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    team_day_usage = supabase.table('agent_runs').select('id').eq('team_id', str(ctx.team_id)) \
        .gte('started_at', day_start.isoformat()).lt('started_at', day_end.isoformat()).execute()
    team_runs_today = len(getattr(team_day_usage, 'data', []) or [])
    team_limit = settings.TEAM_DAILY_RUN_LIMIT or DAILY_REGEN_LIMIT
    if team_runs_today >= team_limit:
        raise HTTPException(status_code=429, detail='Daily team run limit reached')

    # Create run (always create for audit)
    run_mode = 'run'
    run_id = _persist_run(AgentRunCreate(
        agent_type="epic_decomposer",
        action="epic_decompose",
        mode=run_mode,
        epic_id=epic_uuid,
        user_id=current_user.id,
        team_id=ctx.team_id,
        input={
            "epic_id": str(epic_uuid),
            "epic_ref": body.epic_id,
            "max_stories": body.max_stories,
            "user_prompt": body.user_prompt or None,
        }
    ))

    start_time = time.perf_counter()
    input_token_estimate = 0
    output_token_estimate = 0
    duplicate_matches: List[Dict[str, Any]] = []
    quality_score: Optional[float] = None
    warnings_count: Optional[int] = None
    if not body.stories:
        prompt_source = (epic.get('description') or epic.get('title') or '')
        result = await epic_decomposer.decompose_epic(
            epic_description=prompt_source or 'Epic',
            max_stories=body.max_stories,
            epic_id=str(epic['id']),
            user_prompt=body.user_prompt or None,
        )
        raw = result.get('data') if result.get('success') else None
        input_token_estimate = estimate_tokens(prompt_source, model="gemini-2.5-flash")
        candidate_stories: List[Dict[str, Any]]
        if result.get('success') and raw and isinstance(raw, dict) and isinstance(raw.get('stories'), list):
            model_used = "gemini-2.5-flash"
            stub = False
            candidate_stories = list(raw.get('stories'))  # type: ignore[arg-type]
            warnings.extend(result.get('warnings', []))
            duplicate_matches = result.get('duplicate_matches', []) or []
            quality_score = result.get('quality_score')
            warnings_count = result.get('warnings_count') or len(warnings)
        else:
            base_title = (epic.get('title') or 'Epic').strip()
            candidate_stories = [
                {"title": f"{base_title} Story {i}", "acceptance_criteria": ["Criteria one", "Criteria two"]}
                for i in range(1, body.max_stories + 1)
            ]
            warnings.append("LLM unavailable or invalid JSON; using stub output")
            warnings.extend(result.get('warnings', []))
        candidate_stories = candidate_stories[: body.max_stories]
        stories, _ = _normalize_stories(candidate_stories, body.max_stories)
        joined = '\n'.join([s.title + ' ' + ' '.join(s.acceptance_criteria) for s in stories])
        output_token_estimate = estimate_tokens(joined, model="gemini-2.5-flash")
    else:
        if not body.stories:
            raise HTTPException(status_code=400, detail="stories required for commit")
        trimmed = body.stories[: body.max_stories]
        stories, _ = _normalize_stories(trimmed, body.max_stories)
        # Compute a quality score for provided stories (no duplicate detection yet in commit-only path)
        total = len(stories)
        if total:
            avg_criteria = sum(len(s.acceptance_criteria) for s in stories) / total
            criteria_density = min(1.0, avg_criteria / 6.0)
            warnings_count = len(warnings)
            warning_penalty = 1 - min(1.0, (warnings_count / total) / 5.0 if total else 0)
            distinctness = 1.0  # optimistic; duplicate detection not run in commit path yet
            quality_score = compute_quality_score(distinctness, criteria_density, warning_penalty, 1.0)

    created_ids: List[UUID] = []
    created_story_map: List[tuple[UUID, GeneratedStory]] = []
    committed = False
    if body.stories:
        # Preload existing children to avoid duplicates
        try:
            existing_children, _ = epic_decomposer._fetch_existing_children(str(epic['id']))  # type: ignore[attr-defined]
        except Exception:
            existing_children = []
        existing_titles = { (c.get('title') or '').strip().lower() for c in existing_children }
        for idx, st in enumerate(stories):
            # Skip if duplicate by normalized title
            if st.title.strip().lower() in existing_titles:
                warnings.append(f"skipped duplicate story by title: {st.title}")
                continue
            try:
                cid = _create_child_issue(epic, st, current_user.id, run_id)
            except HTTPException as exc:
                warnings.append(f"failed to create story '{st.title}': {exc.detail}")
                continue
            created_ids.append(cid)
            created_story_map.append((cid, st))
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
        total_ms = int((time.perf_counter() - start_time) * 1000)
        total_tokens = (input_token_estimate or 0) + (output_token_estimate or 0)
        cost_estimate = round(total_tokens * 0.000002, 6) if total_tokens else None
        # If not set above (dry_run path), derive simple quality if possible
        if quality_score is None:
            total = len(stories)
            if total:
                avg_criteria = sum(len(s.acceptance_criteria) for s in stories) / total
                criteria_density = min(1.0, avg_criteria / 6.0)
                wc = warnings_count if warnings_count is not None else len(warnings)
                warning_penalty = 1 - min(1.0, (wc / total) / 5.0 if total else 0)
                distinctness = 1.0
                quality_score = compute_quality_score(distinctness, criteria_density, warning_penalty, 1.0 if stories else 0.0)
        if warnings_count is None:
            warnings_count = len(warnings)
        _update_run(run_id, AgentRunUpdate(
            status="succeeded",
            output={
                "stories": [
                    {"title": s.title, "acceptance_criteria": s.acceptance_criteria} for s in stories
                ],
                "warnings": warnings,
                "model": model_used,
                "stub": stub,
                "quality_score": quality_score,
                "warnings_count": warnings_count,
                "duplicate_matches": duplicate_matches if duplicate_matches else None,
            },
            created_issue_ids=created_ids or None,
            ended_at=datetime.utcnow(),
            input_tokens=input_token_estimate or None,
            output_tokens=output_token_estimate or None,
            total_tokens=total_tokens or None,
            latency_ms=total_ms,
            cost_usd_estimate=cost_estimate,
            quality_score=quality_score,
            warnings_count=warnings_count,
        ))

    return EpicDecomposeResponse(
        epic_id=epic_uuid,
        stories=stories,
        warnings=warnings,
        model=model_used,
        stub=stub,
        dry_run=not bool(body.stories),
        committed=committed,
        created_issue_ids=created_ids or None,
        run_id=run_id,
        generated_at=datetime.utcnow().isoformat() + 'Z',
        quality_score=quality_score,
        warnings_count=warnings_count,
        duplicate_matches=duplicate_matches or None,
        epic_issue_key=(epic.get("issue_key") if isinstance(epic, dict) else None),
    )


@router.get("/runs", response_model=List[AgentRunSummary])
async def list_agent_runs(
    epic_id: Optional[UUID] = None,
    limit: int = 20,
    current_user: UserModel = Depends(get_current_user),
    ctx: TeamContext = Depends(get_team_context),
):
    if limit > 50:
        limit = 50
    q = supabase.table("agent_runs").select("*").eq("user_id", str(current_user.id)).eq("team_id", str(ctx.team_id)).order("started_at", desc=True).limit(limit)
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
        # Backfill total_tokens if missing
        total_tokens = r.get('total_tokens')
        if total_tokens is None:
            it = r.get('input_tokens') or 0
            ot = r.get('output_tokens') or 0
            total_tokens = (it + ot) or None
        summaries.append(AgentRunSummary(
            id=UUID(r['id']),
            agent_type=r.get('agent_type'),
            action=r.get('action'),
            epic_id=UUID(r['epic_id']) if r.get('epic_id') else None,
            status=r.get('status'),
            started_at=r.get('started_at'),
            ended_at=r.get('ended_at'),
            model=model_name,
            stub=stub_flag,
            created_issue_count=len(created_ids) if isinstance(created_ids, list) else 0,
            input_tokens=r.get('input_tokens'),
            output_tokens=r.get('output_tokens'),
            total_tokens=total_tokens,
            latency_ms=r.get('latency_ms'),
            cost_usd_estimate=r.get('cost_usd_estimate'),
            quality_score=r.get('quality_score') or (output.get('quality_score') if isinstance(output, dict) else None),
            warnings_count=r.get('warnings_count') or (output.get('warnings_count') if isinstance(output, dict) else None),
            prompt_version=r.get('prompt_version'),
            regen_count=output.get('regen_count') if isinstance(output, dict) else None,
        ))
    return summaries


@router.get("/runs/{run_id}", response_model=AgentRunDetail)
async def get_agent_run(run_id: UUID, current_user: UserModel = Depends(get_current_user), ctx: TeamContext = Depends(get_team_context)):
    res = supabase.table("agent_runs").select("*").eq("id", str(run_id)).eq("user_id", str(current_user.id)).eq("team_id", str(ctx.team_id)).maybe_single().execute()
    row = getattr(res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    output = row.get('output') or {}
    model_name = output.get('model') if isinstance(output, dict) else None
    stub_flag = output.get('stub') if isinstance(output, dict) else None
    created_ids = row.get('created_issue_ids') or []
    # Backfill total tokens if absent
    total_tokens = row.get('total_tokens')
    if total_tokens is None:
        it = row.get('input_tokens') or 0
        ot = row.get('output_tokens') or 0
        total_tokens = (it + ot) or None
    # Remaining regenerations today
    from datetime import timezone, timedelta
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    usage_res = supabase.table('agent_runs').select('output,started_at').eq('user_id', str(current_user.id)).eq('team_id', str(ctx.team_id)) \
        .gte('started_at', day_start.isoformat()).lt('started_at', day_end.isoformat()).execute()
    daily_rows = getattr(usage_res, 'data', []) or []
    total_regens_today = 0
    for dr in daily_rows:
        o = dr.get('output') or {}
        if isinstance(o, dict):
            rc = o.get('regen_count') or 0
            if isinstance(rc, int):
                total_regens_today += rc
    remaining_regens = max(0, DAILY_REGEN_LIMIT - total_regens_today)

    return AgentRunDetail(
        id=UUID(row['id']),
        agent_type=row.get('agent_type'),
        action=row.get('action'),
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
        input_tokens=row.get('input_tokens'),
        output_tokens=row.get('output_tokens'),
        total_tokens=total_tokens,
        latency_ms=row.get('latency_ms'),
        cost_usd_estimate=row.get('cost_usd_estimate'),
        quality_score=row.get('quality_score') or (output.get('quality_score') if isinstance(output, dict) else None),
        warnings_count=row.get('warnings_count') or (output.get('warnings_count') if isinstance(output, dict) else None),
        duplicate_matches=output.get('duplicate_matches') if isinstance(output, dict) else None,
        prompt_version=row.get('prompt_version'),
        regen_count=output.get('regen_count') if isinstance(output, dict) else None,
        remaining_regenerations_today=remaining_regens,
    )


    # (feedback summary endpoint removed)


class FeedbackAggregateResponse(BaseModel):
    days: int
    total_runs: int
    total_stories: int
    feedback_items: int
    avg_rating: Optional[float] = None
    avg_edit_distance_title: Optional[float] = None
    avg_edit_distance_criteria: Optional[float] = None
    rating_distribution: Dict[str, int] = {}
    avg_criteria_count: Optional[float] = None
    avg_story_length_chars: Optional[float] = None


class TeamQuotaResponse(BaseModel):
    team_id: UUID
    daily_runs_used: int
    daily_runs_limit: int
    daily_runs_remaining: int
    tokens_30d_used: Optional[int] = None
    tokens_30d_limit: Optional[int] = None


@router.get("/team_quota", response_model=TeamQuotaResponse)
async def get_team_quota(ctx: TeamContext = Depends(get_team_context)):
    # Daily runs
    from datetime import timezone, timedelta
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    day_res = supabase.table('agent_runs').select('id').eq('team_id', str(ctx.team_id)) \
        .gte('started_at', day_start.isoformat()).lt('started_at', day_end.isoformat()).execute()
    used = len(getattr(day_res, 'data', []) or [])
    limit = settings.TEAM_DAILY_RUN_LIMIT
    remaining = max(0, (limit or 0) - used)
    # 30d tokens
    window_start = now_utc - timedelta(days=30)
    tok_res = supabase.table('agent_runs').select('input_tokens,output_tokens').eq('team_id', str(ctx.team_id)) \
        .gte('started_at', window_start.isoformat()).execute()
    rows = getattr(tok_res, 'data', []) or []
    tokens_used = 0
    for r in rows:
        it = r.get('input_tokens') or 0
        ot = r.get('output_tokens') or 0
        tokens_used += (it or 0) + (ot or 0)
    return TeamQuotaResponse(
        team_id=ctx.team_id,
        daily_runs_used=used,
        daily_runs_limit=limit,
        daily_runs_remaining=remaining,
        tokens_30d_used=tokens_used,
        tokens_30d_limit=settings.TEAM_30D_TOKEN_LIMIT,
    )


# Simplified workflow no longer needs clone_to_dry_run endpoint.


@router.get("/feedback/metrics")
async def aggregate_feedback_metrics():
    raise HTTPException(status_code=410, detail="Feedback metrics removed")


# Prompt variant metrics/stats endpoints removed entirely.


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
            regen_count=r.get('regen_count') or (r.get('metadata', {}).get('regen_count') if isinstance(r.get('metadata'), dict) else None),
            last_feedback=r.get('last_feedback') or (r.get('metadata', {}).get('last_feedback') if isinstance(r.get('metadata'), dict) else None),
        ))
    return items


class CommitOneRequest(BaseModel):
    title: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None


class CommitOneResponse(BaseModel):
    run_id: UUID
    item_id: UUID
    created_issue_id: Optional[UUID]
    status: str
    title: str
    acceptance_criteria: List[str]


class RegenerateOneRequest(BaseModel):
    feedback: Optional[str] = None


class RegenerateOneResponse(BaseModel):
    run_id: UUID
    item_id: UUID
    status: str
    title: str
    acceptance_criteria: List[str]
    warnings: List[str] = []
    duplicate_matches: Optional[List[Dict[str, Any]]] = None


def _sanitize_feedback(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = (text or '').strip()
    if not t:
        return None
    # strip HTML-ish tags
    t = t.replace('<', ' ').replace('>', ' ')
    # collapse whitespace and cap length
    t = ' '.join(t.split())[:800]
    return t


def _get_run_and_item_or_404(run_id: UUID, item_id: UUID, user_id: UUID) -> tuple[Dict[str, Any], Dict[str, Any]]:
    run_res = supabase.table("agent_runs").select("*").eq("id", str(run_id)).eq("user_id", str(user_id)).maybe_single().execute()
    run_row = getattr(run_res, 'data', None)
    if not run_row:
        raise HTTPException(status_code=404, detail="Run not found")
    item_res = supabase.table("agent_run_items").select("*").eq("id", str(item_id)).eq("run_id", str(run_id)).maybe_single().execute()
    item_row = getattr(item_res, 'data', None)
    if not item_row:
        raise HTTPException(status_code=404, detail="Run item not found")
    return run_row, item_row


@router.post("/runs/{run_id}/items/{item_id}/commit", response_model=CommitOneResponse)
async def commit_one_story(run_id: UUID, item_id: UUID, body: CommitOneRequest, current_user: UserModel = Depends(get_current_user)):
    run_row, item_row = _get_run_and_item_or_404(run_id, item_id, current_user.id)
    if item_row.get('created_issue_id'):
        # idempotent
        return CommitOneResponse(
            run_id=run_id,
            item_id=item_id,
            created_issue_id=UUID(item_row['created_issue_id']),
            status=item_row.get('status') or 'created',
            title=item_row.get('title') or '',
            acceptance_criteria=item_row.get('acceptance_criteria') or [],
        )

    epic_id = run_row.get('epic_id')
    if not epic_id:
        raise HTTPException(status_code=400, detail="Run missing epic_id")
    epic_res = supabase.table("issues").select("id,title,type,description,project_id,workspace_id,owner_id").eq("id", str(epic_id)).maybe_single().execute()
    epic = getattr(epic_res, 'data', None)
    if not epic or str(epic.get('owner_id')) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Epic not accessible")

    # Apply optional edits
    new_title = (body.title or item_row.get('title') or '').strip()
    new_ac = body.acceptance_criteria if isinstance(body.acceptance_criteria, list) else (item_row.get('acceptance_criteria') or [])
    new_ac = [str(x).strip() for x in new_ac if str(x).strip()]
    if not new_title:
        raise HTTPException(status_code=400, detail="Title required")

    st = GeneratedStory(title=new_title[:160], acceptance_criteria=new_ac[:12])
    created_id = _create_child_issue(epic, st, current_user.id, run_id)
    try:
        supabase.table("agent_run_items").update({
            "title": st.title,
            "acceptance_criteria": st.acceptance_criteria,
            "created_issue_id": str(created_id),
            "status": "created",
        }).eq("id", str(item_id)).execute()
    except Exception:
        pass
    # append to run.created_issue_ids
    try:
        existing_ids = run_row.get('created_issue_ids') or []
        existing_ids = existing_ids if isinstance(existing_ids, list) else []
        updated_ids = [*(existing_ids or []), str(created_id)]
        supabase.table("agent_runs").update({"created_issue_ids": updated_ids}).eq("id", str(run_id)).execute()
    except Exception:
        pass

    return CommitOneResponse(
        run_id=run_id,
        item_id=item_id,
        created_issue_id=created_id,
        status="created",
        title=st.title,
        acceptance_criteria=st.acceptance_criteria,
    )


@router.post("/runs/{run_id}/items/{item_id}/regenerate", response_model=RegenerateOneResponse)
async def regenerate_one_story(run_id: UUID, item_id: UUID, body: RegenerateOneRequest, current_user: UserModel = Depends(get_current_user)):
    run_row, item_row = _get_run_and_item_or_404(run_id, item_id, current_user.id)
    if item_row.get('created_issue_id'):
        raise HTTPException(status_code=400, detail="Cannot regenerate a committed story")
    # Per-item regen cap (3)
    item_regens = int(item_row.get('regen_count') or 0)
    if item_regens >= 3:
        raise HTTPException(status_code=429, detail='Per-item regeneration limit reached')

    # Daily limit check based on agent_runs output.regen_count aggregated
    from datetime import timezone, timedelta
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    usage_res = supabase.table('agent_runs').select('output,started_at').eq('user_id', str(current_user.id)) \
        .gte('started_at', day_start.isoformat()).lt('started_at', day_end.isoformat()).execute()
    rows = getattr(usage_res, 'data', []) or []
    total_regens_today = 0
    for r in rows:
        o = r.get('output') or {}
        if isinstance(o, dict):
            total_regens_today += int(o.get('regen_count') or 0)
    if total_regens_today >= DAILY_REGEN_LIMIT:
        raise HTTPException(status_code=429, detail='Daily regeneration limit reached')

    epic_id = run_row.get('epic_id')
    if not epic_id:
        raise HTTPException(status_code=400, detail="Run missing epic_id")
    epic_res = supabase.table("issues").select("id,title,description").eq("id", str(epic_id)).maybe_single().execute()
    epic = getattr(epic_res, 'data', None)
    if not epic:
        raise HTTPException(status_code=404, detail="Epic not found")

    feedback = _sanitize_feedback(body.feedback)
    if not feedback:
        raise HTTPException(status_code=400, detail="Feedback is required for regeneration")
    # Build guidance combining feedback
    prompt_source = (epic.get('description') or epic.get('title') or '')
    original_story = {
        "title": item_row.get('title') or '',
        "acceptance_criteria": item_row.get('acceptance_criteria') or [],
    }

    agent_result = await epic_decomposer.regenerate_story(
        epic_description=prompt_source or 'Epic',
        epic_id=str(epic['id']),
        original_story=original_story,
        feedback=feedback,
    )
    warnings: List[str] = []
    duplicate_matches: List[Dict[str, Any]] = []
    if not agent_result.get('success'):
        raise HTTPException(status_code=502, detail=f"Regeneration failed: {agent_result.get('error')}")
    data = agent_result.get('data') or {}
    stories = data.get('stories') or []
    if not stories:
        raise HTTPException(status_code=502, detail="Regeneration returned empty result")
    story = stories[0]
    new_title = (story.get('title') or '').strip()[:160]
    new_ac_raw = story.get('acceptance_criteria') or []
    new_ac: List[str] = []
    if isinstance(new_ac_raw, list):
        for x in new_ac_raw[:12]:
            if isinstance(x, str) and x.strip():
                new_ac.append(x.strip())
    warnings.extend(agent_result.get('warnings', []) or [])
    duplicate_matches = agent_result.get('duplicate_matches', []) or []

    # Update the item with regenerated content
    try:
        # increment regen_count (either column or metadata fallback)
        patch: Dict[str, Any] = {
            "title": new_title,
            "acceptance_criteria": new_ac,
            "status": "regenerated",
        }
        current_regens = item_row.get('regen_count') or 0
        patch["regen_count"] = current_regens + 1
        if feedback:
            patch["last_feedback"] = feedback
        supabase.table("agent_run_items").update(patch).eq("id", str(item_id)).execute()
    except Exception:
        pass

    # Also increment regen_count on run.output for daily accounting
    try:
        output = run_row.get('output') or {}
        if not isinstance(output, dict):
            output = {}
        output['regen_count'] = int(output.get('regen_count') or 0) + 1
        supabase.table('agent_runs').update({"output": output}).eq("id", str(run_id)).execute()
    except Exception:
        pass

    return RegenerateOneResponse(
        run_id=run_id,
        item_id=item_id,
        status="regenerated",
        title=new_title,
        acceptance_criteria=new_ac,
        warnings=warnings,
        duplicate_matches=duplicate_matches or None,
    )
