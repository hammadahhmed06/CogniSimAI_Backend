from __future__ import annotations

from uuid import UUID
from typing import List, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime


class AgentRunItemModel(BaseModel):
    id: UUID
    run_id: UUID
    item_index: int
    title: str
    acceptance_criteria: List[str] = Field(default_factory=list)
    created_issue_id: Optional[UUID] = None
    status: str = "proposed"
    metadata: Optional[dict[str, Any]] = None


class AgentRunModel(BaseModel):
    id: UUID
    agent_type: str
    action: str
    mode: str
    epic_id: Optional[UUID]
    user_id: UUID
    status: str
    input: Optional[dict[str, Any]] = None
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_issue_ids: Optional[list[UUID]] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    # Observability fields (may be null if legacy run)
    prompt_version: Optional[str] = None
    team_id: Optional[UUID] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    cost_usd_estimate: Optional[float] = None
    # Phase 3 metrics
    quality_score: Optional[float] = None
    warnings_count: Optional[int] = None


class AgentRunCreate(BaseModel):
    agent_type: str
    action: str
    mode: str
    epic_id: Optional[UUID]
    user_id: UUID
    status: str = "running"
    input: Optional[dict[str, Any]] = None
    prompt_version: Optional[str] = None
    team_id: Optional[UUID] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    cost_usd_estimate: Optional[float] = None
    quality_score: Optional[float] = None
    warnings_count: Optional[int] = None


class AgentRunUpdate(BaseModel):
    status: Optional[str] = None
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_issue_ids: Optional[list[UUID]] = None
    ended_at: Optional[datetime] = None
    prompt_version: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    cost_usd_estimate: Optional[float] = None
    quality_score: Optional[float] = None
    warnings_count: Optional[int] = None


class AgentRunItemCreate(BaseModel):
    run_id: UUID
    item_index: int
    title: str
    acceptance_criteria: List[str]
