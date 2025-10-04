# models/slack_models.py
# Pydantic models for Slack integration (workspace-level architecture)

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

# ==========================================
# Slack Integration Models (Workspace-Level)
# ==========================================

class SlackIntegrationResponse(BaseModel):
    """Workspace-level Slack integration details"""
    id: UUID
    workspace_id: UUID
    slack_workspace_id: str
    slack_workspace_name: Optional[str]
    slack_team_id: Optional[str]
    bot_user_id: Optional[str]
    default_channel_id: Optional[str]
    default_channel_name: Optional[str]
    notifications_enabled: bool
    slash_commands_enabled: bool
    webhook_url: Optional[str]
    scopes: List[str]
    installed_by: Optional[UUID]
    is_active: bool
    last_sync_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class CreateSlackIntegrationRequest(BaseModel):
    """Request to create Slack integration (from OAuth callback)"""
    slack_workspace_id: str = Field(..., min_length=1, max_length=50, description="Slack workspace ID (T0123456789)")
    slack_workspace_name: Optional[str] = Field(None, max_length=255, description="Workspace name")
    slack_team_id: Optional[str] = Field(None, max_length=50, description="Slack team ID")
    bot_user_id: Optional[str] = Field(None, max_length=50, description="Bot user ID (U0123456789)")
    bot_access_token: str = Field(..., min_length=1, description="Bot token (xoxb-...) - will be encrypted")
    user_access_token: Optional[str] = Field(None, description="User token (xoxp-...) - will be encrypted")
    default_channel_id: Optional[str] = Field(None, max_length=50, description="Default channel ID")
    default_channel_name: Optional[str] = Field(None, max_length=255, description="Default channel name")
    webhook_url: Optional[str] = Field(None, description="Incoming webhook URL")
    webhook_channel: Optional[str] = Field(None, max_length=50, description="Webhook channel ID")
    scopes: List[str] = Field(default_factory=list, description="OAuth scopes granted")


class UpdateSlackIntegrationRequest(BaseModel):
    """Request to update Slack integration settings"""
    default_channel_id: Optional[str] = Field(None, max_length=50)
    default_channel_name: Optional[str] = Field(None, max_length=255)
    notifications_enabled: Optional[bool] = None
    slash_commands_enabled: Optional[bool] = None
    is_active: Optional[bool] = None


# ==========================================
# Team Slack Configuration Models
# ==========================================

class TeamSlackConfigResponse(BaseModel):
    """Team-specific Slack channel configuration"""
    id: UUID
    team_id: UUID
    slack_integration_id: UUID
    channel_id: Optional[str]
    channel_name: Optional[str]
    notifications_enabled: bool
    mention_team_on_critical: bool
    created_at: datetime
    updated_at: datetime


class CreateTeamSlackConfigRequest(BaseModel):
    """Request to create team Slack config"""
    channel_id: str = Field(..., max_length=50, description="Slack channel ID (C0123456789)")
    channel_name: Optional[str] = Field(None, max_length=255, description="Channel name (#team-alpha)")
    notifications_enabled: bool = Field(default=True)
    mention_team_on_critical: bool = Field(default=True)


class UpdateTeamSlackConfigRequest(BaseModel):
    """Request to update team's Slack channel config"""
    channel_id: Optional[str] = Field(None, max_length=50)
    channel_name: Optional[str] = Field(None, max_length=255)
    notifications_enabled: Optional[bool] = None
    mention_team_on_critical: Optional[bool] = None


# ==========================================
# Slack Notification Models
# ==========================================

class SlackNotificationRequest(BaseModel):
    """Request to send a Slack notification"""
    channel_id: Optional[str] = Field(None, description="Channel ID (if None, uses team/workspace default)")
    message: str = Field(..., min_length=1, max_length=4000, description="Message text (required)")
    blocks: Optional[List[Dict[str, Any]]] = Field(None, description="Slack Block Kit blocks (optional)")
    thread_ts: Optional[str] = Field(None, description="Thread timestamp for replies (optional)")
    username: Optional[str] = Field(None, max_length=80, description="Override bot username")
    icon_emoji: Optional[str] = Field(None, max_length=100, description="Override bot icon emoji")


