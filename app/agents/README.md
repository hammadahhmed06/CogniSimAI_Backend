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
Phase 1 (Observability Foundation) Status:
- [x] Migration: add prompt_version + token + latency + cost columns
- [x] Backend models updated
- [x] Instrument epic_decompose endpoint (latency_ms, token heuristics, cost)
- [x] API: existing /runs & /runs/{id} now expose metrics
- [ ] Frontend widget (recent runs + metrics)
- [ ] Optional backfill script (legacy rows zero-fill) – skipped for now

Phase 2 (Validation & Repair) Status:
- [x] Schema validation (strict field & type checks)
- [x] JSON repair cascade (balanced braces + heuristic bullet/numbered list reconstruction)
- [x] Acceptance criteria lint (vague terms, length, empty, >12 items) with warnings surfaced
- [x] Normalization & dedupe centralized in agent layer
- [x] Unit tests (schema valid/invalid, dedupe+limit, lint vague term, multi-stage repair)

Phase 3 (Retrieval & Quality Scoring) Status:
- [x] Migration: embeddings table + quality_score & warnings_count columns
- [x] Embedding utility (Gemini + offline fallback)
- [x] Duplicate semantic detection (threshold 0.85) with warnings
- [x] Quality score computation (distinctness, criteria density, warning penalty, structure)
- [x] Persist embeddings for newly created child issues (commit path)
- [x] Endpoint enhancement: expose duplicate_matches directly (list & detail endpoints updated)
- [x] Frontend: show quality_score badge & duplicate markers (EpicDecomposer page + components)
- [x] Accurate tokenizer integration replacing heuristic (tiktoken fallback to heuristic)

Phase 4 (Regeneration & Interactive Improvement) — Completed Core
Goal: Targeted refinement after initial generation, closing the loop between quality metrics and user feedback.

Delivered:
1. Per-story regeneration endpoint: `POST /api/agents/epic/decompose/{run_id}/stories/{index}/regenerate` (dry_run runs)
2. Per-story sub-scores: distinctness contribution & criteria density in regeneration response
3. Feedback capture: `POST /feedback` storing rating + edit distance diffs in `agent_run_items.metadata`
4. Adaptive prompt versioning: `prompt_version` auto-increments on each regeneration
5. Embedding reuse: only regenerated story re-embedded; existing issue embeddings fetched/cached
6. Guardrails: daily regeneration quota (100), per-run regen cap (20), cost/token estimation endpoint
7. Cost estimation: `GET /regenerate/estimate` returns token + USD estimate and remaining quota
8. Frontend modal integration: regeneration button, estimate button with token+cost display, feedback (rating+comment), duplicate warnings updated
9. Stub regeneration fallback: works without GEMINI_API_KEY (marks warning)

Success Criteria Met:
- Single-story regen leaves other stories intact & updates run output atomically
- Duplicate detection + quality recompute incremental
- Feedback persisted with edit distances
- Guardrails return 429 when exceeded

Phase 4 Residual Polish (Optional):
- [ ] Display prompt_version & regen_count in UI (currently backend only)
- [ ] Show sub-scores (distinctness, criteria_density) visually per regen
- [ ] Expose remaining daily regenerations from estimate endpoint in modal header
- [ ] Add before/after diff highlight for regenerated story
- [ ] Quality score badge live-update post-regeneration in modal
- [ ] Documentation snippet in main README explaining interactive loop
- [ ] Analytics aggregation job for feedback stats (future phase)

After Phase 4 the agent is interactive and ready for Phase 5 (feedback-driven optimization & A/B testing of prompts / scoring heuristics).

Phase 5 (Feedback-Driven Optimization & Experimentation) Plan
------------------------------------------------------------
Objective: Use collected feedback (ratings + edit distances + regeneration behavior) to iteratively tune prompts, improve quality_score, and validate changes via controlled experiments.

Initial Slice Implemented:
- [x] Aggregated feedback metrics endpoint: `GET /api/agents/feedback/metrics?days=30` (avg rating, edit distances, distribution, criteria density proxy).

Planned Iterations:
1. Prompt Variant Registry
  - Table: `prompt_variants(id, name, template, created_at, active, notes)`
  - Endpoint: list/create variants.
2. Experiment Runs
  - Allow specifying `prompt_variant_id` when generating; store on `agent_runs`.
  - Allocation strategy (A/B split) service.
3. Offline Evaluation Harness
  - Fixed sample of historical epics; replay with variant prompts; compute quality deltas.
4. Scoring Enhancements
  - Incorporate edit distance penalty/bonus into quality_score adjustments over time.
  - Track regression guard: block deployment if avg rating drops > X% vs baseline.
5. Analytics Dashboard (Frontend)
  - Charts: rating trend, edit distance distributions, criteria count distribution, duplicate rate.
6. Adaptive Prompt Selection (Later)
  - Auto-promote variant after N runs if statistically significant improvement.
7. Cost-Aware Scoring
  - Efficiency metric: quality_score per 1K tokens; highlight variants that improve cost efficiency.

Success KPIs:
- Maintain or improve avg rating (target ≥ baseline +5%).
- Reduce average edit_distance_title by ≥10% while criteria count stays within 3–6.
- Keep duplicate warning rate < 15% across runs.

Next Up (Actionable):
- Implement `prompt_variants` migration & CRUD endpoints.
- Add `prompt_variant_id` column to `agent_runs` for future A/B.


Next after Phase 3:
1. Per-story regeneration endpoint
2. Glossary/domain memory injection
3. Feedback loop scoring (user edits & acceptance) influencing prompt versioning
4. Cost governance (per-user/project quota + budget alerts)

Contact / Ownership
-------------------
Owner: Agents / Intelligence sub‑team.
PR Review Guidelines: Confirm prompt changes documented with version tag; ensure normalization logic covered by tests.
