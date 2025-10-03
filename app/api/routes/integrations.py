# api/routes/integrations.py
# Simplified API endpoints for Jira integration

import logging
import secrets
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.encoders import jsonable_encoder
from slowapi import Limiter
from slowapi.util import get_remote_address
from datetime import datetime, timedelta
import urllib.parse

from app.models.integration_models import (
    ConnectionStatus, IntegrationType, JiraConnectionRequest, JiraConnectionResponse,
    JiraSyncRequest, JiraSyncResponse,
    IntegrationStatusResponse, AvailableProject, JiraOAuthInitResponse, JiraDisconnectResponse
)
from app.services.jira.jira_sync_service import sync_service, JiraSyncService
from app.services.jira.jira_webhook_handler import webhook_handler
from app.core.dependencies import get_current_user, UserModel, supabase, limiter
from app.core.config import Settings
from pydantic import BaseModel

logger = logging.getLogger("cognisim_ai")

# Create router for integration endpoints
router = APIRouter(prefix="/api/integrations", tags=["integrations"])

# Load settings
settings = Settings()


def get_workspace_id_from_user(request: Request, current_user: UserModel = Depends(get_current_user)) -> str:
    """Resolve active workspace for current user.

    Order of resolution:
    1. X-Workspace-Id header (validate membership)
    2. First workspace the user is a member of (membership table / RPC)
    3. 404 if none
    """
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
            return bool(getattr(res, "data", []) )
        except Exception as e:
            logger.warning(f"Membership validation error for user {user_id} workspace {wid}: {e}")
            return False

    # 1. Header provided
    if header_wid and _validate(header_wid):
        return header_wid
    if header_wid and not _validate(header_wid):
        logger.warning(f"User {user_id} attempted access to non-member workspace {header_wid}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of workspace")

    # 2. Fallback to first membership (RPC preferred)
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


