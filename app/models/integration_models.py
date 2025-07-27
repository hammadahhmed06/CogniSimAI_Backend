# models/integration_models.py
# Pydantic models for Jira integration

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID
from enum import Enum


class IntegrationType(str, Enum):
    JIRA = "jira"
    GITHUB = "github"
    SLACK = "slack"


class ConnectionStatus(str, Enum):
    PENDING = "pending"
    CONNECTED = "connected"
    FAILED = "failed"
    DISCONNECTED = "disconnected"


class SyncStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class SyncType(str, Enum):
    MANUAL = "manual"
    AUTO = "auto"
    INITIAL = "initial"


# Request/Response Models
class JiraConnectionRequest(BaseModel):
    jira_url: str = Field(..., description="Jira instance URL")
    jira_email: EmailStr = Field(..., description="Jira user email")
    jira_api_token: str = Field(..., description="Jira API token")


class JiraConnectionResponse(BaseModel):
    success: bool
    message: str
    connection_status: ConnectionStatus
    integration_id: Optional[UUID] = None


class JiraSyncRequest(BaseModel):
    jira_project_key: str = Field(..., description="Jira project key (e.g., 'PROJ')")
    max_results: Optional[int] = Field(default=100, description="Maximum number of issues to sync")


class JiraSyncResponse(BaseModel):
    success: bool
    message: str
    sync_log_id: UUID
    items_synced: int
    items_created: int
    items_updated: int
    errors_count: int
    sync_status: SyncStatus


class IntegrationCredentials(BaseModel):
    id: UUID
    workspace_id: UUID
    integration_type: IntegrationType
    jira_url: str
    jira_email: str
    is_active: bool
    connection_status: ConnectionStatus
    last_tested_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class SyncLog(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: Optional[UUID]
    integration_type: IntegrationType
    sync_type: SyncType
    status: SyncStatus
    items_synced: int
    items_created: int
    items_updated: int
    errors_count: int
    sync_details: Optional[Dict[str, Any]]
    error_details: Optional[Dict[str, Any]]
    started_at: datetime
    completed_at: Optional[datetime]


class IntegrationMapping(BaseModel):
    id: UUID
    item_id: UUID
    external_system: str
    external_item_id: str
    external_url: Optional[str]
    last_synced_at: Optional[datetime]
    created_at: datetime


class AvailableProject(BaseModel):
    key: str
    name: str
    id: Optional[str] = None
    description: Optional[str] = None


class SyncHistoryResponse(BaseModel):
    sync_logs: List[SyncLog]
    total_count: int
    page: int
    page_size: int


class IntegrationStatusResponse(BaseModel):
    is_connected: bool
    connection_status: ConnectionStatus
    integration_type: IntegrationType
    jira_url: Optional[str]
    jira_email: Optional[str]
    last_tested_at: Optional[datetime]
    last_sync_at: Optional[datetime]
    available_projects: List[AvailableProject] = Field(default=[], description="Available Jira projects")
