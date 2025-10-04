# models/team_models.py
# Pydantic models for Team Management System

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date, time
from uuid import UUID
from decimal import Decimal


# ============= Team Settings Models =============

class TeamSettingsResponse(BaseModel):
    id: UUID
    team_id: UUID
    timezone: str
    working_hours_start: time
    working_hours_end: time
    working_days: List[int]
    sprint_length_days: int
    velocity_tracking_enabled: bool
    created_at: datetime
    updated_at: datetime


class UpdateTeamSettingsRequest(BaseModel):
    timezone: Optional[str] = None
    working_hours_start: Optional[time] = None
    working_hours_end: Optional[time] = None
    working_days: Optional[List[int]] = None
    sprint_length_days: Optional[int] = Field(None, ge=1, le=30)
    velocity_tracking_enabled: Optional[bool] = None


# ============= Team Metrics Models =============

class VelocityDataPoint(BaseModel):
    date: date
    velocity: Optional[Decimal]
    stories_completed: int


class VelocityResponse(BaseModel):
    team_id: UUID
    period_days: int
    data_points: List[VelocityDataPoint]
    average_velocity: Optional[Decimal]
    trend: Optional[str]  # "increasing", "stable", "decreasing"


class CycleTimeDataPoint(BaseModel):
    date: date
    avg_cycle_time_hours: Optional[Decimal]
    issues_count: int


class CycleTimeResponse(BaseModel):
    team_id: UUID
    period_days: int
    data_points: List[CycleTimeDataPoint]
    average_cycle_time_hours: Optional[Decimal]
    trend: Optional[str]


class WorkloadMember(BaseModel):
    user_id: UUID
    user_name: str
    user_email: str
    assigned_issues: int
    in_progress_issues: int
    story_points: Optional[Decimal]
    capacity_utilization: Optional[Decimal]  # percentage


class WorkloadResponse(BaseModel):
    team_id: UUID
    members: List[WorkloadMember]
    total_issues: int
    total_in_progress: int
    average_workload: Decimal


class SprintCompletionData(BaseModel):
    sprint_id: UUID
    sprint_name: str
    start_date: Optional[date]
    end_date: Optional[date]
    committed_points: Decimal
    completed_points: Decimal
    completion_rate: Decimal  # percentage


class SprintCompletionResponse(BaseModel):
    team_id: UUID
    sprints: List[SprintCompletionData]
    average_completion_rate: Decimal
    trend: Optional[str]


class TeamMetricsSummary(BaseModel):
    team_id: UUID
    team_name: str
    
    # Current sprint info
    current_sprint_id: Optional[UUID]
    current_sprint_name: Optional[str]
    current_sprint_progress: Optional[Decimal]  # percentage
    
    # Velocity metrics
    current_velocity: Optional[Decimal]
    average_velocity_30d: Optional[Decimal]
    velocity_trend: Optional[str]
    
    # Cycle time metrics
    avg_cycle_time_hours: Optional[Decimal]
    cycle_time_trend: Optional[str]
    
    # Workload metrics
    total_active_issues: int
    total_in_progress: int
    team_member_count: int
    avg_workload_per_member: Decimal
    
    # Sprint completion
    last_sprint_completion_rate: Optional[Decimal]
    avg_sprint_completion_rate: Optional[Decimal]
    
    # Quality metrics
    bugs_fixed_30d: int
    bugs_created_30d: int
    bug_fix_rate: Optional[Decimal]
    
    # Timestamps
    calculated_at: datetime


# ============= Team Capacity Models =============

class TeamCapacityMember(BaseModel):
    user_id: UUID
    user_name: str
    user_email: str
    capacity_points: Decimal
    committed_points: Decimal
    completed_points: Decimal
    availability_percent: int
    notes: Optional[str]


class TeamCapacityResponse(BaseModel):
    team_id: UUID
    sprint_id: Optional[UUID]
    sprint_name: Optional[str]
    members: List[TeamCapacityMember]
    total_capacity: Decimal
    total_committed: Decimal
    total_completed: Decimal
    capacity_utilization: Decimal  # percentage


class SetCapacityMemberRequest(BaseModel):
    user_id: UUID
    capacity_points: Decimal = Field(..., ge=0)
    availability_percent: int = Field(100, ge=0, le=100)
    notes: Optional[str] = None


class SetCapacityRequest(BaseModel):
    sprint_id: Optional[UUID] = None  # If None, uses current/next sprint
    members: List[SetCapacityMemberRequest]


class UpdateCapacityRequest(BaseModel):
    capacity_points: Optional[Decimal] = Field(None, ge=0)
    committed_points: Optional[Decimal] = Field(None, ge=0)
    completed_points: Optional[Decimal] = Field(None, ge=0)
    availability_percent: Optional[int] = Field(None, ge=0, le=100)
    notes: Optional[str] = None


# ============= Helper Models =============

