# api/routes/integrations.py
# Simplified API endpoints for Jira integration

import logging
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models.integration_models import (
    ConnectionStatus, IntegrationType, JiraConnectionRequest, JiraConnectionResponse,
    JiraSyncRequest, JiraSyncResponse,
    IntegrationStatusResponse, AvailableProject
)
from app.services.jira.jira_sync_service import sync_service, JiraSyncService
from app.services.jira.jira_webhook_handler import webhook_handler
from app.core.dependencies import get_current_user, UserModel, supabase, limiter
from pydantic import BaseModel

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


class TransitionRequest(BaseModel):
    issue_key: str
    transition: str


class AssignRequest(BaseModel):
    issue_key: str
    assignee: str


class CommentRequest(BaseModel):
    issue_key: str
    comment: str


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
        # Look up stored credentials
        cred_result = supabase.table("integration_credentials").select("*") \
            .eq("workspace_id", workspace_id).eq("integration_type", "jira").limit(1).execute()

        if not cred_result.data:
            return IntegrationStatusResponse(
                is_connected=False,
                connection_status=ConnectionStatus.DISCONNECTED,
                integration_type=IntegrationType.JIRA,
                last_tested_at=None,
                last_sync_at=None,
                jira_url="",
                jira_email="",
                available_projects=[]
            )

        credentials = cred_result.data[0]
        integration_id = credentials.get('id')

        from app.services.encryption.simple_credential_store import simple_credential_store
        from app.services.jira.jira_client import JiraClient

        decoded_token = simple_credential_store.decode_credential(credentials.get('jira_api_token_encrypted', ''))
        jira_client = JiraClient(
            credentials.get('jira_url', ''),
            credentials.get('jira_email', ''),
            decoded_token
        )

        success, message = jira_client.connect()
        # If connected, make sure the sync service has a client registered
        try:
            if success and integration_id:
                from app.services.jira.jira_sync_service import sync_service
                sync_service.clients[str(integration_id)] = jira_client
        except Exception as e:
            logger.warning(f"Could not register Jira client for sync: {str(e)}")
        available_projects = []
        if success:
            projects = jira_client.get_all_projects()
            available_projects = [{'key': p['key'], 'name': p['name']} for p in projects[:10]]

        return IntegrationStatusResponse(
            integration_id=integration_id,
            is_connected=success,
            connection_status=ConnectionStatus.CONNECTED if success else ConnectionStatus.FAILED,
            integration_type=IntegrationType.JIRA,
            last_tested_at=credentials.get('last_tested_at'),
            last_sync_at=credentials.get('last_sync_time'),
            jira_url=credentials.get('jira_url', ''),
            jira_email=credentials.get('jira_email', ''),
            available_projects=[AvailableProject(key=p['key'], name=p['name']) for p in available_projects]
        )

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
        
        # Use the sync_integration method instead of sync_project
        result = await sync_service.sync_integration(
            integration_id=str(project_id),
            force=True  # Force sync for manual trigger
        )
        
        # Convert the result format to match expected response
        sync_response = {
            'success': result[0],
            'message': result[1],
            'sync_status': 'completed' if result[0] else 'failed',
            'items_synced': result[2].get('issues_synced', 0) if len(result) > 2 else 0,
            'sync_duration': 0,  # Placeholder
            'errors': result[2].get('errors', []) if len(result) > 2 else []
        }
        
        return JiraSyncResponse(**sync_response)
        
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
        # Get credentials (simplified approach)
        # For now, we'll use a placeholder since _get_credentials doesn't exist
        credentials = None
        
        # Try to check if there are stored credentials in Supabase
        try:
            cred_result = supabase.table("integration_credentials").select("*").eq("workspace_id", workspace_id).eq("integration_type", "jira").execute()
            if cred_result.data:
                credentials = cred_result.data[0]
        except Exception as e:
            logger.warning(f"Could not check credentials: {str(e)}")
            credentials = None
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


# Enhanced Jira Integration Endpoints