def get_jira_sync_service() -> JiraSyncService:
    """Dependency to get JiraSyncService instance."""
    # Return the already-imported global sync_service so all endpoints share the same
    # in-memory Jira clients registry. Creating a new JiraSyncService per request
    # caused empty clients dicts and 404 "Integration not found" responses for
    # endpoints (e.g., create sprint) that relied on the dependency.
    return sync_service


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
    "/jira/oauth/init",
    response_model=JiraOAuthInitResponse,
    summary="Initialize Jira OAuth Flow",
    description="Start the OAuth flow to connect Jira"
)
@limiter.limit("10/minute")
async def init_jira_oauth(
    request: Request,
    workspace_id: str = Depends(get_workspace_id_from_user),
    current_user: UserModel = Depends(get_current_user)
) -> JiraOAuthInitResponse:
    """
    Initialize OAuth flow for Jira integration.
    
    Returns the authorization URL where user should be redirected.
    """
    try:
        # Generate a unique state for CSRF protection
        state = secrets.token_urlsafe(32)
        
        # Store the state with workspace_id and user_id for validation
        oauth_state_record = {
            'state': state,
            'workspace_id': workspace_id,
            'user_id': str(current_user.id),
            'created_at': datetime.utcnow().isoformat(),
            'expires_at': (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        }
        
        # Store in Supabase temporary table
        supabase.table('oauth_states').insert(oauth_state_record).execute()
        
        # Build the authorization URL
        # Note: Jira uses OAuth 2.0 (3LO - 3-legged OAuth)
        # Users need to create an OAuth 2.0 app in their Atlassian Developer Console
        base_auth_url = "https://auth.atlassian.com/authorize"
        
        params = {
            'audience': 'api.atlassian.com',
            'client_id': settings.JIRA_OAUTH_CLIENT_ID or '',
            'scope': 'read:jira-user read:jira-work write:jira-work offline_access',
            'redirect_uri': settings.JIRA_OAUTH_REDIRECT_URI or '',
            'state': state,
            'response_type': 'code',
            'prompt': 'consent'
        }
        
        authorization_url = f"{base_auth_url}?{urllib.parse.urlencode(params)}"
        
        logger.info(f"OAuth flow initiated for workspace {workspace_id}")
        
        return JiraOAuthInitResponse(
            authorization_url=authorization_url,
            state=state
        )
        
    except Exception as e:
        logger.error(f"Failed to initialize OAuth: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize OAuth: {str(e)}"
        )


@router.get(
    "/jira/oauth/callback",
    summary="Handle Jira OAuth Callback",
    description="Process the OAuth callback from Jira"
)
async def jira_oauth_callback(
    code: str = Query(..., description="Authorization code from Jira"),
    state: str = Query(..., description="State parameter for CSRF protection"),
    sync_service: JiraSyncService = Depends(get_jira_sync_service)
):
    """
    Handle the OAuth callback from Jira.
    
    This endpoint:
    1. Validates the state parameter
    2. Exchanges the authorization code for access tokens
    3. Stores the credentials securely
    4. Redirects back to the frontend
    """
    try:
        import httpx
        from app.services.encryption.simple_credential_store import simple_credential_store
        
        # Validate state
        state_result = supabase.table('oauth_states').select('*').eq('state', state).execute()
        
        if not state_result.data:
            logger.error("Invalid OAuth state")
            return RedirectResponse(
                url=f"{settings.FRONTEND_URL}/integrations?error=invalid_state"
            )
        
        oauth_state = state_result.data[0]
        workspace_id = oauth_state['workspace_id']
        
        # Check if state expired
        expires_at = datetime.fromisoformat(oauth_state['expires_at'].replace('Z', '+00:00'))
        if datetime.utcnow() > expires_at:
            logger.error("OAuth state expired")
            return RedirectResponse(
                url=f"{settings.FRONTEND_URL}/integrations?error=state_expired"
            )
        
        # Exchange code for tokens
        token_url = "https://auth.atlassian.com/oauth/token"
        token_data = {
            'grant_type': 'authorization_code',
            'client_id': settings.JIRA_OAUTH_CLIENT_ID,
            'client_secret': settings.JIRA_OAUTH_CLIENT_SECRET.get_secret_value() if settings.JIRA_OAUTH_CLIENT_SECRET else '',
            'code': code,
            'redirect_uri': settings.JIRA_OAUTH_REDIRECT_URI
        }
        
        async with httpx.AsyncClient() as client:
            token_response = await client.post(token_url, data=token_data)
            
            if token_response.status_code != 200:
                logger.error(f"Token exchange failed: {token_response.text}")
                return RedirectResponse(
                    url=f"{settings.FRONTEND_URL}/integrations?error=token_exchange_failed"
                )
            
            token_data = token_response.json()
            access_token = token_data.get('access_token')
            refresh_token = token_data.get('refresh_token')
            
            # Get accessible resources (Jira sites)
            resources_url = "https://api.atlassian.com/oauth/token/accessible-resources"
            headers = {'Authorization': f'Bearer {access_token}'}
            resources_response = await client.get(resources_url, headers=headers)
            
            if resources_response.status_code != 200:
                logger.error("Failed to get accessible resources")
                return RedirectResponse(
                    url=f"{settings.FRONTEND_URL}/integrations?error=resources_failed"
                )
            
            resources = resources_response.json()
            if not resources:
                logger.error("No accessible Jira sites found")
                return RedirectResponse(
                    url=f"{settings.FRONTEND_URL}/integrations?error=no_sites"
                )
            
            # Use the first site
            site = resources[0]
            jira_url = site.get('url', '')
            cloud_id = site.get('id', '')
            
            # Get user info
            me_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/myself"
            me_response = await client.get(me_url, headers=headers)
            
            user_email = ""
            if me_response.status_code == 200:
                user_data = me_response.json()
                user_email = user_data.get('emailAddress', '')
        
        # Store credentials
        encrypted_access_token = simple_credential_store.encode_credential(access_token)
        encrypted_refresh_token = simple_credential_store.encode_credential(refresh_token) if refresh_token else None
        
        credential_record = {
            'workspace_id': workspace_id,
            'integration_type': 'jira',
            'jira_url': jira_url,
            'jira_email': user_email,
            'jira_api_token_encrypted': encrypted_access_token,
            'jira_refresh_token_encrypted': encrypted_refresh_token,
            'jira_cloud_id': cloud_id,
            'connection_status': 'connected',
            'last_tested_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        
        # Check if integration already exists
        existing = supabase.table('integration_credentials').select('id').eq('workspace_id', workspace_id).eq('integration_type', 'jira').execute()
        
        if existing.data:
            # Update existing
            supabase.table('integration_credentials').update(credential_record).eq('id', existing.data[0]['id']).execute()
        else:
            # Insert new
            credential_record['created_at'] = datetime.utcnow().isoformat()
            supabase.table('integration_credentials').insert(credential_record).execute()
        
        # Delete used state
        supabase.table('oauth_states').delete().eq('state', state).execute()
        
        logger.info(f"OAuth flow completed for workspace {workspace_id}")
        
        # Redirect to frontend with success
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/integrations?connected=true"
        )
        
    except Exception as e:
        logger.error(f"OAuth callback failed: {str(e)}", exc_info=True)
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/integrations?error=callback_failed"
        )


