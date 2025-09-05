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
from typing import Any, Dict

from dotenv import load_dotenv, find_dotenv

from openai import AsyncOpenAI  # Official OpenAI client (used with Gemini-compatible base_url)
from agents import Agent, Runner,OpenAIChatCompletionsModel  # Core SDK primitives
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


EPIC_INSTRUCTIONS = (
    "You are an Epic Decomposition Assistant. Return ONLY valid JSON with keys: "
    "epic (string), stories (array of objects with title and acceptance_criteria[]). "
    "3-8 distinct stories, concise titles (<= 12 words), each with 2-6 clear acceptance criteria."
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


async def decompose_epic(epic_description: str) -> Dict[str, Any]:
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

    agent = Agent(
        name="EpicDecomposer",
        instructions=EPIC_INSTRUCTIONS,
        model=OpenAIChatCompletionsModel(
            model="gemini-2.0-flash",
            openai_client=client,
        ),
    )

    user_prompt = f"Decompose this epic:\n{epic_description}".strip()

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
    outcome = await decompose_epic(epic)
    print("=== Epic Decomposition Outcome ===")
    print(json.dumps(outcome, indent=2))


def run_demo_sync() -> None:
    """Convenience sync runner (not used by production code)."""
    asyncio.run(_demo())


if __name__ == "__main__":  # Allow module execution
    run_demo_sync()
