# api/routes/slack_integration.py
# API endpoints for Slack integration (workspace-level)

import logging
from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.responses import JSONResponse, RedirectResponse
from datetime import datetime

from app.models.slack_models import (
    SlackIntegrationResponse,
    CreateSlackIntegrationRequest,
    UpdateSlackIntegrationRequest,
    TeamSlackConfigResponse,
    UpdateTeamSlackConfigRequest,
    SlackNotificationRequest,
    SlackNotificationResponse,
    SlackChannelResponse,
    SlackUserResponse,
    SlackIntegrationStatusResponse,
    SlackOAuthInitResponse,
    SlackOAuthCallbackRequest
)
from app.services.slack.slack_client import SlackClient
from app.services.slack.slack_oauth_service import get_slack_oauth_service
from app.services.encryption.token_encryption import get_token_encryption_service
from app.core.dependencies import get_current_user, UserModel, supabase, limiter

logger = logging.getLogger("cognisim_ai")

# Create router for Slack integration endpoints
router = APIRouter(prefix="/api", tags=["slack-integration"])


def get_workspace_id_from_user(request: Request, current_user: UserModel = Depends(get_current_user)) -> str:
    """Resolve active workspace for current user."""
    user_id = str(current_user.id)
    header_wid = request.headers.get("X-Workspace-Id")

    # Helper to validate membership
    def _validate(wid: str) -> bool:
        try:
            res = (
                supabase.table("workspace_members")
                .select("id")
                .eq("workspace_id", wid)
                .eq("user_id", user_id)
                .eq("status", "active")
                .limit(1)
                .execute()
            )
            return bool(getattr(res, "data", []))
        except Exception as e:
            logger.warning(f"Membership validation error for user {user_id} workspace {wid}: {e}")
            return False

    # 1. Header provided
    if header_wid and _validate(header_wid):
        return header_wid
    if header_wid and not _validate(header_wid):
        logger.warning(f"User {user_id} attempted access to non-member workspace {header_wid}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of workspace")

    # 2. Fallback to first membership
    try:
        try:
            rpc_res = supabase.rpc("list_user_workspaces", {"p_user_id": user_id}).execute()
            rows = getattr(rpc_res, "data", []) or []
        except Exception:
            rows = []
        wid = rows[0]["id"] if rows else None
        if not wid:
            # Manual fallback join
            join_res = (
                supabase.table("workspace_members")
                .select("workspace_id")
                .eq("user_id", user_id)
                .eq("status", "active")
                .limit(1)
                .execute()
            )
            data = getattr(join_res, "data", []) or []
            wid = data[0]["workspace_id"] if data else None
        if not wid:
            raise HTTPException(status_code=404, detail="User has no workspaces")
        return str(wid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workspace resolution failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to resolve workspace")


def verify_workspace_admin(workspace_id: str, current_user: UserModel = Depends(get_current_user)) -> bool:
    """Verify current user is admin of workspace."""
    try:
        res = (
            supabase.table("workspace_members")
            .select("role")
            .eq("workspace_id", workspace_id)
            .eq("user_id", str(current_user.id))
            .eq("status", "active")
            .single()
            .execute()
        )
        member = getattr(res, "data", None)
        if not member or member.get("role") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required for workspace integrations"
            )
        return True
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin verification failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify admin access")


# ============================================================================
# WORKSPACE-LEVEL ENDPOINTS (Admin only)
# ============================================================================