class DateRangeFilter(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    days: Optional[int] = Field(None, ge=1, le=365)


class TrendIndicator(BaseModel):
    direction: str  # "up", "down", "stable"
    percentage_change: Optional[Decimal]
    is_positive: bool  # Whether the trend is good for this metric


# ============= Sprint 2: Team Goals/OKRs Models =============

class TeamGoalResponse(BaseModel):
    id: UUID
    team_id: UUID
    title: str
    description: Optional[str]
    goal_type: str  # okr, kpi, target
    target_value: Optional[Decimal]
    current_value: Decimal
    unit: Optional[str]
    quarter: Optional[str]
    status: str  # active, achieved, at_risk, abandoned
    owner_user_id: Optional[UUID]
    owner_name: Optional[str]
    due_date: Optional[date]
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    progress_percentage: Optional[Decimal]  # calculated field


class CreateGoalRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    goal_type: str = Field("okr", pattern="^(okr|kpi|target)$")
    target_value: Optional[Decimal] = Field(None, ge=0)
    current_value: Decimal = Field(default=Decimal(0), ge=0)
    unit: Optional[str] = Field(None, max_length=50)
    quarter: Optional[str] = Field(None, max_length=10)
    owner_user_id: Optional[UUID] = None
    due_date: Optional[date] = None


class UpdateGoalRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = None
    target_value: Optional[Decimal] = Field(None, ge=0)
    current_value: Optional[Decimal] = Field(None, ge=0)
    unit: Optional[str] = Field(None, max_length=50)
    quarter: Optional[str] = Field(None, max_length=10)
    status: Optional[str] = Field(None, pattern="^(active|achieved|at_risk|abandoned)$")
    owner_user_id: Optional[UUID] = None
    due_date: Optional[date] = None


# ============= Sprint 2: Notification Settings Models =============

class NotificationSettingsResponse(BaseModel):
    id: UUID
    team_id: UUID
    user_id: UUID
    email_daily_digest: bool
    email_sprint_summary: bool
    email_mentions: bool
    email_assignments: bool
    slack_notifications: bool
    slack_webhook_url: Optional[str]
    created_at: datetime
    updated_at: datetime


class UpdateNotificationSettingsRequest(BaseModel):
    email_daily_digest: Optional[bool] = None
    email_sprint_summary: Optional[bool] = None
    email_mentions: Optional[bool] = None
    email_assignments: Optional[bool] = None
    slack_notifications: Optional[bool] = None
    slack_webhook_url: Optional[str] = None


# ============= Sprint 2: Default Assignees Models =============

class DefaultAssigneeResponse(BaseModel):
    id: UUID
    team_id: UUID
    issue_type: Optional[str]
    priority: Optional[str]
    assignee_user_id: UUID
    assignee_name: str
    assignee_email: str
    created_at: datetime


class SetDefaultAssigneeRequest(BaseModel):
    issue_type: Optional[str] = Field(None, max_length=100)
    priority: Optional[str] = Field(None, max_length=50)
    assignee_user_id: UUID


class DeleteDefaultAssigneeRequest(BaseModel):
    issue_type: Optional[str] = None
    priority: Optional[str] = None


# ============= Sprint 2: Team Labels Models =============

class TeamLabelResponse(BaseModel):
    id: UUID
    team_id: UUID
    name: str
    color: str
    description: Optional[str]
    created_at: datetime


class CreateLabelRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field("#3B82F6", pattern="^#[0-9A-Fa-f]{6}$")
    description: Optional[str] = None


class UpdateLabelRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    color: Optional[str] = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    description: Optional[str] = None


# =====================================================
# Sprint 3: Collaboration & Resources Models
# =====================================================

class ResourceCategoryResponse(BaseModel):
    id: UUID
    team_id: UUID
    name: str
    description: Optional[str] = None
    color: str
    icon: str
    parent_category_id: Optional[UUID] = None
    display_order: int
    created_at: datetime
    updated_at: datetime


class CreateCategoryRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    color: str = Field("#3B82F6", pattern="^#[0-9A-Fa-f]{6}$")
    icon: str = Field("folder", max_length=50)
    parent_category_id: Optional[UUID] = None
    display_order: int = Field(0, ge=0)


class UpdateCategoryRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    icon: Optional[str] = Field(None, max_length=50)
    parent_category_id: Optional[UUID] = None
    display_order: Optional[int] = Field(None, ge=0)


class ResourceResponse(BaseModel):
    id: UUID
    team_id: UUID
    category_id: Optional[UUID] = None
    title: str
    description: Optional[str] = None
    resource_type: str
    url: Optional[str] = None
    content: Optional[str] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    tags: list[str] = []
    is_pinned: bool
    is_archived: bool
    view_count: int
    last_viewed_at: Optional[datetime] = None
    created_by: Optional[UUID] = None
    created_by_name: Optional[str] = None
    created_by_email: Optional[str] = None
    updated_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class CreateResourceRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    resource_type: str = Field(..., pattern="^(link|document|file|code_snippet|video|image)$")
    url: Optional[str] = Field(None, max_length=2000)
    content: Optional[str] = None
    category_id: Optional[UUID] = None
    tags: list[str] = Field(default_factory=list)
    is_pinned: bool = False
    file_size_bytes: Optional[int] = Field(None, ge=0)
    mime_type: Optional[str] = Field(None, max_length=100)


class UpdateResourceRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = None
    resource_type: Optional[str] = Field(None, pattern="^(link|document|file|code_snippet|video|image)$")
    url: Optional[str] = Field(None, max_length=2000)
    content: Optional[str] = None
    category_id: Optional[UUID] = None
    tags: Optional[list[str]] = None
    is_pinned: Optional[bool] = None
    is_archived: Optional[bool] = None


class ChatMessageResponse(BaseModel):
    id: UUID
    team_id: UUID
    parent_message_id: Optional[UUID] = None
    message: str
    message_type: str
    mentioned_user_ids: list[UUID] = []
    reactions: dict = {}
    is_edited: bool
    edited_at: Optional[datetime] = None
    user_id: Optional[UUID] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    is_pinned: bool
    is_deleted: bool
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CreateChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    message_type: str = Field("text", pattern="^(text|announcement|system|file_share)$")
    parent_message_id: Optional[UUID] = None
    mentioned_user_ids: list[UUID] = Field(default_factory=list)


class UpdateChatMessageRequest(BaseModel):
    message: Optional[str] = Field(None, min_length=1, max_length=10000)
    is_pinned: Optional[bool] = None
    is_deleted: Optional[bool] = None


class AddReactionRequest(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=10)

