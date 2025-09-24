"""Epic Decomposer Agent (Gemini via OpenAI-compatible endpoint)

Implements a minimal epic → user stories generator using the OpenAI Agents SDK.

Key patterns aligned with official docs:
    - Use Agent(..., instructions=...) instead of passing a system prompt to the model.
    - Use OpenAIChatCompletionsModel for chat completion style models.
    - Run with Runner.run (async) for a single-shot interaction.

Environment:
    - Requires GEMINI_API_KEY exported or provided in a .env file.
    - Install dependencies: `pip install openai-agents` (which brings the `agents` package).

Execute demo:
    python -m app.agents.epic_decomposer

The function `decompose_epic` returns structured metadata including success flag,
parsed JSON (if the model obeys format), raw output, and error message if any.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Tuple, Optional

from dotenv import load_dotenv, find_dotenv

from openai import AsyncOpenAI  # Official OpenAI client (used with Gemini-compatible base_url)
from agents import Agent, Runner, OpenAIChatCompletionsModel  # Core SDK primitives
try:
    # Optional Tool interface (future: QuadRails style tools) – kept loose to not break if version lacks it.
    from agents import Tool  # type: ignore
except Exception:  # pragma: no cover
    class Tool:  # type: ignore
        pass

try:
    from app.core.dependencies import supabase  # reuse existing supabase client
except Exception:  # pragma: no cover
    supabase = None  # type: ignore
# Explicit model import (doc-aligned) – placed at module level (was incorrectly nested)
try:  # runtime-safe import with fallbacks for type checking
    from app.services.embeddings import embed_texts, cosine_sim, compute_quality_score  # type: ignore
except Exception:  # pragma: no cover
    def embed_texts(texts):  # type: ignore
        return []
    def cosine_sim(a, b):  # type: ignore
        return 0.0
    def compute_quality_score(a, b, c, d):  # type: ignore
        return 0.0


# Load .env (silent if missing)
load_dotenv(find_dotenv())

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    # Clear feedback but do not raise so imports (e.g., test discovery) still succeed.
    print("[epic_decomposer] Warning: GEMINI_API_KEY not set. Skipping live calls.")


# Gemini via OpenAI-compatible endpoint (reference: https://ai.google.dev/gemini-api/docs/openai)
MODEL_NAME = "gemini-2.5-flash"
client = AsyncOpenAI(
    api_key=GEMINI_API_KEY or "",  # Empty key allows graceful failure path
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
)


BASE_INSTRUCTIONS = (
    "You are an Epic Decomposition Assistant. You will receive an EPIC_CONTEXT section. "
    "Your job: propose USER STORIES only (type=story). Do NOT propose tasks, subtasks, or new epics. "
    "Scope discipline: generate stories ONLY for functionality actually described in EPIC_CONTEXT. If scope is insufficient, return an empty stories array. "
    "Guardrails: avoid duplication with EXISTING_CHILD_STORIES. Keep each story distinct and user-value oriented. "
    "Output format (JSON ONLY): {\\n  epic: string,\\n  stories: [ { title: string, acceptance_criteria: string[] } ]\\n}. "
    "Titles: concise (<= 12 words), outcome-focused, no implementation details. "
    "Acceptance criteria: 2-6 clear, testable statements phrased as observable outcomes; avoid vague terms (should/could/maybe/some/various/appropriate). "
    "NO markdown, NO commentary—return ONLY the JSON object."
)


def _safe_parse_json(raw: str) -> Dict[str, Any] | None:
    """Attempt to parse the model output as JSON with multi-stage repair.

    Stages:
      1. Direct json.loads after stripping code-fence artifacts.
      2. Balanced braces slice (first '{' to last '}').
      3. Heuristic reconstruction: extract lines that look like titles and bullet criteria
         producing {"stories":[{"title":...,"acceptance_criteria":[...]}]}
         if at least one story discovered.
    Returns None if all attempts fail.
    """
    candidate = raw.strip().strip("` ")
    if candidate.startswith("json\n"):
        candidate = candidate[5:]
    # Stage 1
    try:
        return json.loads(candidate)
    except Exception:
        pass
    # Stage 2: balanced braces slice
    try:
        start = candidate.find('{')
        end = candidate.rfind('}')
        if start != -1 and end != -1 and end > start:
            repaired = candidate[start:end+1]
            return json.loads(repaired)
    except Exception:  # pragma: no cover
        pass
    # Stage 3: heuristic extraction
    lines = [l.strip() for l in candidate.splitlines() if l.strip()]
    stories: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for ln in lines:
        # Title heuristics: numbered list '1. Title', explicit 'Title:' label, JSON-like '"title":', or bullet list criteria
        if ln.lower().startswith('title:'):
            title_txt = ln.split(':', 1)[1].strip().strip('"').strip('-').strip()
            if title_txt:
                if current:
                    stories.append(current)
                current = {"title": title_txt, "acceptance_criteria": []}
            continue
        if (ln.startswith('- ') or ln.startswith('* ')) and current:
            crit = ln[2:].strip().rstrip(',')
            if crit:
                current.setdefault("acceptance_criteria", []).append(crit)
            continue
        # Numeric list like '1. User can ...' (first char digit, second is '.')
        if len(ln) > 2 and ln[0].isdigit() and ln[1] == '.' and not ln.lower().startswith('1. acceptance'):
            title_txt = ln.split('.', 1)[1].strip().strip('\"')
            if title_txt:
                if current:
                    stories.append(current)
                current = {"title": title_txt, "acceptance_criteria": []}
            continue
        # Fallback: detect JSON-like title line "title": "..."
        if '"title"' in ln and ':' in ln:
            try:
                after = ln.split(':', 1)[1]
                potential = after.strip().strip('",')
                if potential:
                    if current:
                        stories.append(current)
                    current = {"title": potential, "acceptance_criteria": []}
                    continue
            except Exception:
                pass
    if current:
        stories.append(current)
    # Clean & filter
    cleaned: List[Dict[str, Any]] = []
    for s in stories:
        title = (s.get('title') or '').strip()
        if not title:
            continue
        ac = s.get('acceptance_criteria') or []
        if isinstance(ac, list):
            ac = [str(x).strip() for x in ac if str(x).strip()]
        else:
            ac = []
        cleaned.append({"title": title, "acceptance_criteria": ac})
    if cleaned:
        return {"stories": cleaned}
    return None


def _fetch_existing_children(epic_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetch existing child issues (tasks/stories) and sibling epics for context (best effort)."""
    if not supabase:
        return [], []
    try:
        children_res = supabase.table("issues").select("id,title,type,acceptance_criteria").eq("epic_id", epic_id).execute()
        children = getattr(children_res, 'data', []) or []
    except Exception:
        children = []
    try:
        # simple sibling fetch: other stories without epic link but same epic's project could be noise; skip for now
        siblings: List[Dict[str, Any]] = []
    except Exception:
        siblings = []
    return children, siblings