@router.post(
    "/jira/disconnect",
    response_model=JiraDisconnectResponse,
    summary="Disconnect Jira Integration",
    description="Revoke and delete Jira connection"
)
@limiter.limit("5/minute")
async def disconnect_jira(
    request: Request,
    workspace_id: str = Depends(get_workspace_id_from_user),
    sync_service: JiraSyncService = Depends(get_jira_sync_service)
) -> JiraDisconnectResponse:
    """
    Disconnect Jira integration and remove stored credentials.
    """
    try:
        # Find and delete credentials
        result = supabase.table('integration_credentials').delete().eq('workspace_id', workspace_id).eq('integration_type', 'jira').execute()
        
        if result.data:
            logger.info(f"Jira integration disconnected for workspace {workspace_id}")
            return JiraDisconnectResponse(
                success=True,
                message="Jira integration disconnected successfully"
            )
        else:
            logger.warning(f"No Jira integration found for workspace {workspace_id}")
            return JiraDisconnectResponse(
                success=False,
                message="No Jira integration found"
            )
        
    except Exception as e:
        logger.error(f"Failed to disconnect Jira: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disconnect: {str(e)}"
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


# Board Management Endpoints

@router.get("/jira/{integration_id}/boards")
async def list_boards(
    integration_id: str,
    current_user: UserModel = Depends(get_current_user)
):
    """
    List all available boards for debugging and board resolution.
    """
    try:
        logger.info(f"Listing boards for integration: {integration_id}")
        
        # Get Jira client for this integration
        try:
            jira_client = sync_service.clients.get(integration_id)
            if not jira_client:
                return JSONResponse(
                    status_code=404,
                    content={"error": "Integration not found or not connected"}
                )
            
            # Get available boards
            boards = jira_client.list_available_boards()
            
            logger.info(f"Found {len(boards)} boards")
            return JSONResponse(
                status_code=200,
                content={
                    "boards": boards,
                    "count": len(boards)
                }
            )
            
        except Exception as e:
            logger.error(f"Error accessing Jira service: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"error": f"Failed to access Jira service: {str(e)}"}
            )
            
    except Exception as e:
        logger.error(f"Error listing boards: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to list boards: {str(e)}"}
        )


# Sprint Management Endpoints

@router.get("/jira/{integration_id}/boards/{board_id}/sprints")
async def get_board_sprints(
    integration_id: str,
    board_id: str,
    state: str = Query("active", description="Sprint state: active, future, closed, or all")
):
    """
    Get sprints for a board.
    """
    try:
        client = sync_service.clients.get(integration_id)
        if not client:
            return JSONResponse(
                status_code=404,
                content={"error": "Integration not found"}
            )
        
        if not client:
            return JSONResponse(
                status_code=400,
                content={"error": "Jira client not available"}
            )
        
        # board_id is a path parameter and should always be a string
        bid = board_id
        if state == "active":
            sprints = client.get_active_sprints(bid)
        else:
            # For future enhancement - could implement other states
            sprints = client.get_active_sprints(bid)
        
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder({
                "sprints": sprints,
                "board_id": board_id,
                "state": state
            })
        )
            
    except Exception as e:
        logger.error(f"Error getting board sprints: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to get sprints: {str(e)}"}
        )


@router.post("/jira/{integration_id}/sprints/{sprint_id}/issues")
async def add_issues_to_sprint(
    integration_id: str,
    sprint_id: str,
    request: Request
):
    """
    Add issues to a sprint.
    """
    try:
        client = sync_service.clients.get(integration_id)
        if not client:
            return JSONResponse(
                status_code=404,
                content={"error": "Integration not found"}
            )
        
        if not client:
            return JSONResponse(
                status_code=400,
                content={"error": "Jira client not available"}
            )
        
        body = await request.json()
        issue_keys = body.get("issue_keys", [])
        
        if not issue_keys:
            return JSONResponse(
                status_code=400,
                content={"error": "No issue keys provided"}
            )
        
        success, message = client.add_issues_to_sprint(sprint_id, issue_keys)
        
        return JSONResponse(
            status_code=200 if success else 400,
            content=jsonable_encoder({
                "success": success,
                "message": message,
                "sprint_id": sprint_id,
                "issue_keys": issue_keys
            })
        )
            
    except Exception as e:
        logger.error(f"Error adding issues to sprint: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to add issues to sprint: {str(e)}"}
        )


