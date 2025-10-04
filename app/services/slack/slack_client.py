# services/slack/slack_client.py
# Slack API client for CogniSim integration
# Handles Slack Web API calls with encrypted token management (matches Jira pattern)

import logging
from typing import Optional, List, Dict, Any, Tuple, TYPE_CHECKING

logger = logging.getLogger("cognisim_ai")

if TYPE_CHECKING:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

try:
    from slack_sdk import WebClient  # type: ignore
    from slack_sdk.errors import SlackApiError  # type: ignore
    SLACK_SDK_AVAILABLE = True
except ImportError:
    SLACK_SDK_AVAILABLE = False
    WebClient = None  # type: ignore
    SlackApiError = None  # type: ignore
    logger.warning("slack-sdk not installed. Run: pip install slack-sdk")

from app.services.encryption.token_encryption import get_token_encryption_service


class SlackClient:
    """
    Slack API client for CogniSim integration.
    Handles Slack Web API calls with encrypted token management.
    Similar to JiraClient - follows same pattern for consistency.
    """
    
    def __init__(self, bot_token: str, is_encrypted: bool = True):
        """
        Initialize Slack client.
        
        Args:
            bot_token: Slack bot token (xoxb-...)
            is_encrypted: Whether the token is encrypted (default: True)
        """
        if not SLACK_SDK_AVAILABLE:
            raise ImportError("slack-sdk is required. Install with: pip install slack-sdk")
        
        self.is_encrypted = is_encrypted
        
        # Decrypt token if needed (same pattern as Jira)
        if is_encrypted:
            try:
                encryption_service = get_token_encryption_service()
                self.bot_token = encryption_service.decrypt(bot_token)
                logger.info("Successfully decrypted Slack bot token")
            except Exception as e:
                logger.error(f"Failed to decrypt Slack token: {str(e)}")
                raise ValueError("Invalid encrypted Slack token")
        else:
            self.bot_token = bot_token
        
        # Initialize Slack WebClient
        self.client: Any = WebClient(token=self.bot_token)  # type: ignore
        self.is_connected = False
    
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test connection to Slack workspace.
        
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            response = self.client.auth_test()
            
            if response["ok"]:
                self.is_connected = True
                workspace_name = response.get("team", "Unknown")
                bot_user_id = response.get("user_id", "Unknown")
                
                success_msg = f"Connected to Slack workspace '{workspace_name}' as bot '{bot_user_id}'"
                logger.info(success_msg)
                return True, success_msg
            else:
                error_msg = f"Slack auth test failed: {response.get('error', 'Unknown error')}"
                logger.error(error_msg)
                return False, error_msg
                
        except SlackApiError as e:  # type: ignore
            error_msg = f"Slack API error: {e.response['error']}"
            logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def send_message(
        self,
        channel: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]] = None,
        thread_ts: Optional[str] = None,
        username: Optional[str] = None,
        icon_emoji: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Send a message to a Slack channel.
        
        Args:
            channel: Channel ID (e.g., C1234567890) or name (#general)
            text: Message text (fallback for notifications)
            blocks: Slack Block Kit blocks (optional, for rich formatting)
            thread_ts: Thread timestamp for replies (optional)
            username: Override bot username (optional)
            icon_emoji: Override bot icon (optional, e.g., :rocket:)
        
        Returns:
            Tuple of (success: bool, message_ts: str, error: str)
        """
        try:
            kwargs = {
                "channel": channel,
                "text": text,
            }
            
            if blocks:
                kwargs["blocks"] = blocks  # type: ignore
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            if username:
                kwargs["username"] = username
            if icon_emoji:
                kwargs["icon_emoji"] = icon_emoji
            
            response = self.client.chat_postMessage(**kwargs)  # type: ignore
            
            if response["ok"]:
                message_ts = response["ts"]
                logger.info(f"Message sent to Slack channel {channel}: {message_ts}")
                return True, message_ts, None
            else:
                error = response.get("error", "Unknown error")
                logger.error(f"Failed to send Slack message: {error}")
                return False, None, error
                
        except SlackApiError as e:  # type: ignore
            error_msg = e.response["error"]
            logger.error(f"Slack API error sending message: {error_msg}")
            return False, None, error_msg
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Unexpected error sending message: {error_msg}")
            return False, None, error_msg
    
    def list_channels(self, limit: int = 100, exclude_archived: bool = True) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """
        List all channels in the workspace.
        
        Args:
            limit: Maximum number of channels to return
            exclude_archived: Exclude archived channels (default: True)
        
        Returns:
            Tuple of (success: bool, channels: List[Dict], error: str)
        """
        try:
            response = self.client.conversations_list(
                limit=limit,
                types="public_channel,private_channel",
                exclude_archived=exclude_archived
            )
            
            if response["ok"]:
                channels = response.get("channels", [])
                logger.info(f"Retrieved {len(channels)} Slack channels")
                return True, channels, None  # type: ignore
            else:
                error = response.get("error", "Unknown error")
                logger.error(f"Failed to list Slack channels: {error}")
                return False, [], error
                
        except SlackApiError as e:  # type: ignore
            error_msg = e.response["error"]
            logger.error(f"Slack API error listing channels: {error_msg}")
            return False, [], error_msg
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Unexpected error listing channels: {error_msg}")
            return False, [], error_msg
    
    def list_users(self, limit: int = 100) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """
        List all users in the workspace.
        
        Args:
            limit: Maximum number of users to return
        
        Returns:
            Tuple of (success: bool, users: List[Dict], error: str)
        """
        try:
            response = self.client.users_list(limit=limit)
            
            if response["ok"]:
                users = response.get("members", [])
                logger.info(f"Retrieved {len(users)} Slack users")
                return True, users, None  # type: ignore
            else:
                error = response.get("error", "Unknown error")
                logger.error(f"Failed to list Slack users: {error}")
                return False, [], error
                
        except SlackApiError as e:  # type: ignore
            error_msg = e.response["error"]
            logger.error(f"Slack API error listing users: {error_msg}")
            return False, [], error_msg
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Unexpected error listing users: {error_msg}")
            return False, [], error_msg
    
    def get_channel_info(self, channel_id: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Get information about a specific channel.
        
        Args:
            channel_id: Channel ID (C1234567890)
        
        Returns:
            Tuple of (success: bool, channel_info: Dict, error: str)
        """
        try:
            response = self.client.conversations_info(channel=channel_id)
            
            if response["ok"]:
                channel_info = response["channel"]
                logger.info(f"Retrieved info for Slack channel {channel_id}")
                return True, channel_info, None
            else:
                error = response.get("error", "Unknown error")
                logger.error(f"Failed to get channel info: {error}")
                return False, None, error
                
        except SlackApiError as e:  # type: ignore
            error_msg = e.response["error"]
            logger.error(f"Slack API error getting channel info: {error_msg}")
            return False, None, error_msg
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Unexpected error getting channel info: {error_msg}")
            return False, None, error_msg
    
    def post_ephemeral_message(
        self,
        channel: str,
        user: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Post an ephemeral message (only visible to one user).
        Useful for slash command responses.
        
        Args:
            channel: Channel ID
            user: User ID to show message to
            text: Message text
            blocks: Slack Block Kit blocks (optional)
        
        Returns:
            Tuple of (success: bool, error: str)
        """
        try:
            kwargs = {
                "channel": channel,
                "user": user,
                "text": text,
            }
            
            if blocks:
                kwargs["blocks"] = blocks  # type: ignore
            
            response = self.client.chat_postEphemeral(**kwargs)  # type: ignore
            
            if response["ok"]:
                logger.info(f"Ephemeral message sent to user {user} in channel {channel}")
                return True, None
            else:
                error = response.get("error", "Unknown error")
                logger.error(f"Failed to send ephemeral message: {error}")
                return False, error
                
        except SlackApiError as e:  # type: ignore
            error_msg = e.response["error"]
            logger.error(f"Slack API error sending ephemeral message: {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Unexpected error sending ephemeral message: {error_msg}")
            return False, error_msg
