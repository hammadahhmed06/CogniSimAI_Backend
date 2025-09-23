from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from app.core.dependencies import get_current_user, UserModel, supabase, get_team_context, TeamContext, team_role_required
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

router = APIRouter(prefix="/api/agents", tags=["Agents"], dependencies=[Depends(get_current_user)])

class EpicDecomposeRequest(BaseModel):
    epic_id: UUID
    max_stories: int = Field(6, ge=1, le=MAX_STORIES)
    dry_run: bool = True
    commit: bool = False
    stories: Optional[List[Dict[str, Any]]] = None  # allow client-edited stories for commit
    # Phase 5: allow client to specify desired prompt variant (optional). If omitted server picks default.
    prompt_variant_id: Optional[UUID] = None

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
    quality_score: Optional[float] = None
    warnings_count: Optional[int] = None
    duplicate_matches: Optional[List[Dict[str, Any]]] = None
    prompt_variant_id: Optional[UUID] = None


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
    prompt_variant_id: Optional[UUID] = None


class AgentRunDetail(AgentRunSummary):
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_issue_ids: Optional[List[UUID]] = None
    duplicate_matches: Optional[List[Dict[str, Any]]] = None
    remaining_regenerations_today: Optional[int] = None


# Phase 5: Prompt Variant Schemas
class PromptVariantBase(BaseModel):
    name: str
    template: str
    active: bool = True
    is_default: bool = False
    notes: Optional[str] = None
    traffic_weight: Optional[float] = Field(default=1.0, ge=0)
    archived: Optional[bool] = False

class PromptVariantCreate(PromptVariantBase):
    id: Optional[UUID] = None

class PromptVariantUpdate(BaseModel):
    name: Optional[str] = None
    template: Optional[str] = None
    active: Optional[bool] = None
    is_default: Optional[bool] = None
    notes: Optional[str] = None
    traffic_weight: Optional[float] = Field(default=None, ge=0)
    archived: Optional[bool] = None

class PromptVariantOut(PromptVariantBase):
    id: UUID
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    usage_last_30d: Optional[int] = None


class AgentRunItem(BaseModel):
    id: UUID
    run_id: UUID
    item_index: int
    title: str
    acceptance_criteria: List[str] = []
    created_issue_id: Optional[UUID] = None
    status: str


class RegenerateStoryRequest(BaseModel):
    replacement_instructions: Optional[str] = Field(
        default=None,
        description="Optional extra guidance for the regenerated story (e.g. focus, user persona)."
    )


class RegenerateStoryResponse(BaseModel):
    run_id: UUID
    story_index: int
    story: GeneratedStory
    quality_score: Optional[float] = None
    warnings: List[str] = []
    duplicate_matches: Optional[List[Dict[str, Any]]] = None
    updated_warnings_count: Optional[int] = None
    prompt_version: Optional[str] = None
    # New per-story sub-metrics
    distinctness: Optional[float] = None
    criteria_density: Optional[float] = None
    regen_count: Optional[int] = None


class RegenerateEstimateResponse(BaseModel):
    run_id: UUID
    story_index: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_total_tokens: int
    estimated_cost_usd: float
    remaining_regenerations_today: int
    daily_limit: int

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