@router.post("/jira/{integration_id}/boards/{board_id}/sprints")
async def create_sprint(
    integration_id: str,
    board_id: str,
    request: Request
):
    """
    Create a new sprint for a board.
    
    Expected JSON payload:
    {
        "name": "Sprint Name (required)",
        "goal": "Sprint goal (optional)", 
        "startDate": "2024-01-01T00:00:00.000Z (optional, ISO format)",
        "endDate": "2024-01-14T23:59:59.000Z (optional, ISO format)"
    }
    """
    try:
        # Use the global sync_service instead of dependency injection to avoid empty clients dict
        client = sync_service.clients.get(integration_id)
        if not client:
            logger.error(f"Integration {integration_id} not found in sync_service.clients: {list(sync_service.clients.keys())}")
            return JSONResponse(
                status_code=404,
                content={"error": "Integration not found or not connected. Please connect to Jira first."}
            )
        
        # Validate client connection
        if not client.is_connected:
            logger.warning(f"Client for integration {integration_id} is not connected. Attempting reconnection...")
            success, message = client.connect()
            if not success:
                return JSONResponse(
                    status_code=503,
                    content={"error": f"Jira connection failed: {message}"}
                )
        
        # Parse and validate request body
        try:
            body = await request.json()
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid JSON in request body: {str(e)}"}
            )
        
        name = body.get('name', '').strip()
        goal = body.get('goal', '').strip()
        start_date = body.get('startDate', '').strip()
        end_date = body.get('endDate', '').strip()
        
        # Validate required fields
        if not name:
            return JSONResponse(
                status_code=400,
                content={"error": "Sprint name is required and cannot be empty"}
            )
        
        # Validate board_id is not empty
        if not board_id or board_id.strip() == '':
            return JSONResponse(
                status_code=400,
                content={"error": "Board ID is required and cannot be empty"}
            )
        
        # If board_id is not numeric, try to get the numeric board ID
        resolved_board_id = board_id
        if not board_id.isdigit():
            numeric_board_id = client.get_board_id_by_key(board_id)
            if numeric_board_id:
                resolved_board_id = str(numeric_board_id)
                logger.info(f"Resolved board '{board_id}' to numeric ID: {numeric_board_id}")
            else:
                logger.warning(f"Could not resolve board '{board_id}' to numeric ID, trying as-is")
        
        logger.info(f"Creating sprint '{name}' on board '{board_id}' (resolved: '{resolved_board_id}') for integration {integration_id}")
        logger.debug(f"Sprint payload: name='{name}', goal='{goal}', start_date='{start_date}', end_date='{end_date}'")
        
        # Create sprint using client with proper error handling
        success, message, sprint_data = client.create_sprint(
            board_id=resolved_board_id,
            name=name,
            start_date=start_date if start_date else None,
            end_date=end_date if end_date else None,
            goal=goal if goal else None
        )
        
        if success:
            logger.info(f"Successfully created sprint: {message}")
            return JSONResponse(
                status_code=201,
                content=jsonable_encoder({
                    "success": True,
                    "message": message,
                    "sprint": sprint_data,
                    "integration_id": integration_id,
                    "board_id": board_id,
                    "resolved_board_id": resolved_board_id
                })
            )
        else:
            logger.error(f"Failed to create sprint: {message}")
            return JSONResponse(
                status_code=400,
                content=jsonable_encoder({
                    "success": False,
                    "error": message,
                    "integration_id": integration_id,
                    "board_id": board_id,
                    "resolved_board_id": resolved_board_id
                })
            )
            
    except Exception as e:
        error_msg = f"Unexpected error creating sprint: {str(e)}"
        logger.error(error_msg)
        return JSONResponse(
            status_code=500,
            content={"error": error_msg, "integration_id": integration_id, "board_id": board_id}
        )


@router.get("/jira/{integration_id}/sync/logs")
async def get_jira_sync_logs(integration_id: str):
    """Return basic sync status and placeholder logs for a Jira integration.
    This implements the missing /sync/logs endpoint that the frontend is calling.
    """
    try:
        status = sync_service.get_sync_status(integration_id)
        if status.get("status") == "not_found":
            return JSONResponse(status_code=404, content={"error": "Integration not found"})
        # Placeholder: real log collection not yet implemented.
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder({
                "integration_id": integration_id,
                "status": status,
                "logs": []
            })
        )
    except Exception as e:
        logger.error(f"Error getting sync logs: {str(e)}")
        return JSONResponse(status_code=500, content={"error": f"Failed to get sync logs: {str(e)}"})
