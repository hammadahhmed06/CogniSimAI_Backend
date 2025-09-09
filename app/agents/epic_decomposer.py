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
from typing import Any, Dict, List, Tuple

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
# Explicit model import (doc-aligned)


# Load .env (silent if missing)
load_dotenv(find_dotenv())

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    # Clear feedback but do not raise so imports (e.g., test discovery) still succeed.
    print("[epic_decomposer] Warning: GEMINI_API_KEY not set. Skipping live calls.")


# Gemini via OpenAI-compatible endpoint (reference: https://ai.google.dev/gemini-api/docs/openai)
client = AsyncOpenAI(
    api_key=GEMINI_API_KEY or "",  # Empty key allows graceful failure path
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
)


BASE_INSTRUCTIONS = (
    "You are an Epic Decomposition Assistant. You will receive an EPIC_CONTEXT section. "
    "Generate user stories ONLY for scope actually described there. Avoid duplication of existing child stories. "
    "Return STRICT JSON: {\\n  epic: string,\\n  stories: [ { title: string, acceptance_criteria: string[] } ]\\n}. "
    "Titles: concise (<= 12 words), distinct, user-value oriented. Acceptance criteria: 2-6 bullet-quality statements phrased as observable outcomes (no future tense, no vague words like 'should work'). "
    "No markdown, no explanations outside JSON."
)


def _safe_parse_json(raw: str) -> Dict[str, Any] | None:
    """Attempt to parse the model output as JSON, trimming common formatting artifacts."""
    candidate = raw.strip().strip("` ")
    if candidate.startswith("json\n"):
        candidate = candidate[5:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
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


async def decompose_epic(epic_description: str, max_stories: int = 6, epic_id: str | None = None) -> Dict[str, Any]:
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

    dynamic_instructions = (
        f"{BASE_INSTRUCTIONS}\n" \
        f"Required story count: up to {max_stories} stories (fewer allowed if scope is small). Do NOT exceed {max_stories}. "
        f"If functionality already represented in EXISTING_CHILD_STORIES skip it."
    )

    agent = Agent(
        name="EpicDecomposer",
        instructions=dynamic_instructions,
        model=OpenAIChatCompletionsModel(
            model="gemini-2.0-flash",
            openai_client=client,
        ),
    )

    user_prompt = (
        "EPIC_CONTEXT:\n" + epic_description + "\n\n" +
        "EXISTING_CHILD_STORIES:\n" + children_summary + "\n\n" +
        "Respond with JSON now."
    )

    try:
        result = await Runner.run(agent, user_prompt)
    except Exception as exc:  # Broad catch to return structured error
        return {
            "success": False,
            "data": None,
            "raw_output": None,
            "error": f"agent run failed: {exc}",
        }

    raw_text = getattr(result, "final_output", str(result))
    parsed = _safe_parse_json(raw_text) or None
    # Enforce max count & prune duplicates if parsed
    if parsed and isinstance(parsed, dict) and isinstance(parsed.get('stories'), list):
        seen = set()
        deduped = []
        for s in parsed['stories']:
            title = (s.get('title') if isinstance(s, dict) else None) or ''
            norm = title.strip().lower()
            if not title.strip():
                continue
            if norm in seen:
                continue
            seen.add(norm)
            deduped.append(s)
            if len(deduped) >= max_stories:
                break
        parsed['stories'] = deduped
    return {
        "success": parsed is not None,
        "data": parsed,
        "raw_output": raw_text,
        "error": None if parsed is not None else "model did not return valid JSON",
    }


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
