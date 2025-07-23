# api/routes/integrations.py
# Simplified API endpoints for Jira integration

import logging
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models.integration_models import (
    JiraConnectionRequest, JiraConnectionResponse,
    JiraSyncRequest, JiraSyncResponse,
    IntegrationStatusResponse
)
from app.services.jira.jira_sync_service import JiraSyncService
from app.core.dependencies import get_current_user, UserModel, supabase, limiter

logger = logging.getLogger("cognisim_ai")

# Create router for integration endpoints
router = APIRouter(prefix="/api/integrations", tags=["integrations"])


def get_workspace_id_from_user(current_user: UserModel = Depends(get_current_user)) -> str:
    """
    Get workspace ID for current user.
    """
    try:
        user_id = str(current_user.id)
        
        # Try to find existing workspace for user via team_members -> teams -> workspace_id
        user_teams = supabase.table("team_members").select("team_id").eq("user_id", user_id).limit(1).execute()
        
        if user_teams.data:
            team_id = str(user_teams.data[0]['team_id'])
            # Get the workspace_id from the team
            team_info = supabase.table("teams").select("workspace_id").eq("id", team_id).execute()
            if team_info.data:
                workspace_id = str(team_info.data[0]['workspace_id'])
                logger.info(f"Found workspace {workspace_id} for user {user_id} via team {team_id}")
                return workspace_id
        
        # Use the correct CogniSim Corp workspace ID
        existing_workspace_id = "84e53826-b670-41fa-96d3-211ebdbc080c"
        logger.info(f"Using CogniSim Corp workspace: {existing_workspace_id}")
        return existing_workspace_id
        
    except Exception as e:
        logger.error(f"Failed to get workspace for user {current_user.id}: {str(e)}")
        # Fallback to the correct CogniSim Corp workspace
        fallback_workspace_id = "84e53826-b670-41fa-96d3-211ebdbc080c"
        logger.info(f"Using fallback workspace: {fallback_workspace_id}")
        return fallback_workspace_id


def get_jira_sync_service() -> JiraSyncService:
    """Dependency to get JiraSyncService instance."""
    return JiraSyncService(supabase)


@router.post(
    "/jira/connect",
    response_model=JiraConnectionResponse,
    summary="Connect Jira Integration",
    description="Save and test Jira credentials for the current workspace"
)
@limiter.limit("5/minute")
async def connect_jira(
    request: Request,
    connection_request: JiraConnectionRequest,
    workspace_id: str = Depends(get_workspace_id_from_user),
    sync_service: JiraSyncService = Depends(get_jira_sync_service)
) -> JiraConnectionResponse:
    """
    Connect and test Jira credentials.
    
    This endpoint:
    1. Tests the connection to the provided Jira instance
    2. Encodes and stores the credentials securely
    3. Returns the connection status
    """
    try:
        logger.info(f"Jira connection attempt for workspace {workspace_id}")
        
        result = await sync_service.save_and_test_credentials(
            workspace_id=workspace_id,
            jira_url=connection_request.jira_url,
            jira_email=connection_request.jira_email,
            jira_api_token=connection_request.jira_api_token
        )
        
        return JiraConnectionResponse(**result)
        
    except Exception as e:
        logger.error(f"Jira connection failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Connection failed: {str(e)}"
        )


@router.get(
    "/jira/status",
    response_model=IntegrationStatusResponse,
    summary="Get Jira Integration Status",
    description="Get the current status of Jira integration for the workspace"
)
@limiter.limit("10/minute")
async def get_jira_status(
    request: Request,
    workspace_id: str = Depends(get_workspace_id_from_user),
    sync_service: JiraSyncService = Depends(get_jira_sync_service)
) -> IntegrationStatusResponse:
    """
    Get current Jira integration status.
    """
    try:
        status_data = await sync_service.get_integration_status(workspace_id)
        return IntegrationStatusResponse(**status_data)
        
    except Exception as e:
        logger.error(f"Failed to get Jira status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get status: {str(e)}"
        )


@router.post(
    "/jira/sync/{project_id}",
    response_model=JiraSyncResponse,
    summary="Sync Jira Project",
    description="Trigger manual sync of a Jira project with CogniSim"
)
@limiter.limit("3/minute")  # Lower limit for sync operations
async def sync_jira_project(
    request: Request,
    project_id: UUID,
    sync_request: JiraSyncRequest,
    workspace_id: str = Depends(get_workspace_id_from_user),
    sync_service: JiraSyncService = Depends(get_jira_sync_service)
) -> JiraSyncResponse:
    """
    Trigger manual sync of a specific project.
    
    This endpoint:
    1. Validates Jira connection
    2. Fetches issues from the specified Jira project
    3. Maps and syncs them to CogniSim items
    4. Returns sync results
    """
    try:
        logger.info(f"Manual sync triggered for project {project_id} with Jira project {sync_request.jira_project_key}")
        
        result = await sync_service.sync_project(
            workspace_id=workspace_id,
            project_id=str(project_id),
            jira_project_key=sync_request.jira_project_key,
            max_results=sync_request.max_results or 50
        )
        
        return JiraSyncResponse(**result)
        
    except Exception as e:
        logger.error(f"Sync failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync failed: {str(e)}"
        )


@router.get(
    "/jira/test",
    summary="Test Jira Connection",
    description="Test the current Jira connection without syncing"
)
@limiter.limit("10/minute")
async def test_jira_connection(
    request: Request,
    workspace_id: str = Depends(get_workspace_id_from_user),
    sync_service: JiraSyncService = Depends(get_jira_sync_service)
):
    """
    Test the current Jira connection and return basic information.
    """
    try:
        # Get credentials
        credentials = await sync_service._get_credentials(workspace_id)
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No Jira credentials found for this workspace"
            )
        
        # Test connection
        from app.services.encryption.simple_credential_store import simple_credential_store
        from app.services.jira.jira_client import JiraClient
        
        decoded_token = simple_credential_store.decode_credential(credentials['jira_api_token_encrypted'])
        jira_client = JiraClient(
            credentials['jira_url'],
            credentials['jira_email'],
            decoded_token
        )
        
        success, message = jira_client.test_connection()
        
        if success:
            # Get available projects
            projects = jira_client.get_all_projects()
            jira_client.close()
            
            return {
                'success': True,
                'message': message,
                'jira_url': credentials['jira_url'],
                'jira_email': credentials['jira_email'],
                'available_projects': [{'key': p['key'], 'name': p['name']} for p in projects[:10]]  # Limit to 10
            }
        else:
            jira_client.close()
            return {
                'success': False,
                'message': message,
                'jira_url': credentials['jira_url'],
                'jira_email': credentials['jira_email'],
                'available_projects': []
            }
        
    except Exception as e:
        logger.error(f"Connection test failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Connection test failed: {str(e)}"
        )