@router.post("/jira/webhook")
async def jira_webhook(request: Request):
    """
    Handle incoming Jira webhooks for real-time synchronization.
    """
    try:
        # Get raw payload for signature verification
        raw_payload = await request.body()
        webhook_data = await request.json()
        
        # Optional: Verify webhook signature if configured
        # signature = request.headers.get("X-Hub-Signature-256", "")
        # if not webhook_handler.validate_webhook_signature(raw_payload.decode(), signature, webhook_secret):
        #     return JSONResponse(status_code=401, content={"error": "Invalid signature"})
        
        # Process the webhook
        result = webhook_handler.process_webhook(webhook_data)
        
        logger.info(f"Webhook processed: {result}")
        
        return JSONResponse(
            status_code=200,
            content=result
        )
        
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Webhook processing failed: {str(e)}"}
        )


@router.post("/jira/{integration_id}/issues")
async def create_jira_issue(
    integration_id: str,
    issue_data: dict,
    request: Request
):
    """
    Create a new issue in Jira with bi-directional sync.
    """
    try:
        # Extract project key and issue details
        project_key = issue_data.get("project_key", "")
        if not project_key:
            return JSONResponse(
                status_code=400,
                content={"error": "project_key is required"}
            )
        
        # Create the issue using enhanced sync service
        success, message, issue_key = await sync_service.create_issue(
            integration_id=integration_id,
            project_key=project_key,
            issue_data=issue_data
        )
        
        if success:
            return JSONResponse(
                status_code=201,
                content={
                    "message": message,
                    "issue_key": issue_key,
                    "success": True
                }
            )
        else:
            return JSONResponse(
                status_code=400,
                content={"error": message, "success": False}
            )
            
    except Exception as e:
        logger.error(f"Error creating Jira issue: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to create issue: {str(e)}"}
        )


@router.put("/jira/{integration_id}/issues/{issue_key}")
async def update_jira_issue(
    integration_id: str,
    issue_key: str,
    updates: dict,
    request: Request
):
    """
    Update an existing issue in Jira with bi-directional sync.
    """
    try:
        success, message = await sync_service.update_issue(
            integration_id=integration_id,
            issue_key=issue_key,
            updates=updates
        )
        
        if success:
            return JSONResponse(
                status_code=200,
                content={
                    "message": message,
                    "issue_key": issue_key,
                    "success": True
                }
            )
        else:
            return JSONResponse(
                status_code=400,
                content={"error": message, "success": False}
            )
            
    except Exception as e:
        logger.error(f"Error updating Jira issue: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to update issue: {str(e)}"}
        )

@router.get("/jira/{integration_id}/issues/{issue_key}")
async def get_issue_detail(
    integration_id: str,
    issue_key: str
):
    """Get a single Jira issue."""
    try:
        client = sync_service.clients.get(integration_id)
        if not client:
            return JSONResponse(status_code=404, content={"error": "integration not found"})
        issue = client.get_issue(issue_key)
        if not issue:
            return JSONResponse(status_code=404, content={"error": "issue not found"})
        return JSONResponse(status_code=200, content=jsonable_encoder(issue))
    except Exception as e:
        logger.error(f"Error getting issue {issue_key}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": f"Failed to get issue: {str(e)}"})

@router.get("/jira/{integration_id}/issues/{issue_key}/editmeta")
async def get_issue_editmeta(
    integration_id: str,
    issue_key: str
):
    """Get edit metadata for a Jira issue (which fields are editable and allowed values)."""
    try:
        client = sync_service.clients.get(integration_id)
        if not client:
            return JSONResponse(status_code=404, content={"error": "integration not found"})
        meta = client.get_issue_editmeta(issue_key)
        return JSONResponse(status_code=200, content=jsonable_encoder(meta))
    except Exception as e:
        logger.error(f"Error getting editmeta for {issue_key}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": f"Failed to get editmeta: {str(e)}"})

