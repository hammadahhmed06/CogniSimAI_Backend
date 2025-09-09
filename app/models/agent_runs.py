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


class AgentRunCreate(BaseModel):
    agent_type: str
    action: str
    mode: str
    epic_id: Optional[UUID]
    user_id: UUID
    status: str = "running"
    input: Optional[dict[str, Any]] = None


class AgentRunUpdate(BaseModel):
    status: Optional[str] = None
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_issue_ids: Optional[list[UUID]] = None
    ended_at: Optional[datetime] = None


class AgentRunItemCreate(BaseModel):
    run_id: UUID
    item_index: int
    title: str
    acceptance_criteria: List[str]