def _summarize_children(children: List[Dict[str, Any]]) -> str:
    if not children:
        return "(none)"
    out = []
    for c in children[:15]:  # cap to avoid prompt bloat
        raw_title = c.get('title') or ''
        title_snip = raw_title[:120]
        ac = c.get('acceptance_criteria') or []
        ac_items: List[str] = []
        if isinstance(ac, list):
            for a in ac[:3]:
                if isinstance(a, dict):
                    txt = a.get('text') or ''
                else:
                    txt = str(a) if a is not None else ''
                txt = txt.strip()
                if txt:
                    ac_items.append(txt[:120])
        ac_preview = "; ".join(ac_items)
        if len(ac_preview) > 160:
            ac_preview = ac_preview[:157] + '...'
        out.append(f"- {title_snip} | criteria: {ac_preview}")
    return "\n".join(out)


VAGUE_TERMS = {"should", "maybe", "could", "some", "various", "appropriate"}


def _lint_acceptance_criteria(criteria: List[str]) -> List[str]:
    warnings: List[str] = []
    if len(criteria) < 1:
        warnings.append("acceptance criteria empty")
    if len(criteria) > 12:
        warnings.append("acceptance criteria exceeds 12 items")
    for i, c in enumerate(criteria):
        low = c.lower()
        if any(t in low.split() for t in VAGUE_TERMS):
            warnings.append(f"criterion {i+1} contains vague term")
        if len(c) > 260:
            warnings.append(f"criterion {i+1} truncated >260 chars")
    return warnings