@router.post("/jira/{integration_id}/issues/transition")
async def transition_issue(
    integration_id: str,
    body: TransitionRequest
):
    """Transition a Jira issue to a new status."""
    try:
        client = sync_service.clients.get(integration_id)
        if not client:
            return JSONResponse(status_code=404, content={"error": "integration not found"})
        ok, msg = client.transition_issue(body.issue_key, body.transition)
        updated = client.get_issue(body.issue_key) if ok else None
        return JSONResponse(status_code=200 if ok else 400, content=jsonable_encoder({"success": ok, "message": msg, "issue": updated}))
    except Exception as e:
        logger.error(f"Error transitioning issue {body.issue_key}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": f"Failed to transition issue: {str(e)}"})

@router.post("/jira/{integration_id}/issues/assign")
async def assign_issue(
    integration_id: str,
    body: AssignRequest
):
    """Assign a Jira issue to a user."""
    try:
        client = sync_service.clients.get(integration_id)
        if not client:
            return JSONResponse(status_code=404, content={"error": "integration not found"})
        ok, msg = client.update_issue(body.issue_key, {"assignee": body.assignee})
        updated = client.get_issue(body.issue_key) if ok else None
        return JSONResponse(status_code=200 if ok else 400, content=jsonable_encoder({"success": ok, "message": msg, "issue": updated}))
    except Exception as e:
        logger.error(f"Error assigning issue {body.issue_key}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": f"Failed to assign issue: {str(e)}"})

@router.post("/jira/{integration_id}/issues/comment")
async def comment_issue(
    integration_id: str,
    body: CommentRequest
):
    """Add a comment to a Jira issue."""
    try:
        client = sync_service.clients.get(integration_id)
        if not client:
            return JSONResponse(status_code=404, content={"error": "integration not found"})
        ok, msg = client.add_comment(body.issue_key, body.comment)
        updated = client.get_issue(body.issue_key) if ok else None
        return JSONResponse(status_code=200 if ok else 400, content=jsonable_encoder({"success": ok, "message": msg, "issue": updated}))
    except Exception as e:
        logger.error(f"Error commenting on issue {body.issue_key}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": f"Failed to add comment: {str(e)}"})

@router.get("/jira/{integration_id}/issues/{issue_key}/transitions")
async def list_issue_transitions(
    integration_id: str,
    issue_key: str
):
    """List available transitions for an issue."""
    try:
        client = sync_service.clients.get(integration_id)
        if not client:
            return JSONResponse(status_code=404, content={"error": "integration not found"})
        transitions = client.get_transitions(issue_key)
        return JSONResponse(status_code=200, content=jsonable_encoder({"transitions": transitions}))
    except Exception as e:
        logger.error(f"Error listing transitions for {issue_key}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": f"Failed to list transitions: {str(e)}"})

@router.get("/jira/{integration_id}/projects/{project_key}/users")
async def list_project_users(
    integration_id: str,
    project_key: str
):
    """List assignable users for a project."""
    try:
        client = sync_service.clients.get(integration_id)
        if not client:
            return JSONResponse(status_code=404, content={"error": "integration not found"})
        users = client.get_project_users(project_key)
        return JSONResponse(status_code=200, content=jsonable_encoder({"users": users}))
    except Exception as e:
        logger.error(f"Error listing users for project {project_key}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": f"Failed to list users: {str(e)}"})

@router.get("/jira/{integration_id}/priorities")
async def list_priorities(
    integration_id: str
):
    """List global priorities available in Jira."""
    try:
        client = sync_service.clients.get(integration_id)
        if not client:
            return JSONResponse(status_code=404, content={"error": "integration not found"})
        items = client.get_priorities()
        return JSONResponse(status_code=200, content=jsonable_encoder({"priorities": items}))
    except Exception as e:
        logger.error(f"Error listing priorities: {str(e)}")
        return JSONResponse(status_code=500, content={"error": f"Failed to list priorities: {str(e)}"})