class SlackNotificationResponse(BaseModel):
    """Response after sending a Slack notification"""
    success: bool
    message_ts: Optional[str] = Field(None, description="Slack message timestamp (unique ID)")
    channel_id: Optional[str] = Field(None, description="Channel where message was sent")
    error: Optional[str] = Field(None, description="Error message if failed")


# ==========================================
# Slack Channel & User Models
# ==========================================

class SlackChannelResponse(BaseModel):
    """Slack channel information"""
    id: str = Field(..., description="Channel ID (C0123456789)")
    name: str = Field(..., description="Channel name (general)")
    is_channel: bool = Field(default=True, description="Is a channel (vs DM/group)")
    is_private: bool = Field(default=False, description="Is private channel")
    is_archived: bool = Field(default=False, description="Is archived")
    num_members: Optional[int] = Field(None, description="Number of members")


class SlackUserResponse(BaseModel):
    """Slack user information"""
    id: str = Field(..., description="User ID (U0123456789)")
    name: str = Field(..., description="Username")
    real_name: Optional[str] = Field(None, description="Real name")
    email: Optional[str] = Field(None, description="Email address")
    is_bot: bool = Field(default=False, description="Is a bot user")
    is_admin: bool = Field(default=False, description="Is workspace admin")


# ==========================================
# Slack OAuth Models
# ==========================================

class SlackOAuthInitResponse(BaseModel):
    """Response for OAuth initiation"""
    authorization_url: str = Field(..., description="URL to redirect user for OAuth")
    state: str = Field(..., description="CSRF protection state parameter")
    expires_at: datetime = Field(..., description="When the state expires")


class SlackOAuthCallbackRequest(BaseModel):
    """OAuth callback data from Slack"""
    code: str = Field(..., description="OAuth authorization code")
    state: str = Field(..., description="State parameter for CSRF validation")


class SlackOAuthStateRecord(BaseModel):
    """OAuth state record for CSRF protection"""
    id: UUID
    state: str
    workspace_id: UUID
    user_id: UUID
    redirect_uri: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    is_used: bool = False


class SlackOAuthCallbackResponse(BaseModel):
    """Response after successful OAuth"""
    success: bool
    message: str
    integration_id: Optional[UUID] = None
    workspace_id: Optional[UUID] = None


# ==========================================
# Slack Webhook Event Models
# ==========================================

class SlackWebhookEventRequest(BaseModel):
    """Incoming Slack webhook event"""
    token: Optional[str] = Field(None, description="Verification token")
    team_id: Optional[str] = Field(None, description="Slack team/workspace ID")
    api_app_id: Optional[str] = Field(None, description="App ID")
    event: Optional[Dict[str, Any]] = Field(None, description="Event payload")
    type: str = Field(..., description="Event type (url_verification, event_callback, etc.)")
    challenge: Optional[str] = Field(None, description="Challenge for URL verification")


class SlackSlashCommandRequest(BaseModel):
    """Incoming Slack slash command"""
    command: str = Field(..., description="Command name (/cognisim)")
    text: str = Field(default="", description="Command arguments")
    user_id: str = Field(..., description="User who invoked command")
    user_name: str = Field(..., description="Username")
    channel_id: str = Field(..., description="Channel where invoked")
    channel_name: str = Field(..., description="Channel name")
    team_id: str = Field(..., description="Slack workspace ID")
    team_domain: str = Field(..., description="Workspace domain")
    trigger_id: str = Field(..., description="Trigger ID for modals")


# ==========================================
# Integration Status Model
# ==========================================

class SlackIntegrationStatusResponse(BaseModel):
    """Status of Slack integration"""
    is_connected: bool
    workspace_id: Optional[UUID] = None
    workspace_name: Optional[str] = None
    slack_workspace_name: Optional[str] = None
    default_channel_name: Optional[str] = None
    notifications_enabled: bool = False
    slash_commands_enabled: bool = False
    last_sync_at: Optional[datetime] = None
    installed_by_email: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)