def _schema_validate(parsed: Dict[str, Any]) -> Tuple[Optional[List[Dict[str, Any]]], List[str]]:
    """Return (stories, warnings). Stories None if invalid schema."""
    warnings: List[str] = []
    if not isinstance(parsed, dict):
        return None, ["parsed root not object"]
    stories = parsed.get('stories')
    if not isinstance(stories, list):
        return None, ["stories field missing or not list"]
    valid: List[Dict[str, Any]] = []
    for idx, s in enumerate(stories):
        if not isinstance(s, dict):
            warnings.append(f"story {idx+1} not object; skipped")
            continue
        title = s.get('title')
        if not isinstance(title, str) or not title.strip():
            warnings.append(f"story {idx+1} invalid title; skipped")
            continue
        ac = s.get('acceptance_criteria')
        if isinstance(ac, str):
            ac = [ac]
        if not isinstance(ac, list):
            ac = []
        ac_clean: List[str] = []
        for raw in ac:
            if isinstance(raw, str):
                for line in raw.split('\n'):
                    t = line.strip()
                    if t:
                        ac_clean.append(t[:300])
        ac_warnings = _lint_acceptance_criteria(ac_clean)
        warnings.extend([f"{title}: {w}" for w in ac_warnings])
        valid.append({"title": title.strip()[:160], "acceptance_criteria": ac_clean[:12]})
    return valid, warnings


