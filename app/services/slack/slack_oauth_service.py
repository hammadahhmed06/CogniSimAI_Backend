# services/slack/slack_oauth_service.py
# Handles Slack OAuth 2.0 flow for workspace installation

import logging
import secrets
from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
from uuid import UUID
import urllib.parse

logger = logging.getLogger("cognisim_ai")

try:
    from slack_sdk.oauth import AuthorizeUrlGenerator
    from slack_sdk.web import WebClient
    from slack_sdk.errors import SlackApiError
    SLACK_SDK_AVAILABLE = True
except ImportError:
    SLACK_SDK_AVAILABLE = False
    logger.warning("slack-sdk not installed. Run: pip install slack-sdk")

from app.core.dependencies import supabase
from app.core.config import Settings
from app.services.encryption.token_encryption import get_token_encryption_service


class SlackOAuthService:
    """
    Handles Slack OAuth 2.0 authorization flow.
    
    Flow:
    1. generate_authorization_url() → Returns Slack OAuth URL with state
    2. User authorizes on Slack
    3. Slack redirects to callback with code + state
    4. exchange_code_for_token() → Exchanges code for access tokens
    5. Returns workspace info + encrypted tokens
    """
    
    def __init__(self):
        """Initialize OAuth service with settings."""
        if not SLACK_SDK_AVAILABLE:
            raise ImportError("slack-sdk is required. Install with: pip install slack-sdk")
        
        self.settings = Settings()
        self.client_id = self.settings.SLACK_CLIENT_ID
        self.client_secret = self.settings.SLACK_CLIENT_SECRET.get_secret_value() if self.settings.SLACK_CLIENT_SECRET else None
        self.redirect_uri = self.settings.SLACK_REDIRECT_URI
        
        # Validate config
        if not self.client_id or not self.client_secret or not self.redirect_uri:
            logger.warning("Slack OAuth not configured. Set SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, SLACK_REDIRECT_URI")
            raise ValueError("Slack OAuth configuration missing. Cannot initialize OAuth service.")
        
        # Type assertions for type checker (already validated above)
        assert self.client_id is not None
        assert self.client_secret is not None
        assert self.redirect_uri is not None
        
        # OAuth scopes required for the app
        self.scopes = [
            "channels:read",      # List public channels
            "chat:write",         # Send messages
            "users:read",         # List workspace members
            "channels:history",   # Read channel messages
            "groups:read",        # List private channels
            "im:read",            # List DMs
            "mpim:read",          # List group DMs
            "team:read",          # Read workspace info
        ]
    
    def generate_authorization_url(
        self,
        workspace_id: UUID,
        user_id: UUID,
        redirect_after_auth: Optional[str] = None
    ) -> Tuple[str, str, datetime]:
        """
        Generate Slack OAuth authorization URL.
        
        Args:
            workspace_id: Workspace to connect Slack to
            user_id: User initiating OAuth flow
            redirect_after_auth: Optional URL to redirect after OAuth completion
        
        Returns:
            Tuple of (authorization_url, state, expires_at)
        """
        try:
            # Generate random state for CSRF protection
            state = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(minutes=10)
            
            # Store state in database
            state_data = {
                "state": state,
                "workspace_id": str(workspace_id),
                "user_id": str(user_id),
                "redirect_uri": redirect_after_auth,
                "expires_at": expires_at.isoformat(),
                "is_used": False,
                "created_at": datetime.utcnow().isoformat()
            }
            
            insert_res = supabase.table("slack_oauth_states").insert(state_data).execute()
            if not getattr(insert_res, "data", []):
                raise Exception("Failed to store OAuth state")
            
            logger.info(f"OAuth state created for workspace {workspace_id}, user {user_id}")
            
            # Build authorization URL
            url_generator = AuthorizeUrlGenerator(  # type: ignore
                client_id=self.client_id,  # type: ignore
                scopes=self.scopes,
                redirect_uri=self.redirect_uri  # type: ignore
            )
            
            auth_url = url_generator.generate(state=state)
            
            return auth_url, state, expires_at
            
        except Exception as e:
            logger.error(f"Failed to generate OAuth URL: {e}")
            raise
    
    def validate_state(self, state: str, user_id: UUID) -> Tuple[bool, Optional[UUID], Optional[str]]:
        """
        Validate OAuth state parameter (CSRF protection).
        
        Args:
            state: State parameter from OAuth callback
            user_id: Current user ID
        
        Returns:
            Tuple of (is_valid, workspace_id, redirect_uri)
        """
        try:
            # Look up state in database
            state_res = (
                supabase.table("slack_oauth_states")
                .select("*")
                .eq("state", state)
                .eq("user_id", str(user_id))
                .eq("is_used", False)
                .single()
                .execute()
            )
            
            state_record = getattr(state_res, "data", None)
            
            if not state_record:
                logger.warning(f"Invalid or already-used OAuth state: {state[:10]}...")
                return False, None, None
            
            # Check expiration
            expires_at = datetime.fromisoformat(state_record["expires_at"].replace("Z", "+00:00"))
            if datetime.utcnow() > expires_at:
                logger.warning(f"Expired OAuth state: {state[:10]}...")
                return False, None, None
            
            # Mark as used
            supabase.table("slack_oauth_states").update({
                "is_used": True,
                "used_at": datetime.utcnow().isoformat()
            }).eq("state", state).execute()
            
            workspace_id = UUID(state_record["workspace_id"])
            redirect_uri = state_record.get("redirect_uri")
            
            logger.info(f"OAuth state validated for workspace {workspace_id}")
            
            return True, workspace_id, redirect_uri
            
        except Exception as e:
            logger.error(f"Failed to validate OAuth state: {e}")
            return False, None, None
    
    def exchange_code_for_token(
        self,
        code: str,
        workspace_id: UUID
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Exchange OAuth authorization code for access tokens.
        
        Args:
            code: OAuth authorization code from callback
            workspace_id: Workspace ID from validated state
        
        Returns:
            Tuple of (success, integration_data, error_message)
            
        integration_data contains:
            - slack_workspace_id
            - slack_workspace_name
            - slack_team_id
            - bot_user_id
            - bot_access_token (encrypted)
            - user_access_token (encrypted, optional)
            - scopes
            - webhook_url (optional)
            - default_channel_id (optional)
        """
        try:
            # Exchange code for token using Slack OAuth v2 API
            client = WebClient()  # type: ignore
            
            response = client.oauth_v2_access(  # type: ignore
                client_id=self.client_id,  # type: ignore
                client_secret=self.client_secret,  # type: ignore
                code=code,
                redirect_uri=self.redirect_uri  # type: ignore
            )
            
            if not response["ok"]:
                error = response.get("error", "Unknown error")
                logger.error(f"Slack OAuth token exchange failed: {error}")
                return False, None, f"Token exchange failed: {error}"
            
            # Extract data from response
            team = response.get("team", {})
            authed_user = response.get("authed_user", {})
            access_token = response.get("access_token")  # User token (xoxp-...)
            
            # Bot token from bot scope
            bot_user_id = None
            bot_access_token = None
            if "bot_user_id" in response:
                bot_user_id = response["bot_user_id"]
            if "access_token" in response:
                bot_access_token = response["access_token"]
            
            # Try to get from nested structure (v2 API)
            if not bot_access_token and "bot" in response and response["bot"]:
                bot_access_token = response["bot"].get("bot_access_token")  # type: ignore
                bot_user_id = response["bot"].get("bot_user_id")  # type: ignore
            
            if not bot_access_token:
                logger.error("No bot access token in OAuth response")
                return False, None, "Bot token not granted. Ensure bot scopes are configured."
            
            # Encrypt tokens
            encryption_service = get_token_encryption_service()
            encrypted_bot_token = encryption_service.encrypt(bot_access_token)
            encrypted_user_token = encryption_service.encrypt(access_token) if access_token else None
            
            # Extract scopes
            scope_string = response.get("scope", "")
            scopes = scope_string.split(",") if scope_string else []
            
            # Get incoming webhook if granted
            incoming_webhook = response.get("incoming_webhook", {})
            webhook_url = incoming_webhook.get("url")
            webhook_channel = incoming_webhook.get("channel")
            webhook_channel_id = incoming_webhook.get("channel_id")
            
            # Build integration data
            integration_data = {
                "workspace_id": str(workspace_id),
                "slack_workspace_id": team.get("id", ""),
                "slack_workspace_name": team.get("name"),
                "slack_team_id": team.get("id"),
                "bot_user_id": bot_user_id,
                "bot_token": encrypted_bot_token,
                "user_token": encrypted_user_token,
                "scopes": scopes,
                "webhook_url": webhook_url,
                "webhook_channel": webhook_channel_id or webhook_channel,
                "default_channel_id": webhook_channel_id,
                "default_channel_name": webhook_channel,
                "notifications_enabled": True,
                "slash_commands_enabled": False,
                "is_active": True,
                "last_sync_at": datetime.utcnow().isoformat(),
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            
            logger.info(f"OAuth token exchange successful for workspace {workspace_id}")
            logger.info(f"Slack workspace: {team.get('name')} ({team.get('id')})")
            logger.info(f"Bot user: {bot_user_id}")
            logger.info(f"Scopes: {', '.join(scopes)}")
            
            return True, integration_data, None
            
        except SlackApiError as e:  # type: ignore
            error_msg = e.response["error"]
            logger.error(f"Slack API error during token exchange: {error_msg}")
            return False, None, f"Slack API error: {error_msg}"
        except Exception as e:
            logger.error(f"Unexpected error during token exchange: {e}")
            return False, None, f"Unexpected error: {str(e)}"


# Singleton instance
_oauth_service: Optional[SlackOAuthService] = None

def get_slack_oauth_service() -> SlackOAuthService:
    """Get singleton OAuth service instance."""
    global _oauth_service
    if _oauth_service is None:
        _oauth_service = SlackOAuthService()
    return _oauth_service