@router.post("/jira/{integration_id}/issues/bulk")
async def bulk_create_jira_issues(
    integration_id: str,
    issues_data: dict,
    request: Request
):
    """
    Create multiple issues in bulk with optimized sync.
    """
    try:
        issues_list = issues_data.get("issues", [])
        if not issues_list:
            return JSONResponse(
                status_code=400,
                content={"error": "issues array is required"}
            )
        
        success, message, created_keys = await sync_service.bulk_create_issues(
            integration_id=integration_id,
            issues_data=issues_list
        )
        
        return JSONResponse(
            status_code=200 if success else 400,
            content={
                "message": message,
                "created_issues": created_keys,
                "total_created": len(created_keys),
                "success": success
            }
        )
            
    except Exception as e:
        logger.error(f"Error bulk creating Jira issues: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to bulk create issues: {str(e)}"}
        )


@router.get("/jira/{integration_id}/search")
async def search_jira_issues(
    integration_id: str,
    jql: Optional[str] = Query(None),
    max_results: int = 50,
    start_at: int = 0
):
    """
    Search issues using JQL with local caching.
    """
    try:
        if not jql:
            return JSONResponse(
                status_code=400,
                content={"error": "jql parameter is required"}
            )
        
        result = await sync_service.search_issues(
            integration_id=integration_id,
            jql=jql,
            max_results=max_results,
            start_at=start_at
        )
        
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder({
                "issues": result.get("issues", []),
                "total": result.get("total", 0),
                "jql": jql,
                "max_results": max_results,
                "start_at": start_at
            })
        )
            
    except Exception as e:
        logger.error(f"Error searching Jira issues: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to search issues: {str(e)}"}
        )


@router.get("/jira/{integration_id}/sync/status")
async def get_jira_sync_status(
    integration_id: str
):
    """
    Get current sync status for a Jira integration.
    """
    try:
        status_data = sync_service.get_sync_status(integration_id)

        # If not found (e.g., after reload), try to bootstrap the client from stored credentials
        if status_data.get('status') == 'not_found':
            try:
                cred_result = supabase.table("integration_credentials").select("*") \
                    .eq("id", integration_id).limit(1).execute()
                if cred_result.data:
                    credentials = cred_result.data[0]
                    from app.services.encryption.simple_credential_store import simple_credential_store
                    from app.services.jira.jira_client import JiraClient
                    decoded_token = simple_credential_store.decode_credential(credentials.get('jira_api_token_encrypted', ''))
                    jira_client = JiraClient(
                        credentials.get('jira_url', ''),
                        credentials.get('jira_email', ''),
                        decoded_token
                    )
                    success, _ = jira_client.connect()
                    if success:
                        sync_service.clients[str(integration_id)] = jira_client
                        # Recompute status after registering client
                        status_data = sync_service.get_sync_status(integration_id)
            except Exception as e:
                logger.warning(f"Could not bootstrap Jira client for status: {str(e)}")

        # Ensure any non-serializable values (e.g., datetime) are encoded properly
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(status_data)
        )
            
    except Exception as e:
        logger.error(f"Error getting sync status: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to get sync status: {str(e)}"}
        )


@router.post("/jira/{integration_id}/sync")
async def trigger_jira_sync(
    integration_id: str,
    force: bool = False
):
    """
    Trigger a manual sync for a Jira integration.
    """
    try:
        success, message, sync_stats = await sync_service.sync_integration(
            integration_id=integration_id,
            force=force
        )
        
        return JSONResponse(
            status_code=200 if success else 400,
            content=jsonable_encoder({
                "success": success,
                "message": message,
                "sync_stats": sync_stats
            })
        )
            
    except Exception as e:
        logger.error(f"Error triggering sync: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to trigger sync: {str(e)}"}
        )


@router.get("/jira/sync/status/all")
async def get_all_jira_sync_statuses():
    """
    Get sync status for all Jira integrations.
    """
    try:
        statuses = sync_service.get_all_sync_statuses()
        
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder({
                "integrations": statuses,
                "total_integrations": len(statuses)
            })
        )
            
    except Exception as e:
        logger.error(f"Error getting all sync statuses: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to get sync statuses: {str(e)}"}
        )
