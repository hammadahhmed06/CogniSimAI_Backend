Epic Decomposer Agent
======================

Purpose
-------
Turn an existing Epic (large product goal) into a concise, de‑duplicated set of user stories with normalized acceptance criteria and clear provenance (planned). This README consolidates and supersedes the older scattered docs (`EPIC_AGENT_MVP.md`, `FIRST_AGENT_REMAINING.md`).

Current Scope (Implemented)
--------------------------
- Single pass generation using Gemini (OpenAI‑compatible endpoint) via `openai-agents` SDK.
- Context enrichment: existing child stories fetched and summarized to avoid duplicates.
- Max story limit honored (hard bound 3–12; caller chooses `max_stories`).
- Dedup & pruning: case‑insensitive title uniqueness; empty titles removed.
- Acceptance criteria shaping (via prompt: 2–6 outcome‑oriented statements; no vague wording).
- Graceful failure path if `GEMINI_API_KEY` missing (returns structured error; no crash).

Not Yet Implemented (Planned Next)
----------------------------------
1. Run persistence (agent_runs & agent_run_items already migrated? If not, add) capturing prompt, model, tokens, latency.
2. Frontend run history widget + provenance badge linking to run detail modal.
3. Commit flow: user edits + bulk create stories; associate `origin_run_id` to created issues.
4. JSON validation & repair (schema enforcement + fallback mini‑parser / cascade to smaller model for fix‑ups).
5. Feedback loop: user rating + edit distance diff stored for continual prompt tuning.
6. Cost & token telemetry (prompt vs completion, model cost instrumentation, per‑project quotas).
7. Retrieval & validation tooling: semantics dedupe, criteria lint, domain glossary injection.
8. Multi‑turn refinement (regenerate a single weak story; partial commit UI).

Key File
--------
`epic_decomposer.py` — houses:
- `decompose_epic(epic_description, max_stories=6, epic_id=None)` main async function.
- Private helpers `_fetch_existing_children`, `_summarize_children`, `_safe_parse_json`.

High Level Flow
---------------
1. (Optional) Fetch existing child issues for the epic (title + first criteria) and summarize.
2. Build dynamic instructions (base + max story constraint + avoidance of duplicates).
3. Execute single `Runner.run` call with aggregated user prompt (EPIC_CONTEXT + EXISTING_CHILD_STORIES).
4. Parse JSON (strip code fences, attempt `json.loads`).
5. Normalize: prune invalid/empty, dedupe, enforce max count.
6. Return structured dict: `{ success, data, raw_output, error }`.

Environment & Setup
-------------------
Requirements:
```
pip install -r requirements.txt  # ensure openai-agents + dotenv present
export GEMINI_API_KEY=...        # or set in .env
```
Local Demo:
```
python -m app.agents.epic_decomposer
```
Returns a JSON object with candidate stories (when key present).

API Contract (Target)
---------------------
POST `/api/agents/epic/decompose`
Request:
```
{ "epic_id": "<uuid>", "max_stories": 6, "dry_run": true }
```
Future additions: `commit`, `stories` (edited client payload), `prompt_version`.

Sample Response (dry run):
```
{
  "epic_id": "<uuid>",
  "stories": [ { "title": "User can view notifications panel", "acceptance_criteria": ["..."] } ],
  "warnings": ["trimmed duplicate titles"],
  "model": "gemini-2.0-flash",
  "stub": false,
  "dry_run": true,
  "committed": false,
  "created_issue_ids": null,
  "run_id": null
}
```

Normalization Rules
-------------------
- Title non‑empty, <= ~120 chars (prompt bias keeps concise).
- Titles deduped (case‑insensitive).
- Acceptance criteria array required (future server validation); each trimmed; blank removed; cap (future) 12 lines.
- Enforce max story limit strictly after dedupe.

Error Modes
-----------
| Condition | Behavior |
|-----------|----------|
| Missing API key | `success=False`, error message returned |
| Model returns non‑JSON | `success=False`, raw text preserved |
| SDK/network exception | `success=False`, error str included |

Extensibility Hooks (Planned)
-----------------------------
- Tool interface: retrieval (search similar issues), validation (criteria style checker), scoring (complexity estimate) as Tools passed into Agent.
- Prompt versioning: `prompt_version` column stored per run for A/B comparisons.
- Multi‑model cascade: fast model first, fallback to higher quality if validation fails.

Observability Roadmap
---------------------
Add columns to `agent_runs`:
- `input_tokens`, `output_tokens`, `total_tokens`
- `latency_ms`
- `cost_usd_estimate`
Emit structured log lines: `agent=epic_decomposer run_id=... tokens=... ms=... success=true stories=5`.

Security & Governance
---------------------
- Avoid leaking unrelated project data: child fetch constrained to same epic only.
- Future: enforce workspace / project scoping before fetching context.
- Quotas: daily story generation limit per project (config) to control cost.

Testing Strategy
----------------
Short term:
- Unit test: dedupe + max limit enforcement.
- Unit test: invalid JSON path returns `success=False`.
- Mocked Supabase fetch to ensure empty context path still works.

Medium term (after persistence):
- Integration test: dry_run creates `agent_runs` row with status succeeded.
- Commit flow test: edited stories persist & provenance set.

Deletion / Consolidation Notice
-------------------------------
This file replaces the following removed docs: `EPIC_AGENT_MVP.md`, `FIRST_AGENT_REMAINING.md`.
Refer to `docs/REFACTOR_PLAN.md` for cross‑team phased roadmap.

Quick Dev Checklist (Active Slice)
----------------------------------
1. Persist run lifecycle (dry run) with tokens + latency.
2. Hub widget: recent runs list.
3. Run detail modal + provenance badge link.
4. Commit UI: edit + bulk create + provenance.
5. Feedback capture (rating + diff).

Contact / Ownership
-------------------
Owner: Agents / Intelligence sub‑team.
PR Review Guidelines: Confirm prompt changes documented with version tag; ensure normalization logic covered by tests.