def _final_normalize(stories: List[Dict[str, Any]], max_stories: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    dedup: List[Dict[str, Any]] = []
    seen = set()
    warn: List[str] = []
    for s in stories:
        t = s["title"].strip()
        key = t.lower()
        if key in seen:
            warn.append(f"duplicate title removed: {t}")
            continue
        seen.add(key)
        if not t:
            warn.append("empty title removed")
            continue
        dedup.append({"title": t, "acceptance_criteria": s.get('acceptance_criteria', [])})
        if len(dedup) >= max_stories:
            break
    if len(dedup) < len(stories) and len(dedup) == max_stories:
        warn.append(f"truncated to max_stories={max_stories}")
    return dedup, warn


async def decompose_epic(epic_description: str, max_stories: int = 6, epic_id: str | None = None, user_prompt: str | None = None) -> Dict[str, Any]:
    """Run the agent to decompose an epic and return structured JSON.

    Returns a dict with keys:
      success (bool)
      data (parsed JSON or None)
      raw_output (original text)
      error (optional message)
    """
    if not GEMINI_API_KEY:
        return {
            "success": False,
            "data": None,
            "raw_output": None,
            "error": "GEMINI_API_KEY not configured",
        }

    # Bound max stories (3..12)
    if max_stories < 3:
        max_stories = 3
    if max_stories > 12:
        max_stories = 12

    existing_children: List[Dict[str, Any]] = []
    if epic_id:
        existing_children, _ = _fetch_existing_children(epic_id)
    children_summary = _summarize_children(existing_children)

    # Guardrails: treat user_prompt as guidance only, not new scope. Strip excessive length.
    safe_user_prompt = None
    if user_prompt:
        up = user_prompt.strip()
        if up:
            safe_user_prompt = up[:800]

    dynamic_instructions = (
        f"{BASE_INSTRUCTIONS}\n"
        f"Required story count: up to {max_stories} stories (fewer allowed if scope is small). Do NOT exceed {max_stories}. "
        f"If functionality already represented in EXISTING_CHILD_STORIES skip it. "
        f"If USER_GUIDANCE is provided, use it ONLY to refine clarity (e.g., target sections, emphasis, example outcomes). "
        f"Do NOT invent functionality absent from EPIC_CONTEXT; if USER_GUIDANCE expands scope beyond EPIC_CONTEXT, ignore the expansion."
    )

    agent = Agent(
        name="EpicDecomposer",
        instructions=dynamic_instructions,
        model=OpenAIChatCompletionsModel(
            model=MODEL_NAME,
            openai_client=client,
        ),
    )

    user_message = (
        "EPIC_CONTEXT:\n" + epic_description + "\n\n" +
        "EXISTING_CHILD_STORIES:\n" + children_summary + "\n\n" +
        ("USER_GUIDANCE:\n" + safe_user_prompt + "\n\n" if safe_user_prompt else "") +
        "Respond with JSON now."
    )

    try:
        result = await Runner.run(agent, user_message)
    except Exception as exc:  # Broad catch to return structured error
        return {
            "success": False,
            "data": None,
            "raw_output": None,
            "error": f"agent run failed: {exc}",
        }

    raw_text = getattr(result, "final_output", str(result))
    parsed = _safe_parse_json(raw_text)
    schema_warnings: List[str] = []
    normalized: Optional[List[Dict[str, Any]]] = None
    if parsed:
        normalized, schema_warnings = _schema_validate(parsed)
    if not normalized:  # fallback stub with warning
        fallback = []
        schema_warnings.append("fallback stub used due to parse/validation failure")
    else:
        fallback = normalized
    final_list, final_warn = _final_normalize(fallback, max_stories)
    success = bool(parsed and normalized and final_list)

    # Phase 3: duplicate detection vs existing children (semantic)
    duplicate_matches: List[Dict[str, Any]] = []
    quality_score: float | None = None
    if success and final_list:
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
            existing_text_values = [t[1] for t in existing_texts]
            existing_vectors = embed_texts(existing_text_values) if existing_text_values else []
            story_texts = [s['title'] + '\n' + '\n'.join(s.get('acceptance_criteria', [])[:6]) for s in final_list]
            story_vectors = embed_texts(story_texts)
            # Build arrays for similarity
            existing_vec_map = [ev.vector for ev in existing_vectors]
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
                        "story_title": final_list[idx]['title'],
                        "existing_title": best_title,
                        "similarity": round(best_sim, 3)
                    })
        except Exception as dup_exc:  # pragma: no cover
            schema_warnings.append(f"duplicate detection error: {dup_exc}")

    # Quality score computation
    if success and final_list:
        total = len(final_list)
        dup_count = len(duplicate_matches)
        distinctness = 1 - (dup_count / total) if total else 0
        avg_criteria = 0.0
        if total:
            avg_criteria = sum(len(s.get('acceptance_criteria', []) or []) for s in final_list) / total
        criteria_density = min(1.0, avg_criteria / 6.0)
        all_warnings = schema_warnings + final_warn
        warnings_per_story = (len(all_warnings) / total) if total else 0
        warning_penalty = 1 - min(1.0, warnings_per_story / 5.0)
        structure_valid = 1.0 if success else 0.0
        quality_score = compute_quality_score(distinctness, criteria_density, warning_penalty, structure_valid)

    warnings_combined = schema_warnings + final_warn
    for dm in duplicate_matches:
        warnings_combined.append(
            f"possible duplicate story '{dm['story_title']}' similar to existing '{dm['existing_title']}' (sim={dm['similarity']})"
        )

    return {
        "success": success,
        "data": {"stories": final_list} if success else None,
        "raw_output": raw_text,
        "error": None if success else "model did not return valid schema",
        "warnings": warnings_combined,
        "duplicate_matches": duplicate_matches,
        "quality_score": quality_score,
        "warnings_count": len(warnings_combined),
    }


async def regenerate_story(epic_description: str, epic_id: str | None = None, guidance: str | None = None) -> Dict[str, Any]:
    """Regenerate a single improved story using the same guardrails and parsing.

    Returns the same envelope as decompose_epic, but with at most one story.
    """
    return await decompose_epic(epic_description=epic_description, max_stories=1, epic_id=epic_id, user_prompt=guidance)


async def _demo() -> None:
    epic = (
        "As a product team we need an in-app notification center so users can see real-time "
        "updates about project events (new comments, status changes, assignments) without refreshing."
    )
    outcome = await decompose_epic(epic, max_stories=5)
    print("=== Epic Decomposition Outcome ===")
    print(json.dumps(outcome, indent=2))


def run_demo_sync() -> None:
    """Convenience sync runner (not used by production code)."""
    asyncio.run(_demo())


if __name__ == "__main__":  # Allow module execution
    run_demo_sync()