@router.post("/epic/decompose/{run_id}/stories/{story_index}/regenerate", response_model=RegenerateStoryResponse)
async def regenerate_story(run_id: UUID, story_index: int, body: RegenerateStoryRequest, current_user: UserModel = Depends(get_current_user)):
    """Regenerate a single story within an existing dry-run decomposition (Phase 4 initial feature).

    Constraints:
    - Only allowed on succeeded dry_run runs (not on committed runs yet).
    - Replaces story in-place inside run output and agent_run_items.
    - Recomputes quality_score & duplicate warnings incrementally.
    """
    # Fetch run
    res = supabase.table("agent_runs").select("*").eq("id", str(run_id)).eq("user_id", str(current_user.id)).maybe_single().execute()
    row = getattr(res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    if row.get('mode') != 'dry_run' or row.get('status') != 'succeeded':
        raise HTTPException(status_code=400, detail="Regeneration only allowed on succeeded dry_run runs")
    epic_id = row.get('epic_id')
    if not epic_id:
        raise HTTPException(status_code=400, detail="Run missing epic context")
    output = row.get('output') or {}
    # Daily usage check
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    usage_res = supabase.table('agent_runs').select('output,started_at').eq('user_id', str(current_user.id)) \
        .gte('started_at', day_start.isoformat()).lt('started_at', day_end.isoformat()).execute()
    daily_rows = getattr(usage_res, 'data', []) or []
    total_regens_today = 0
    for dr in daily_rows:
        o = dr.get('output') or {}
        if isinstance(o, dict):
            rc = o.get('regen_count') or 0
            if isinstance(rc, int):
                total_regens_today += rc
    if total_regens_today >= DAILY_REGEN_LIMIT:
        raise HTTPException(status_code=429, detail="Daily regeneration limit reached")
    stories_data = (output.get('stories') or []) if isinstance(output, dict) else []
    if not isinstance(stories_data, list) or not stories_data:
        raise HTTPException(status_code=400, detail="Run has no stories to regenerate")
    if story_index < 0 or story_index >= len(stories_data):
        raise HTTPException(status_code=400, detail="story_index out of range")

    # Pull epic description to give model context
    epic_res = supabase.table("issues").select("id,title,description").eq("id", epic_id).maybe_single().execute()
    epic_row = getattr(epic_res, 'data', None) or {}
    epic_description = epic_row.get('description') or epic_row.get('title') or 'Epic'

    # Locked stories (all except target)
    locked = [s for i, s in enumerate(stories_data) if i != story_index]
    target_prev = stories_data[story_index]

    # Build a focused prompt for a single story regeneration using existing agent utilities
    extra = (body.replacement_instructions or '').strip()
    regen_instructions = (
        "You are refining one user story from a prior epic decomposition. "
        "The other stories must remain conceptually distinct; do NOT duplicate them. "
        "Generate EXACTLY one JSON object: {\n  title: string, acceptance_criteria: string[]\n}. "
        "Title concise (<=12 words), outcome focused. 2-6 criteria. No vague terms."
    )
    if extra:
        regen_instructions += f" Additional guidance: {extra}."
    locked_titles = [s.get('title') for s in locked if isinstance(s, dict)]
    locked_preview = '\n'.join(f"- {t}" for t in locked_titles if t)
    user_prompt = (
        "EPIC_CONTEXT:\n" + epic_description + "\n\n" +
        "EXISTING_LOCKED_STORIES (keep distinct from these):\n" + (locked_preview or '(none)') + "\n\n" +
        "PREVIOUS_STORY_VERSION:\n- Title: " + (target_prev.get('title') or '') + "\n" +
        "Regenerate now with improvements, return JSON only."
    )

    # Use same agent framework as epic_decomposer for consistency
    from openai import AsyncOpenAI
    from agents import Agent, Runner, OpenAIChatCompletionsModel
    GEMINI_API_KEY = epickey = None
    try:
        import os
        GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    except Exception:
        pass
    raw_text = None
    # Ensure warnings list exists before potential stub path usage
    warnings: List[str] = []
    stub_regen = False
    if not GEMINI_API_KEY:
        # Fallback stub regeneration so UX still functions without full LLM credentials
        stub_regen = True
    else:
        client = AsyncOpenAI(api_key=GEMINI_API_KEY, base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
        agent = Agent(
            name="StoryRegenerator",
            instructions=regen_instructions,
            model=OpenAIChatCompletionsModel(model="gemini-2.5-flash", openai_client=client)
        )
        try:
            result = await Runner.run(agent, user_prompt)
            raw_text = getattr(result, 'final_output', str(result))
        except Exception:
            # Fallback to stub if remote call fails
            stub_regen = True
    if stub_regen:
        # Produce a deterministic JSON variant of the previous story
        import json
        prev_title = (target_prev.get('title') or 'Story').strip()
        base_title = prev_title
        if not base_title.lower().startswith('refined'):
            base_title = f"Refined {base_title}"[:160]
        prev_ac = target_prev.get('acceptance_criteria') or []
        if isinstance(prev_ac, list):
            new_ac_list = [c for c in prev_ac][:6]
            if new_ac_list:
                new_ac_list[0] = ("Refined: " + new_ac_list[0])[:300]
            else:
                new_ac_list = ["Criteria clarified"]
        else:
            new_ac_list = ["Criteria clarified"]
        raw_text = json.dumps({
            "title": base_title,
            "acceptance_criteria": new_ac_list
        })
        warnings.append("stub regeneration - GEMINI_API_KEY not configured or call failed")

    # Reuse parser & schema pieces from epic_decomposer
    parsed = epic_decomposer._safe_parse_json(raw_text)  # type: ignore[attr-defined]
    new_story: Optional[Dict[str, Any]] = None
    # warnings list already initialized above; extend rather than reassign
    if parsed and isinstance(parsed, dict):
        # Accept either direct story or wrapped in stories list
        if 'stories' in parsed and isinstance(parsed['stories'], list) and parsed['stories']:
            candidate = parsed['stories'][0]
        else:
            candidate = parsed
        if isinstance(candidate, dict):
            title = (candidate.get('title') or '').strip()
            ac = candidate.get('acceptance_criteria') or []
            if isinstance(ac, str):
                ac = [ac]
            if isinstance(ac, list):
                ac_clean = [str(x).strip()[:300] for x in ac if str(x).strip()]
            else:
                ac_clean = []
            # Lint via agent's internal function
            try:
                lint_fn = epic_decomposer._lint_acceptance_criteria  # type: ignore[attr-defined]
                ac_warnings = lint_fn(ac_clean)
            except Exception:
                ac_warnings = []
            warnings.extend([f"{title}: {w}" for w in ac_warnings])
            if title:
                new_story = {"title": title[:160], "acceptance_criteria": ac_clean[:12]}
    if not new_story:
        raise HTTPException(status_code=422, detail="Model did not return a valid story JSON (after regen)")

    # Replace and normalize uniqueness vs locked titles
    locked_keys = { (s.get('title') or '').lower() for s in locked if isinstance(s, dict) }
    if new_story['title'].lower() in locked_keys:
        warnings.append("duplicate title replaced with suffix due to conflict with locked stories")
        base = new_story['title']
        i = 2
        while f"{base} ({i})".lower() in locked_keys:
            i += 1
        new_story['title'] = f"{base} ({i})"

    # Regeneration guardrail (per-run cap)
    regen_count = int(output.get('regen_count') or 0)
    if regen_count >= 20:
        raise HTTPException(status_code=429, detail="Regeneration limit (20) reached for this run")

    # Apply story replacement
    stories_data[story_index] = new_story

    # Optimized duplicate detection: embed only regenerated story & reuse existing embeddings
    duplicate_matches: List[Dict[str, Any]] = []
    try:
        existing_children, _ = epic_decomposer._fetch_existing_children(str(epic_id))  # type: ignore[attr-defined]
    except Exception:
        existing_children = []
    if existing_children:
        existing_ids = [c.get('id') for c in existing_children if c.get('id')]
        emb_map = fetch_issue_embeddings(existing_ids) if existing_ids else {}
        missing = []
        missing_texts = []
        for c in existing_children:
            cid = c.get('id')
            if cid and cid not in emb_map:
                title_part = (c.get('title') or '')
                ac_raw = c.get('acceptance_criteria') or []
                ac_texts: List[str] = []
                if isinstance(ac_raw, list):
                    for ac in ac_raw[:6]:
                        if isinstance(ac, dict):
                            t = ac.get('text')
                            if isinstance(t, str) and t.strip():
                                ac_texts.append(t.strip())
                        elif isinstance(ac, str) and ac.strip():
                            ac_texts.append(ac.strip())
                combined = title_part + ('\n' + '\n'.join(ac_texts) if ac_texts else '')
                missing.append((cid, combined))
                missing_texts.append(combined)
        if missing_texts:
            vecs = embed_texts(missing_texts)
            upserts = []
            for (cid, _), v in zip(missing, vecs):
                upserts.append((cid, v.vector))
                emb_map[cid] = v.vector
            if upserts:
                upsert_issue_embeddings(upserts)
        new_story_text = new_story['title'] + '\n' + '\n'.join(new_story['acceptance_criteria'][:6])
        new_vecs = embed_texts([new_story_text])
        new_vec = new_vecs[0].vector if new_vecs else []
        best_sim = 0.0
        best_title = None
        for c in existing_children:
            cid = c.get('id')
            if not cid:
                continue
            sim = cosine_sim(new_vec, emb_map.get(cid) or [])
            if sim > best_sim:
                best_sim = sim
                best_title = c.get('title')
        if best_title and best_sim >= 0.85:
            duplicate_matches.append({
                "story_index": story_index,
                "story_title": new_story['title'],
                "existing_title": best_title,
                "similarity": round(best_sim, 3)
            })

    # Recompute aggregate quality
    rebuilt = [GeneratedStory(title=s['title'], acceptance_criteria=s.get('acceptance_criteria') or []) for s in stories_data]
    total_after = len(rebuilt)
    if total_after:
        avg_criteria = sum(len(s.acceptance_criteria) for s in rebuilt) / total_after
        criteria_density = min(1.0, avg_criteria / 6.0)
        dup_count = len(duplicate_matches)
        distinctness = 1 - (dup_count / total_after)
        warning_penalty = 1 - min(1.0, (dup_count / total_after) / 5.0)
        quality_score = compute_quality_score(distinctness, criteria_density, warning_penalty, 1.0)
    else:
        quality_score = None
        criteria_density = None
        distinctness = None
    # Total warnings = prior non-duplicate warnings (from run) excluding old duplicate lines + new ones
    prior_warnings = (output.get('warnings') or []) if isinstance(output, dict) else []
    # Keep previous warnings that are not duplicate related (heuristic: lines starting with 'possible duplicate story' )
    retained = [w for w in prior_warnings if not w.startswith('possible duplicate story')]
    # Add new duplicate warnings
    for dm in duplicate_matches:
        retained.append(
            f"possible duplicate story '{dm['story_title']}' similar to existing '{dm['existing_title']}' (sim={dm['similarity']})"
        )
    retained.extend(warnings)
    warnings_count = len(retained)
    regen_count += 1
    # Prompt version bump (simple semantic increment)
    cur_ver = row.get('prompt_version')
    if cur_ver is None:
        next_ver = 'v1'
    else:
        import re
        m = re.findall(r'(\d+)', str(cur_ver))
        if m:
            next_ver = f"v{int(m[-1]) + 1}"
        else:
            next_ver = 'v1'

    # Write back
    try:
        supabase.table('agent_runs').update({
            'output': {
                'stories': stories_data,
                'warnings': retained,
                'model': output.get('model') if isinstance(output, dict) else 'gemini-2.5-flash',
                'stub': output.get('stub') if isinstance(output, dict) else False,
                'quality_score': quality_score,
                'warnings_count': warnings_count,
                'duplicate_matches': duplicate_matches or None,
                'regen_count': regen_count,
            },
            'quality_score': quality_score,
            'warnings_count': warnings_count,
            'prompt_version': next_ver,
        }).eq('id', str(run_id)).execute()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to persist regeneration output")

    # Update specific run item row
    try:
        supabase.table('agent_run_items').update({
            'title': new_story['title'],
            'acceptance_criteria': new_story['acceptance_criteria'],
            'status': 'regenerated'
        }).eq('run_id', str(run_id)).eq('item_index', story_index).execute()
    except Exception:
        pass

    return RegenerateStoryResponse(
        run_id=run_id,
        story_index=story_index,
        story=GeneratedStory(title=new_story['title'], acceptance_criteria=new_story['acceptance_criteria']),
        quality_score=quality_score,
        warnings=warnings,
        duplicate_matches=duplicate_matches or None,
        updated_warnings_count=warnings_count,
        prompt_version=next_ver,
        distinctness=distinctness,
        criteria_density=criteria_density,
        regen_count=regen_count,
    )


@router.get("/epic/decompose/{run_id}/stories/{story_index}/regenerate/estimate", response_model=RegenerateEstimateResponse)
async def estimate_regeneration(run_id: UUID, story_index: int, current_user: UserModel = Depends(get_current_user)):
    res = supabase.table("agent_runs").select("*").eq("id", str(run_id)).eq("user_id", str(current_user.id)).maybe_single().execute()
    row = getattr(res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    if row.get('mode') != 'dry_run':
        raise HTTPException(status_code=400, detail="Estimate only for dry_run runs")
    output = row.get('output') or {}
    stories_data = (output.get('stories') or []) if isinstance(output, dict) else []
    if story_index < 0 or story_index >= len(stories_data):
        raise HTTPException(status_code=400, detail="story_index out of range")
    epic_id = row.get('epic_id')
    if not epic_id:
        raise HTTPException(status_code=400, detail="Run missing epic context")
    epic_res = supabase.table("issues").select("id,title,description").eq("id", epic_id).maybe_single().execute()
    epic_row = getattr(epic_res, 'data', None) or {}
    epic_description = epic_row.get('description') or epic_row.get('title') or ''
    locked = [s for i, s in enumerate(stories_data) if i != story_index]
    # Build approximate input text
    locked_titles = '\n'.join([(s.get('title') or '') for s in locked if isinstance(s, dict)])
    prev_story = stories_data[story_index] or {}
    prev_text = (prev_story.get('title') or '') + '\n' + '\n'.join(prev_story.get('acceptance_criteria') or [])
    input_comp = epic_description + '\n' + locked_titles + '\n' + prev_text
    estimated_input_tokens = estimate_tokens(input_comp, model="gemini-2.5-flash")
    # Output approx: reuse prev story token size
    estimated_output_tokens = estimate_tokens(prev_text, model="gemini-2.5-flash") or 60
    estimated_total_tokens = estimated_input_tokens + estimated_output_tokens
    estimated_cost_usd = round(estimated_total_tokens * REGEN_TOKEN_COST_USD, 6)
    # Remaining daily limit
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    usage_res = supabase.table('agent_runs').select('output,started_at').eq('user_id', str(current_user.id)) \
        .gte('started_at', day_start.isoformat()).lt('started_at', day_end.isoformat()).execute()
    daily_rows = getattr(usage_res, 'data', []) or []
    total_regens_today = 0
    for dr in daily_rows:
        o = dr.get('output') or {}
        if isinstance(o, dict):
            rc = o.get('regen_count') or 0
            if isinstance(rc, int):
                total_regens_today += rc
    remaining = max(0, DAILY_REGEN_LIMIT - total_regens_today)
    return RegenerateEstimateResponse(
        run_id=run_id,
        story_index=story_index,
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        estimated_total_tokens=estimated_total_tokens,
        estimated_cost_usd=estimated_cost_usd,
        remaining_regenerations_today=remaining,
        daily_limit=DAILY_REGEN_LIMIT,
    )


class FeedbackRequest(BaseModel):
    edited_title: Optional[str] = None
    edited_acceptance_criteria: Optional[List[str]] = None
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    comment: Optional[str] = None


class FeedbackResponse(BaseModel):
    success: bool
    run_id: UUID
    story_index: int
    edit_distance_title: Optional[int] = None
    edit_distance_criteria: Optional[int] = None
    rating: Optional[int] = None


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    m, n = len(a), len(b)
    dp = list(range(n+1))
    for i in range(1, m+1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n+1):
            cur = dp[j]
            if a[i-1] == b[j-1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j-1], cur)
            prev = cur
    return dp[n]


@router.post("/epic/decompose/{run_id}/stories/{story_index}/feedback", response_model=FeedbackResponse)
async def submit_story_feedback(run_id: UUID, story_index: int, body: FeedbackRequest, current_user: UserModel = Depends(get_current_user)):
    res = supabase.table("agent_runs").select("*").eq("id", str(run_id)).eq("user_id", str(current_user.id)).maybe_single().execute()
    row = getattr(res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    # Feedback allowed for both dry_run and commit states after success
    if row.get('status') != 'succeeded':
        raise HTTPException(status_code=400, detail="Feedback only allowed on succeeded runs")
    output = row.get('output') or {}
    stories_data = (output.get('stories') or []) if isinstance(output, dict) else []
    if story_index < 0 or story_index >= len(stories_data):
        raise HTTPException(status_code=400, detail="story_index out of range")
    original = stories_data[story_index]
    orig_title = original.get('title') or ''
    orig_ac = [c for c in (original.get('acceptance_criteria') or [])]
    new_title = body.edited_title.strip() if body.edited_title else orig_title
    new_ac = body.edited_acceptance_criteria if body.edited_acceptance_criteria is not None else orig_ac
    # Basic normalization
    new_ac_clean: list[str] = []
    for c in new_ac:
        if isinstance(c, str):
            t = c.strip()
            if t:
                new_ac_clean.append(t[:300])
    if not new_ac_clean:
        new_ac_clean = orig_ac
    # Compute edit distances
    edt_title = _levenshtein(orig_title, new_title)
    edt_criteria = _levenshtein('\n'.join(orig_ac), '\n'.join(new_ac_clean))
    # Persist metadata diff on run item
    try:
        supabase.table('agent_run_items').update({
            'title': new_title,
            'acceptance_criteria': new_ac_clean,
            'metadata': {
                'edit_distance_title': edt_title,
                'edit_distance_criteria': edt_criteria,
                'rating': body.rating,
                'feedback_comment': body.comment,
            },
            'status': 'feedback'
        }).eq('run_id', str(run_id)).eq('item_index', story_index).execute()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to persist feedback")
    return FeedbackResponse(success=True, run_id=run_id, story_index=story_index, edit_distance_title=edt_title, edit_distance_criteria=edt_criteria, rating=body.rating)


def _persist_run(create: AgentRunCreate) -> Optional[UUID]:
    try:
        payload = create.model_dump()
        payload["id"] = str(uuid4())
        payload["user_id"] = str(payload["user_id"])
        if payload.get("epic_id"):
            payload["epic_id"] = str(payload["epic_id"])
        if payload.get("team_id"):
            payload["team_id"] = str(payload["team_id"])
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


# ---- Prompt Variant Helpers / Endpoints (Phase 5) ----
def _select_prompt_variant(requested_id: Optional[UUID], team_id: Optional[UUID]) -> Optional[UUID]:
    """Return a variant id to use. If requested exists & active use it; else default active one.
    Falls back to None if table empty or errors."""
    try:
        if requested_id:
            q = supabase.table('prompt_variants').select('id,active').eq('id', str(requested_id))
            if team_id:
                q = q.eq('team_id', str(team_id))
            res = q.maybe_single().execute()
            data = getattr(res, 'data', None)
            if data and data.get('active'):
                return UUID(data['id'])
        # Load all active non-archived variants with weights
        q_all = supabase.table('prompt_variants').select('id,is_default,traffic_weight').eq('active', True).eq('archived', False)
        if team_id:
            q_all = q_all.eq('team_id', str(team_id))
        all_res = q_all.execute()
        rows = getattr(all_res, 'data', []) or []
        if not rows:
            return None
        import random, math
        ids = [r['id'] for r in rows]
        base_weight: Dict[str, float] = {}
        for r in rows:
            tw_raw = r.get('traffic_weight') or 1
            try:
                tw = float(tw_raw)
            except Exception:
                tw = 1.0
            if r.get('is_default'):
                tw *= 1.2
            base_weight[r['id']] = max(0.0001, tw)
        # Fetch last 200 runs for bandit scoring
        q_runs = supabase.table('agent_runs').select('prompt_variant_id,quality_score').is_('prompt_variant_id', 'not.null')
        if team_id:
            q_runs = q_runs.eq('team_id', str(team_id))
        run_res = q_runs.order('started_at', desc=True).limit(200).execute()
        run_rows = getattr(run_res, 'data', []) or []
        per_variant_scores: Dict[str, list[float]] = {}
        for rr in run_rows:
            vid = rr.get('prompt_variant_id')
            qs = rr.get('quality_score')
            if vid and isinstance(qs, (int,float)):
                per_variant_scores.setdefault(vid, []).append(float(qs))
        total_runs = sum(len(v) for v in per_variant_scores.values()) or 1
        composite_weights: Dict[str, float] = {}
        for vid in ids:
            samples = per_variant_scores.get(vid, [])
            n = len(samples)
            mean_q = sum(samples)/n if n else 0.5  # prior mean
            # UCB exploration bonus
            bonus = math.sqrt(2 * math.log(total_runs + 1) / (n + 1)) if n < total_runs else 0.0
            composite = base_weight[vid] * (mean_q + bonus)
            composite_weights[vid] = composite
        total_w = sum(composite_weights.values()) or 0
        if total_w <= 0:
            return UUID(ids[0])
        pick = random.random() * total_w
        acc = 0.0
        for vid, w in composite_weights.items():
            acc += w
            if pick <= acc:
                return UUID(vid)
    except Exception:
        return None
    return None


@router.post('/prompt_variants', response_model=PromptVariantOut)
async def create_prompt_variant(body: PromptVariantCreate, ctx: TeamContext = Depends(team_role_required('editor','admin','owner'))):
    # For now anyone authenticated can create (add future RBAC). Name unique enforced at DB.
    vid = body.id or uuid4()
    payload = {
        'id': str(vid),
        'name': body.name,
        'template': body.template,
        'active': body.active,
        'is_default': body.is_default,
        'notes': body.notes,
    'traffic_weight': body.traffic_weight if body.traffic_weight is not None else 1.0,
    'archived': body.archived if body.archived is not None else False,
    }
    try:
        payload['team_id'] = str(ctx.team_id)
        ins = supabase.table('prompt_variants').insert(payload).execute()
        data = getattr(ins, 'data', None) or []
        if not data:
            raise HTTPException(status_code=500, detail='Insert failed')
        row = data[0]
        return PromptVariantOut(
            id=UUID(row['id']),
            name=row.get('name'),
            template=row.get('template'),
            active=row.get('active'),
            is_default=row.get('is_default'),
            notes=row.get('notes'),
            traffic_weight=row.get('traffic_weight'),
            archived=row.get('archived'),
            created_at=row.get('created_at'),
            updated_at=row.get('updated_at'),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error creating variant: {e}')


@router.get('/prompt_variants', response_model=List[PromptVariantOut])
async def list_prompt_variants(include_inactive: bool = False, ctx: TeamContext = Depends(get_team_context)):
    try:
        q = supabase.table('prompt_variants').select('*').eq('team_id', str(ctx.team_id))
        if not include_inactive:
            q = q.eq('active', True)
        res = q.order('created_at', desc=False).execute()
        rows = getattr(res, 'data', []) or []
        # Usage counts (last 30d)
        from datetime import timezone, timedelta
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        window_start = now_utc - timedelta(days=30)
        usage_res = supabase.table('agent_runs').select('prompt_variant_id').eq('team_id', str(ctx.team_id)).gte('started_at', window_start.isoformat()).execute()
        usage_rows = getattr(usage_res, 'data', []) or []
        counts: Dict[str, int] = {}
        for r in usage_rows:
            vid = r.get('prompt_variant_id')
            if vid:
                counts[vid] = counts.get(vid, 0) + 1
        out: List[PromptVariantOut] = []
        for r in rows:
            out.append(PromptVariantOut(
                id=UUID(r['id']),
                name=r.get('name'),
                template=r.get('template'),
                active=r.get('active'),
                is_default=r.get('is_default'),
                notes=r.get('notes'),
                created_at=r.get('created_at'),
                updated_at=r.get('updated_at'),
                usage_last_30d=counts.get(r['id'])
            ))
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error listing variants: {e}')


@router.get('/prompt_variants/by_id/{variant_id}', response_model=PromptVariantOut)
async def get_prompt_variant(variant_id: UUID, ctx: TeamContext = Depends(get_team_context)):
    res = supabase.table('prompt_variants').select('*').eq('id', str(variant_id)).eq('team_id', str(ctx.team_id)).maybe_single().execute()
    row = getattr(res, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail='Variant not found')
    return PromptVariantOut(
        id=UUID(row['id']),
        name=row.get('name'),
        template=row.get('template'),
        active=row.get('active'),
        is_default=row.get('is_default'),
        notes=row.get('notes'),
        traffic_weight=row.get('traffic_weight'),
        archived=row.get('archived'),
        created_at=row.get('created_at'),
        updated_at=row.get('updated_at'),
    )


@router.patch('/prompt_variants/by_id/{variant_id}', response_model=PromptVariantOut)
async def update_prompt_variant(variant_id: UUID, body: PromptVariantUpdate, ctx: TeamContext = Depends(team_role_required('editor','admin','owner'))):
    patch = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if not patch:
        res = supabase.table('prompt_variants').select('*').eq('id', str(variant_id)).eq('team_id', str(ctx.team_id)).maybe_single().execute()
        row = getattr(res, 'data', None)
        if not row:
            raise HTTPException(status_code=404, detail='Variant not found')
    else:
        try:
            supabase.table('prompt_variants').update(patch).eq('id', str(variant_id)).eq('team_id', str(ctx.team_id)).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Update failed: {e}')
    # Return fresh
    fresh = supabase.table('prompt_variants').select('*').eq('id', str(variant_id)).eq('team_id', str(ctx.team_id)).maybe_single().execute()
    row = getattr(fresh, 'data', None)
    if not row:
        raise HTTPException(status_code=404, detail='Variant not found after update')
    return PromptVariantOut(
        id=UUID(row['id']),
        name=row.get('name'),
        template=row.get('template'),
        active=row.get('active'),
        is_default=row.get('is_default'),
        notes=row.get('notes'),
        traffic_weight=row.get('traffic_weight'),
        archived=row.get('archived'),
        created_at=row.get('created_at'),
        updated_at=row.get('updated_at'),
    )


class PromptVariantAllocationResponse(BaseModel):
    chosen_variant_id: Optional[UUID]
    reason: str


@router.get('/prompt_variants/allocate', response_model=PromptVariantAllocationResponse)
async def allocate_prompt_variant(requested_variant_id: Optional[UUID] = None, team_id: UUID | None = None, current_user: UserModel = Depends(get_current_user), ctx: TeamContext = Depends(get_team_context)):
    if team_id and str(team_id) != str(ctx.team_id):
        raise HTTPException(status_code=403, detail='Team mismatch')
    vid = _select_prompt_variant(requested_variant_id, ctx.team_id)
    reason = 'requested_active' if requested_variant_id and vid == requested_variant_id else 'default_or_any'
    return PromptVariantAllocationResponse(chosen_variant_id=vid, reason=reason)


@router.post("/epic/decompose", response_model=EpicDecomposeResponse)
async def epic_decompose(body: EpicDecomposeRequest, current_user: UserModel = Depends(get_current_user), ctx: TeamContext = Depends(get_team_context)):
    epic = _validate_and_fetch_epic(body.epic_id, current_user.id)

    # If commit without prior generation stories provided -> error
    if body.commit and body.dry_run:
        raise HTTPException(status_code=400, detail="Cannot commit with dry_run=true")

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

    # Create run (always create for audit even dry run)
    run_mode = 'dry_run' if body.dry_run else 'commit'
    # Select prompt variant (Phase 5)
    chosen_variant_id = _select_prompt_variant(body.prompt_variant_id, ctx.team_id)

    run_id = _persist_run(AgentRunCreate(
        agent_type="epic_decomposer",
        action="epic_decompose",
        mode=run_mode,
        epic_id=body.epic_id,
        user_id=current_user.id,
        team_id=ctx.team_id,
        prompt_variant_id=chosen_variant_id,
        input={
            "epic_id": str(body.epic_id),
            "max_stories": body.max_stories,
            "dry_run": body.dry_run,
            "commit": body.commit,
            "prompt_variant_id": str(chosen_variant_id) if chosen_variant_id else None,
        }
    ))

    start_time = time.perf_counter()
    input_token_estimate = 0
    output_token_estimate = 0
    duplicate_matches: List[Dict[str, Any]] = []
    quality_score: Optional[float] = None
    warnings_count: Optional[int] = None
    if body.dry_run:
        result = await epic_decomposer.decompose_epic(
            epic_description=epic.get('description') or epic.get('title') or 'Epic',
            max_stories=body.max_stories,
            epic_id=str(epic['id'])
        )
        raw = result.get('data') if result.get('success') else None
        prompt_source = (epic.get('description') or epic.get('title') or '')
        import hashlib
        prompt_hash = hashlib.sha256((prompt_source + (str(chosen_variant_id) or '')).encode('utf-8')).hexdigest()[:16]
        input_token_estimate = estimate_tokens(prompt_source, model="gemini-2.5-flash")
        candidate_stories: List[Dict[str, Any]]
        if result.get('success') and raw and isinstance(raw, dict) and isinstance(raw.get('stories'), list):
            model_used = "gemini-2.0-flash"
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
        prompt_hash = None
        if not body.stories:
            raise HTTPException(status_code=400, detail="stories required when commit=true")
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
    if body.commit and not body.dry_run:
        for idx, st in enumerate(stories):
            cid = _create_child_issue(epic, st, current_user.id, run_id)
            if cid:
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
                "prompt_variant_id": str(chosen_variant_id) if chosen_variant_id else None,
                "prompt_hash": prompt_hash if body.dry_run else None,
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
            prompt_variant_id=chosen_variant_id,
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
        generated_at=datetime.utcnow().isoformat() + 'Z',
        quality_score=quality_score,
        warnings_count=warnings_count,
        duplicate_matches=duplicate_matches or None,
        prompt_variant_id=chosen_variant_id,
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
            mode=r.get('mode'),
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
            prompt_variant_id=UUID(r['prompt_variant_id']) if r.get('prompt_variant_id') else None,
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
        prompt_variant_id=UUID(row['prompt_variant_id']) if row.get('prompt_variant_id') else None,
    )


class FeedbackSummaryResponse(BaseModel):
    run_id: UUID
    story_count: int
    feedback_items: int
    average_rating: Optional[float] = None
    avg_edit_distance_title: Optional[float] = None
    avg_edit_distance_criteria: Optional[float] = None


@router.get("/runs/{run_id}/feedback_summary", response_model=FeedbackSummaryResponse)
async def feedback_summary(run_id: UUID, current_user: UserModel = Depends(get_current_user)):
    # Ensure run belongs to user
    res = supabase.table("agent_runs").select("id").eq("id", str(run_id)).eq("user_id", str(current_user.id)).maybe_single().execute()
    if not getattr(res, 'data', None):
        raise HTTPException(status_code=404, detail="Run not found")
    items_res = supabase.table("agent_run_items").select("metadata").eq("run_id", str(run_id)).execute()
    rows = getattr(items_res, 'data', []) or []
    fb = 0
    rating_sum = 0
    edt_title_sum = 0
    edt_criteria_sum = 0
    for r in rows:
        meta = r.get('metadata') or {}
        if not isinstance(meta, dict):
            continue
        has_rating = meta.get('rating') is not None or meta.get('edit_distance_title') is not None or meta.get('edit_distance_criteria') is not None
        if has_rating:
            fb += 1
            if isinstance(meta.get('rating'), int):
                rating_sum += meta['rating']
            if isinstance(meta.get('edit_distance_title'), int):
                edt_title_sum += meta['edit_distance_title']
            if isinstance(meta.get('edit_distance_criteria'), int):
                edt_criteria_sum += meta['edit_distance_criteria']
    story_count = len(rows)
    return FeedbackSummaryResponse(
        run_id=run_id,
        story_count=story_count,
        feedback_items=fb,
        average_rating=(rating_sum / fb) if fb else None,
        avg_edit_distance_title=(edt_title_sum / fb) if fb else None,
        avg_edit_distance_criteria=(edt_criteria_sum / fb) if fb else None,
    )


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


@router.get("/feedback/metrics", response_model=FeedbackAggregateResponse)
async def aggregate_feedback_metrics(days: int = 30, current_user: UserModel = Depends(get_current_user)):
    """Aggregate feedback + edit metrics across user's runs for last N days (Phase 5 seed endpoint).

    Computes:
    - total runs considered
    - total stories (agent_run_items)
    - feedback_items (items with metadata diff or rating)
    - average rating & edit distances
    - rating distribution (1..5)
    - acceptance criteria average count
    - story title+criteria length heuristic (chars)
    """
    if days < 1:
        days = 1
    if days > 180:
        days = 180
    from datetime import timezone, timedelta
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    window_start = now_utc - timedelta(days=days)
    # Fetch run ids first (limit large scans)
    runs_res = supabase.table('agent_runs').select('id,output,started_at').eq('user_id', str(current_user.id)) \
        .gte('started_at', window_start.isoformat()).execute()
    runs = getattr(runs_res, 'data', []) or []
    run_ids = [r.get('id') for r in runs if r.get('id')]
    total_runs = len(run_ids)
    total_stories = 0
    feedback_items = 0
    rating_sum = 0
    edt_title_sum = 0
    edt_criteria_sum = 0
    rating_dist: Dict[str, int] = {str(i): 0 for i in range(1,6)}
    criteria_total = 0
    story_len_chars = 0
    if run_ids:
        # Chunk to avoid URL length issues (simple approach; supabase python client handles list eq? fallback to multiple queries)
        for rid in run_ids:
            items_res = supabase.table('agent_run_items').select('title,acceptance_criteria,metadata').eq('run_id', rid).execute()
            items = getattr(items_res, 'data', []) or []
            total_stories += len(items)
            for it in items:
                title = (it.get('title') or '')
                ac_list = it.get('acceptance_criteria') or []
                if isinstance(ac_list, list):
                    criteria_total += len(ac_list)
                story_len_chars += len(title) + sum(len(c) for c in ac_list) if isinstance(ac_list, list) else len(title)
                meta = it.get('metadata') or {}
                if isinstance(meta, dict):
                    has_fb = any(k in meta for k in ('rating','edit_distance_title','edit_distance_criteria','feedback_comment'))
                    if has_fb:
                        feedback_items += 1
                        r_val = meta.get('rating')
                        if isinstance(r_val, int) and 1 <= r_val <= 5:
                            rating_sum += r_val
                            rating_dist[str(r_val)] += 1
                        if isinstance(meta.get('edit_distance_title'), int):
                            edt_title_sum += meta['edit_distance_title']
                        if isinstance(meta.get('edit_distance_criteria'), int):
                            edt_criteria_sum += meta['edit_distance_criteria']
    avg_rating = (rating_sum / sum(rating_dist.values())) if sum(rating_dist.values()) else None
    avg_criteria_count = (criteria_total / total_stories) if total_stories else None
    avg_story_length_chars = (story_len_chars / total_stories) if total_stories else None
    avg_edit_distance_title = (edt_title_sum / feedback_items) if feedback_items else None
    avg_edit_distance_criteria = (edt_criteria_sum / feedback_items) if feedback_items else None
    return FeedbackAggregateResponse(
        days=days,
        total_runs=total_runs,
        total_stories=total_stories,
        feedback_items=feedback_items,
        avg_rating=avg_rating,
        avg_edit_distance_title=avg_edit_distance_title,
        avg_edit_distance_criteria=avg_edit_distance_criteria,
        rating_distribution=rating_dist,
        avg_criteria_count=avg_criteria_count,
        avg_story_length_chars=avg_story_length_chars,
    )


class PromptVariantMetrics(BaseModel):
    id: UUID
    name: str
    runs: int
    stories: int
    avg_quality_score: Optional[float] = None
    avg_rating: Optional[float] = None
    avg_edit_distance_title: Optional[float] = None
    avg_edit_distance_criteria: Optional[float] = None
    cost_per_1k_tokens: Optional[float] = None
    avg_regen_count: Optional[float] = None
    duplicate_warning_rate: Optional[float] = None


class PromptDiffRequest(BaseModel):
    old_variant_id: Optional[UUID] = None
    new_template: str

class PromptDiffResponse(BaseModel):
    diff: str
    new_length: int
    old_length: int
    risk_flags: List[str] = []

@router.post('/prompt_variants/diff', response_model=PromptDiffResponse)
async def prompt_variant_diff(body: PromptDiffRequest, current_user: UserModel = Depends(get_current_user)):
    from app.services.prompt_diff import diff_prompts
    old_text = ''
    if body.old_variant_id:
        res = supabase.table('prompt_variants').select('template').eq('id', str(body.old_variant_id)).maybe_single().execute()
        row = getattr(res, 'data', None)
        if row:
            old_text = row.get('template') or ''
    result = diff_prompts(old_text, body.new_template)
    return PromptDiffResponse(**result)


@router.get('/prompt_variants/metrics', response_model=List[PromptVariantMetrics])
async def prompt_variant_metrics(days: int = 30, ctx: TeamContext = Depends(get_team_context)):
    """Aggregate quality & feedback metrics per prompt variant (Phase 5 experimentation).

    For each active variant used in last N days: runs count, stories count, avg quality_score (run-level),
    and avg rating / edit distances (story-level feedback)."""
    if days < 1:
        days = 1
    if days > 180:
        days = 180
    from datetime import timezone, timedelta
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    window_start = now_utc - timedelta(days=days)
    # Fetch runs with variant
    runs_res = supabase.table('agent_runs').select('id,prompt_variant_id,quality_score,started_at,output,input_tokens,output_tokens').eq('team_id', str(ctx.team_id)) \
        .gte('started_at', window_start.isoformat()).execute()
    runs = getattr(runs_res, 'data', []) or []
    variant_runs: Dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        vid = r.get('prompt_variant_id')
        if not vid:
            continue
        variant_runs.setdefault(vid, []).append(r)
    if not variant_runs:
        return []
    # Fetch variant names
    v_ids = list(variant_runs.keys())
    names_map: Dict[str, str] = {}
    for vid in v_ids:
        vr = supabase.table('prompt_variants').select('id,name').eq('id', vid).eq('team_id', str(ctx.team_id)).maybe_single().execute()
        row = getattr(vr, 'data', None)
        if row:
            names_map[vid] = row.get('name') or 'Unnamed'
    out: List[PromptVariantMetrics] = []
    # Story-level aggregation
    for vid, variant_run_list in variant_runs.items():
        run_ids = [r.get('id') for r in variant_run_list if r.get('id')]
        quality_scores: List[float] = []
        regen_sum = 0
        duplicate_warning_hits = 0
        token_total = 0
        for r in variant_run_list:
            qs = r.get('quality_score')
            if isinstance(qs, (int, float)):
                quality_scores.append(float(qs))
            run_output = r.get('output') or {}
            if isinstance(run_output, dict):
                rc = run_output.get('regen_count')
                if isinstance(rc, int):
                    regen_sum += rc
                dups = run_output.get('duplicate_matches')
                if isinstance(dups, list):
                    duplicate_warning_hits += len(dups)
            itoks = r.get('input_tokens') or 0
            otoks = r.get('output_tokens') or 0
            token_total += (itoks or 0) + (otoks or 0)
        stories = 0
        rating_sum = 0
        rating_count = 0
        edt_title_sum = 0
        edt_criteria_sum = 0
        for rid in run_ids:
            items_res = supabase.table('agent_run_items').select('metadata').eq('run_id', rid).execute()
            items = getattr(items_res, 'data', []) or []
            stories += len(items)
            for it in items:
                meta = it.get('metadata') or {}
                if isinstance(meta, dict):
                    r_val = meta.get('rating')
                    if isinstance(r_val, int) and 1 <= r_val <= 5:
                        rating_sum += r_val
                        rating_count += 1
                    if isinstance(meta.get('edit_distance_title'), int):
                        edt_title_sum += meta['edit_distance_title']
                    if isinstance(meta.get('edit_distance_criteria'), int):
                        edt_criteria_sum += meta['edit_distance_criteria']
        avg_q = (sum(quality_scores) / len(quality_scores)) if quality_scores else None
        avg_rating = (rating_sum / rating_count) if rating_count else None
        avg_edt_title = (edt_title_sum / rating_count) if rating_count else None
        avg_edt_criteria = (edt_criteria_sum / rating_count) if rating_count else None
        cost_per_1k = None
        if token_total:
            # reuse global heuristic cost 0.000002 per token => *1000
            cost_per_1k = round((token_total * 0.000002) / (token_total/1000), 6)
        avg_regen = (regen_sum / len(variant_run_list)) if variant_run_list else None
        dup_rate = (duplicate_warning_hits / stories) if stories else None
        out.append(PromptVariantMetrics(
            id=UUID(vid),
            name=names_map.get(vid, 'Unknown'),
            runs=len(variant_run_list),
            stories=stories,
            avg_quality_score=avg_q,
            avg_rating=avg_rating,
            avg_edit_distance_title=avg_edt_title,
            avg_edit_distance_criteria=avg_edt_criteria,
            cost_per_1k_tokens=cost_per_1k,
            avg_regen_count=avg_regen,
            duplicate_warning_rate=dup_rate,
        ))
    # Sort by runs desc
    out.sort(key=lambda x: x.runs, reverse=True)
    return out


# --- Experiment Stats (Bayesian / Significance Approximation) ---
class VariantDailyPoint(BaseModel):
    date: date  # type: ignore[name-defined]
    runs: int
    mean_quality: Optional[float]


class PromptVariantStats(BaseModel):
    id: UUID
    name: str
    runs: int
    mean_quality: Optional[float] = None
    bayesian_mean: Optional[float] = None
    quality_ci_low: Optional[float] = None
    quality_ci_high: Optional[float] = None
    relative_lift_pct: Optional[float] = None
    timeseries: List[VariantDailyPoint] = []


@router.get('/prompt_variants/stats', response_model=List[PromptVariantStats])
async def prompt_variant_stats(days: int = 30, include_timeseries: bool = True, ctx: TeamContext = Depends(get_team_context)):
    """Return per-variant statistical summary for quality_score with simple Bayesian (Beta) approximation.

    Treat quality_score in [0,1] as fractional success. Use uniform Beta(1,1) prior.
    Posterior: alpha = 1 + sum(scores); beta = 1 + n - sum(scores) (clamped if negative) and normal approx for 95% CI.
    Also returns daily aggregated means if include_timeseries.
    """
    if days < 1:
        days = 1
    if days > 180:
        days = 180
    from datetime import timezone, timedelta
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    window_start = now_utc - timedelta(days=days)
    runs_res = supabase.table('agent_runs').select('id,prompt_variant_id,quality_score,started_at').eq('team_id', str(ctx.team_id)) \
        .gte('started_at', window_start.isoformat()).execute()
    runs = getattr(runs_res, 'data', []) or []
    variant_scores: Dict[str, List[float]] = {}
    variant_dates: Dict[str, Dict[str, List[float]]] = {}
    variant_started: Dict[str, List[str]] = {}
    for r in runs:
        vid = r.get('prompt_variant_id')
        if not vid:
            continue
        qs = r.get('quality_score')
        if isinstance(qs, (int, float)):
            # clamp to [0,1]
            val = float(qs)
            if val < 0:
                val = 0.0
            if val > 1:
                val = 1.0
            variant_scores.setdefault(vid, []).append(val)
            dt_str = r.get('started_at')
            if include_timeseries and isinstance(dt_str, str) and len(dt_str) >= 10:
                day = dt_str[:10]
                variant_dates.setdefault(vid, {}).setdefault(day, []).append(val)
        variant_started.setdefault(vid, []).append(r.get('started_at') or '')
    if not variant_scores:
        return []
    # Fetch variant names in one-by-one (could optimize with in_ once supabase-py updates)
    names_map: Dict[str, str] = {}
    for vid in variant_scores.keys():
        vr = supabase.table('prompt_variants').select('id,name').eq('id', vid).eq('team_id', str(ctx.team_id)).maybe_single().execute()
        row = getattr(vr, 'data', None)
        if row:
            names_map[vid] = row.get('name') or 'Unnamed'
    stats: List[PromptVariantStats] = []
    # Compute bayesian & CI
    for vid, scores in variant_scores.items():
        n = len(scores)
        s = sum(scores)
        mean_q: Optional[float] = (s / n) if n else None
        alpha = 1.0 + s
        beta_param = 1.0 + (n - s)
        if beta_param < 1e-6:
            beta_param = 1e-6
        bayesian_mean = alpha / (alpha + beta_param)
        # variance of Beta
        var = (alpha * beta_param) / (((alpha + beta_param) ** 2) * (alpha + beta_param + 1.0))
        import math
        sd = math.sqrt(var)
        ci_low = bayesian_mean - 1.96 * sd
        ci_high = bayesian_mean + 1.96 * sd
        if ci_low < 0:
            ci_low = 0.0
        if ci_high > 1:
            ci_high = 1.0
        series: List[VariantDailyPoint] = []
        if include_timeseries:
            date_map = variant_dates.get(vid, {})
            for d_str, vals in sorted(date_map.items()):
                try:
                    d_date = datetime.strptime(d_str, '%Y-%m-%d').date()
                except Exception:
                    continue
                series.append(VariantDailyPoint(date=d_date, runs=len(vals), mean_quality=(sum(vals)/len(vals)) if vals else None))
        stats.append(PromptVariantStats(
            id=UUID(vid),
            name=names_map.get(vid, 'Unknown'),
            runs=len(variant_started.get(vid, [])),
            mean_quality=mean_q,
            bayesian_mean=round(bayesian_mean, 4) if bayesian_mean is not None else None,
            quality_ci_low=round(ci_low, 4) if mean_q is not None else None,
            quality_ci_high=round(ci_high, 4) if mean_q is not None else None,
            timeseries=series,
        ))
    # Compute relative lift vs best bayesian mean
    best = max([s.bayesian_mean or 0 for s in stats])
    if best > 0:
        for s in stats:
            if s.bayesian_mean is not None:
                s.relative_lift_pct = round(((s.bayesian_mean / best) - 1.0) * 100.0, 2)
    # Sort by bayesian mean desc
    stats.sort(key=lambda x: x.bayesian_mean or 0, reverse=True)
    return stats


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