@router.get(
    "/workspaces/{workspace_id}/slack",
    response_model=SlackIntegrationResponse,
    summary="Get Slack Integration",
    description="Get Slack integration details for workspace"
)
async def get_slack_integration(
    workspace_id: UUID,
    current_user: UserModel = Depends(get_current_user)
) -> SlackIntegrationResponse:
    """Get Slack integration details for workspace."""
    try:
        # Verify workspace membership
        workspace_id_str = str(workspace_id)
        res = (
            supabase.table("workspace_members")
            .select("id")
            .eq("workspace_id", workspace_id_str)
            .eq("user_id", str(current_user.id))
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if not getattr(res, "data", []):
            raise HTTPException(status_code=403, detail="Not a member of this workspace")
        
        # Get integration
        integration_res = (
            supabase.table("slack_integrations")
            .select("*")
            .eq("workspace_id", workspace_id_str)
            .eq("is_active", True)
            .single()
            .execute()
        )
        
        integration = getattr(integration_res, "data", None)
        if not integration:
            raise HTTPException(status_code=404, detail="Slack integration not found")
        
        # Don't return encrypted token
        integration["bot_token"] = "***ENCRYPTED***"
        
        return SlackIntegrationResponse(**integration)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get Slack integration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/workspaces/{workspace_id}/slack",
    response_model=SlackIntegrationResponse,
    summary="Create Slack Integration",
    description="Connect Slack to workspace (admin only)"
)
@limiter.limit("5/minute")
async def create_slack_integration(
    request: Request,
    workspace_id: UUID,
    integration_request: CreateSlackIntegrationRequest,
    current_user: UserModel = Depends(get_current_user)
) -> SlackIntegrationResponse:
    """Create Slack integration for workspace (admin only)."""
    try:
        workspace_id_str = str(workspace_id)
        
        # Verify admin access
        verify_workspace_admin(workspace_id_str, current_user)
        
        # Encrypt bot token
        encryption_service = get_token_encryption_service()
        encrypted_token = encryption_service.encrypt(integration_request.bot_access_token)
        
        # Test connection before saving
        try:
            test_client = SlackClient(integration_request.bot_access_token, is_encrypted=False)
            success, message = test_client.test_connection()
            
            if not success:
                raise HTTPException(status_code=400, detail=f"Slack connection failed: {message}")
            
            logger.info(f"Slack connection test successful: {message}")
            
        except Exception as e:
            logger.error(f"Slack connection test failed: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to connect to Slack: {str(e)}")
        
        # Check if integration already exists
        existing_res = (
            supabase.table("slack_integrations")
            .select("id")
            .eq("workspace_id", workspace_id_str)
            .limit(1)
            .execute()
        )
        
        if getattr(existing_res, "data", []):
            raise HTTPException(status_code=400, detail="Slack integration already exists. Use PATCH to update.")
        
        # Create integration
        integration_data = {
            "workspace_id": workspace_id_str,
            "bot_token": encrypted_token,
            "slack_workspace_id": integration_request.slack_workspace_id,
            "slack_workspace_name": integration_request.slack_workspace_name,
            "slack_team_id": integration_request.slack_team_id,
            "bot_user_id": integration_request.bot_user_id,
            "default_channel_id": integration_request.default_channel_id,
            "default_channel_name": integration_request.default_channel_name,
            "webhook_url": integration_request.webhook_url,
            "scopes": integration_request.scopes,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        insert_res = (
            supabase.table("slack_integrations")
            .insert(integration_data)
            .execute()
        )
        
        integration = getattr(insert_res, "data", [None])[0]
        if not integration:
            raise HTTPException(status_code=500, detail="Failed to create Slack integration")
        
        logger.info(f"Slack integration created for workspace {workspace_id_str}")
        
        # Don't return encrypted token
        integration["bot_token"] = "***ENCRYPTED***"
        
        return SlackIntegrationResponse(**integration)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create Slack integration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch(
    "/workspaces/{workspace_id}/slack",
    response_model=SlackIntegrationResponse,
    summary="Update Slack Integration",
    description="Update Slack integration (admin only)"
)
async def update_slack_integration(
    workspace_id: UUID,
    update_request: UpdateSlackIntegrationRequest,
    current_user: UserModel = Depends(get_current_user)
) -> SlackIntegrationResponse:
    """Update Slack integration (admin only)."""
    try:
        workspace_id_str = str(workspace_id)
        
        # Verify admin access
        verify_workspace_admin(workspace_id_str, current_user)
        
        # Get existing integration
        existing_res = (
            supabase.table("slack_integrations")
            .select("*")
            .eq("workspace_id", workspace_id_str)
            .single()
            .execute()
        )
        
        existing = getattr(existing_res, "data", None)
        if not existing:
            raise HTTPException(status_code=404, detail="Slack integration not found")
        
        # Build update data
        update_data = {"updated_at": datetime.utcnow().isoformat()}
        
        if update_request.default_channel_id:
            update_data["default_channel_id"] = update_request.default_channel_id
        
        if update_request.default_channel_name:
            update_data["default_channel_name"] = update_request.default_channel_name
        
        if update_request.notifications_enabled is not None:
            update_data["notifications_enabled"] = update_request.notifications_enabled  # type: ignore
        
        if update_request.slash_commands_enabled is not None:
            update_data["slash_commands_enabled"] = update_request.slash_commands_enabled  # type: ignore
        
        if update_request.is_active is not None:
            update_data["is_active"] = update_request.is_active  # type: ignore
        
        # Update integration
        update_res = (
            supabase.table("slack_integrations")
            .update(update_data)
            .eq("workspace_id", workspace_id_str)
            .execute()
        )
        
        integration = getattr(update_res, "data", [None])[0]
        if not integration:
            raise HTTPException(status_code=500, detail="Failed to update Slack integration")
        
        logger.info(f"Slack integration updated for workspace {workspace_id_str}")
        
        # Don't return encrypted token
        integration["bot_token"] = "***ENCRYPTED***"
        
        return SlackIntegrationResponse(**integration)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update Slack integration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/workspaces/{workspace_id}/slack",
    summary="Delete Slack Integration",
    description="Delete Slack integration (admin only)"
)
async def delete_slack_integration(
    workspace_id: UUID,
    current_user: UserModel = Depends(get_current_user)
) -> JSONResponse:
    """Delete Slack integration (admin only)."""
    try:
        workspace_id_str = str(workspace_id)
        
        # Verify admin access
        verify_workspace_admin(workspace_id_str, current_user)
        
        # Delete integration (cascade will delete team configs)
        delete_res = (
            supabase.table("slack_integrations")
            .delete()
            .eq("workspace_id", workspace_id_str)
            .execute()
        )
        
        if not getattr(delete_res, "data", []):
            raise HTTPException(status_code=404, detail="Slack integration not found")
        
        logger.info(f"Slack integration deleted for workspace {workspace_id_str}")
        
        return JSONResponse(
            status_code=200,
            content={"message": "Slack integration deleted successfully"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete Slack integration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/workspaces/{workspace_id}/slack/test",
    response_model=SlackIntegrationStatusResponse,
    summary="Test Slack Connection",
    description="Test Slack integration connection"
)
async def test_slack_connection(
    workspace_id: UUID,
    current_user: UserModel = Depends(get_current_user)
) -> SlackIntegrationStatusResponse:
    """Test Slack integration connection."""
    try:
        workspace_id_str = str(workspace_id)
        
        # Verify workspace membership
        res = (
            supabase.table("workspace_members")
            .select("id")
            .eq("workspace_id", workspace_id_str)
            .eq("user_id", str(current_user.id))
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if not getattr(res, "data", []):
            raise HTTPException(status_code=403, detail="Not a member of this workspace")
        
        # Get integration
        integration_res = (
            supabase.table("slack_integrations")
            .select("bot_token, is_active")
            .eq("workspace_id", workspace_id_str)
            .single()
            .execute()
        )
        
        integration = getattr(integration_res, "data", None)
        if not integration:
            raise HTTPException(status_code=404, detail="Slack integration not found")
        
        # Test connection
        client = SlackClient(integration["bot_token"], is_encrypted=True)
        success, message = client.test_connection()
        
        return SlackIntegrationStatusResponse(
            is_connected=success,
            workspace_id=workspace_id,
            notifications_enabled=integration.get("notifications_enabled", False),
            slash_commands_enabled=integration.get("slash_commands_enabled", False)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to test Slack connection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/workspaces/{workspace_id}/slack/channels",
    response_model=List[SlackChannelResponse],
    summary="List Slack Channels",
    description="List available Slack channels in workspace"
)
async def list_slack_channels(
    workspace_id: UUID,
    current_user: UserModel = Depends(get_current_user)
) -> List[SlackChannelResponse]:
    """List available Slack channels."""
    try:
        workspace_id_str = str(workspace_id)
        
        # Verify workspace membership
        res = (
            supabase.table("workspace_members")
            .select("id")
            .eq("workspace_id", workspace_id_str)
            .eq("user_id", str(current_user.id))
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if not getattr(res, "data", []):
            raise HTTPException(status_code=403, detail="Not a member of this workspace")
        
        # Get integration
        integration_res = (
            supabase.table("slack_integrations")
            .select("bot_token")
            .eq("workspace_id", workspace_id_str)
            .eq("is_active", True)
            .single()
            .execute()
        )
        
        integration = getattr(integration_res, "data", None)
        if not integration:
            raise HTTPException(status_code=404, detail="Slack integration not found or inactive")
        
        # List channels
        client = SlackClient(integration["bot_token"], is_encrypted=True)
        success, channels, error = client.list_channels(limit=200)
        
        if not success:
            raise HTTPException(status_code=500, detail=f"Failed to list channels: {error}")
        
        # Convert to response models
        channel_responses = []
        for channel in channels:
            channel_responses.append(SlackChannelResponse(
                id=channel.get("id", ""),
                name=channel.get("name", ""),
                is_private=channel.get("is_private", False),
                is_archived=channel.get("is_archived", False),
                num_members=channel.get("num_members", 0)
            ))
        
        return channel_responses
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list Slack channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# TEAM-LEVEL ENDPOINTS (Team members can view, admins can modify)
# ============================================================================

@router.get(
    "/teams/{team_id}/slack/config",
    response_model=TeamSlackConfigResponse,
    summary="Get Team Slack Config",
    description="Get Slack configuration for team"
)
async def get_team_slack_config(
    team_id: UUID,
    current_user: UserModel = Depends(get_current_user)
) -> TeamSlackConfigResponse:
    """Get Slack configuration for team."""
    try:
        team_id_str = str(team_id)
        
        # Verify team membership
        member_res = (
            supabase.table("team_members")
            .select("role")
            .eq("team_id", team_id_str)
            .eq("user_id", str(current_user.id))
            .single()
            .execute()
        )
        
        if not getattr(member_res, "data", None):
            raise HTTPException(status_code=403, detail="Not a member of this team")
        
        # Get config
        config_res = (
            supabase.table("team_slack_configs")
            .select("*")
            .eq("team_id", team_id_str)
            .single()
            .execute()
        )
        
        config = getattr(config_res, "data", None)
        if not config:
            raise HTTPException(status_code=404, detail="Team Slack config not found")
        
        return TeamSlackConfigResponse(**config)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get team Slack config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch(
    "/teams/{team_id}/slack/config",
    response_model=TeamSlackConfigResponse,
    summary="Update Team Slack Config",
    description="Update Slack configuration for team (team admin only)"
)
async def update_team_slack_config(
    team_id: UUID,
    update_request: UpdateTeamSlackConfigRequest,
    current_user: UserModel = Depends(get_current_user)
) -> TeamSlackConfigResponse:
    """Update Slack configuration for team (admin only)."""
    try:
        team_id_str = str(team_id)
        
        # Verify team admin access
        member_res = (
            supabase.table("team_members")
            .select("role")
            .eq("team_id", team_id_str)
            .eq("user_id", str(current_user.id))
            .single()
            .execute()
        )
        
        member = getattr(member_res, "data", None)
        if not member or member.get("role") not in ["admin", "owner"]:
            raise HTTPException(status_code=403, detail="Team admin access required")
        
        # Build update data
        update_data = {}
        if update_request.channel_id:
            update_data["channel_id"] = update_request.channel_id
        if update_request.channel_name:
            update_data["channel_name"] = update_request.channel_name
        if update_request.notifications_enabled is not None:
            update_data["notifications_enabled"] = update_request.notifications_enabled
        if update_request.mention_team_on_critical is not None:
            update_data["mention_team_on_critical"] = update_request.mention_team_on_critical
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No update fields provided")
        
        update_data["updated_at"] = datetime.utcnow().isoformat()
        
        # Check if config exists
        existing_res = (
            supabase.table("team_slack_configs")
            .select("id")
            .eq("team_id", team_id_str)
            .limit(1)
            .execute()
        )
        
        if getattr(existing_res, "data", []):
            # Update existing
            update_res = (
                supabase.table("team_slack_configs")
                .update(update_data)
                .eq("team_id", team_id_str)
                .execute()
            )
            config = getattr(update_res, "data", [None])[0]
        else:
            # Create new
            update_data["team_id"] = team_id_str
            update_data["created_at"] = datetime.utcnow().isoformat()
            insert_res = (
                supabase.table("team_slack_configs")
                .insert(update_data)
                .execute()
            )
            config = getattr(insert_res, "data", [None])[0]
        
        if not config:
            raise HTTPException(status_code=500, detail="Failed to update team Slack config")
        
        logger.info(f"Team Slack config updated for team {team_id_str}")
        
        return TeamSlackConfigResponse(**config)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update team Slack config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/teams/{team_id}/slack/notify",
    response_model=SlackNotificationResponse,
    summary="Send Slack Notification",
    description="Send notification to team's Slack channel"
)
async def send_slack_notification(
    team_id: UUID,
    notification_request: SlackNotificationRequest,
    current_user: UserModel = Depends(get_current_user)
) -> SlackNotificationResponse:
    """Send notification to team's Slack channel."""
    try:
        team_id_str = str(team_id)
        
        # Verify team membership
        member_res = (
            supabase.table("team_members")
            .select("role")
            .eq("team_id", team_id_str)
            .eq("user_id", str(current_user.id))
            .single()
            .execute()
        )
        
        if not getattr(member_res, "data", None):
            raise HTTPException(status_code=403, detail="Not a member of this team")
        
        # Get team config
        config_res = (
            supabase.table("team_slack_configs")
            .select("channel_id")
            .eq("team_id", team_id_str)
            .single()
            .execute()
        )
        
        config = getattr(config_res, "data", None)
        if not config or not config.get("channel_id"):
            raise HTTPException(status_code=404, detail="Team Slack channel not configured")
        
        # Get workspace integration
        team_res = (
            supabase.table("teams")
            .select("workspace_id")
            .eq("id", team_id_str)
            .single()
            .execute()
        )
        
        team = getattr(team_res, "data", None)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        
        integration_res = (
            supabase.table("slack_integrations")
            .select("bot_token")
            .eq("workspace_id", team["workspace_id"])
            .eq("is_active", True)
            .single()
            .execute()
        )
        
        integration = getattr(integration_res, "data", None)
        if not integration:
            raise HTTPException(status_code=404, detail="Workspace Slack integration not found or inactive")
        
        # Send message
        client = SlackClient(integration["bot_token"], is_encrypted=True)
        success, message_ts, error = client.send_message(
            channel=notification_request.channel_id or config["channel_id"],
            text=notification_request.message,
            blocks=notification_request.blocks
        )
        
        if not success:
            raise HTTPException(status_code=500, detail=f"Failed to send message: {error}")
        
        return SlackNotificationResponse(
            success=True,
            message_ts=message_ts,
            channel_id=notification_request.channel_id or config["channel_id"],
            error=None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send Slack notification: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# OAUTH FLOW ENDPOINTS
# ============================================================================

@router.get(
    "/workspaces/{workspace_id}/slack/oauth/init",
    response_model=SlackOAuthInitResponse,
    summary="Initiate Slack OAuth Flow",
    description="Generate Slack OAuth authorization URL (admin only)"
)
async def init_slack_oauth(
    workspace_id: UUID,
    redirect_after_auth: Optional[str] = Query(None, description="URL to redirect after OAuth"),
    current_user: UserModel = Depends(get_current_user)
) -> SlackOAuthInitResponse:
    """
    Initiate Slack OAuth flow.
    
    Returns authorization URL that user should be redirected to.
    After user authorizes on Slack, they'll be redirected back to the callback endpoint.
    """
    try:
        workspace_id_str = str(workspace_id)
        
        # Verify admin access
        verify_workspace_admin(workspace_id_str, current_user)
        
        # Generate OAuth URL
        oauth_service = get_slack_oauth_service()
        auth_url, state, expires_at = oauth_service.generate_authorization_url(
            workspace_id=workspace_id,
            user_id=current_user.id,
            redirect_after_auth=redirect_after_auth
        )
        
        logger.info(f"OAuth flow initiated for workspace {workspace_id}")
        
        return SlackOAuthInitResponse(
            authorization_url=auth_url,
            state=state,
            expires_at=expires_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to initiate OAuth flow: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/slack/oauth/callback",
    summary="Slack OAuth Callback",
    description="Handle OAuth callback from Slack (redirects to frontend)"
)
async def slack_oauth_callback(
    code: str = Query(..., description="OAuth authorization code"),
    state: str = Query(..., description="OAuth state parameter"),
    error: Optional[str] = Query(None, description="Error from Slack"),
    current_user: UserModel = Depends(get_current_user)
):
    """
    Handle OAuth callback from Slack.
    
    This endpoint:
    1. Validates the state parameter (CSRF protection)
    2. Exchanges authorization code for access tokens
    3. Creates/updates Slack integration in database
    4. Redirects to frontend with success/error message
    """
    try:
        # Check for errors from Slack
        if error:
            logger.error(f"Slack OAuth error: {error}")
            # Redirect to frontend with error
            redirect_url = f"{get_slack_oauth_service().settings.FRONTEND_URL}/settings/integrations?slack_error={error}"
            return RedirectResponse(url=redirect_url)
        
        # Validate state
        oauth_service = get_slack_oauth_service()
        is_valid, workspace_id, redirect_uri = oauth_service.validate_state(state, current_user.id)
        
        if not is_valid or not workspace_id:
            logger.error("Invalid OAuth state")
            redirect_url = f"{oauth_service.settings.FRONTEND_URL}/settings/integrations?slack_error=invalid_state"
            return RedirectResponse(url=redirect_url)
        
        # Exchange code for tokens
        success, integration_data, error_msg = oauth_service.exchange_code_for_token(
            code=code,
            workspace_id=workspace_id
        )
        
        if not success or not integration_data:
            logger.error(f"Token exchange failed: {error_msg}")
            redirect_url = f"{oauth_service.settings.FRONTEND_URL}/settings/integrations?slack_error=token_exchange_failed"
            return RedirectResponse(url=redirect_url)
        
        # Check if integration already exists
        existing_res = (
            supabase.table("slack_integrations")
            .select("id")
            .eq("workspace_id", str(workspace_id))
            .limit(1)
            .execute()
        )
        
        if getattr(existing_res, "data", []):
            # Update existing integration
            update_res = (
                supabase.table("slack_integrations")
                .update(integration_data)
                .eq("workspace_id", str(workspace_id))
                .execute()
            )
            integration = getattr(update_res, "data", [None])[0]
            logger.info(f"Updated Slack integration for workspace {workspace_id}")
        else:
            # Create new integration
            integration_data["installed_by"] = str(current_user.id)
            insert_res = (
                supabase.table("slack_integrations")
                .insert(integration_data)
                .execute()
            )
            integration = getattr(insert_res, "data", [None])[0]
            logger.info(f"Created Slack integration for workspace {workspace_id}")
        
        if not integration:
            raise Exception("Failed to save Slack integration")
        
        # Redirect to frontend with success
        final_redirect = redirect_uri or f"{oauth_service.settings.FRONTEND_URL}/settings/integrations?slack_success=true"
        return RedirectResponse(url=final_redirect)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OAuth callback failed: {e}")
        redirect_url = f"{get_slack_oauth_service().settings.FRONTEND_URL}/settings/integrations?slack_error=unknown"
        return RedirectResponse(url=redirect_url)
