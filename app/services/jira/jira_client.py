# services/jira/jira_client.py
# Simplified Jira API client for communication with Jira instances

import logging
from jira import JIRA
from jira.exceptions import JIRAError
from typing import List, Dict, Any, Optional, Tuple
import time
from requests.exceptions import RequestException, Timeout, ConnectionError

from app.services.encryption.token_encryption import get_token_encryption_service

logger = logging.getLogger("cognisim_ai")


class JiraClient:
    """
    Simplified Jira API client for CogniSim integration.
    Handles connection testing, issue fetching, and error handling.
    Supports both encrypted and plaintext API tokens.
    """
    
    def __init__(self, jira_url: str, email: str, api_token: str, is_encrypted: bool = False):
        """
        Initialize Jira client.
        
        Args:
            jira_url: Jira instance URL
            email: User email for authentication
            api_token: API token for authentication (plaintext or encrypted)
            is_encrypted: Whether the api_token is encrypted (default: False)
        """
        self.jira_url = jira_url.rstrip('/')
        self.email = email
        self.is_encrypted = is_encrypted
        
        # Handle encrypted tokens
        if is_encrypted:
            try:
                encryption_service = get_token_encryption_service()
                self.api_token = encryption_service.decrypt(api_token)
                logger.info("Successfully decrypted API token for Jira client")
            except Exception as e:
                logger.error(f"Failed to decrypt API token: {str(e)}")
                raise ValueError("Invalid encrypted API token")
        else:
            self.api_token = api_token
            
        self.client: Optional[JIRA] = None
        self.is_connected = False
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 0.2  # 200ms between requests
    
    def connect(self) -> Tuple[bool, str]:
        """
        Test connection to Jira instance and initialize client.
        
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            logger.info(f"Connecting to Jira at {self.jira_url}")
            
            # Initialize Jira client with timeout
            self.client = JIRA(
                server=self.jira_url,
                basic_auth=(self.email, self.api_token),
                timeout=30,
                max_retries=3
            )
            
            # Test connection by getting current user info
            user_info = self.client.myself()
            self.is_connected = True
            
            # Get display name safely
            display_name = self.email
            if hasattr(user_info, 'displayName'):
                display_name = getattr(user_info, 'displayName', self.email)
            elif isinstance(user_info, dict) and 'displayName' in user_info:
                display_name = user_info['displayName']
            
            success_message = f"Connected to Jira as {display_name}"
            logger.info(success_message)
            
            return True, success_message
            
        except JIRAError as e:
            error_message = self._handle_jira_error(e)
            logger.error(f"Jira connection failed: {error_message}")
            return False, error_message
            
        except (ConnectionError, Timeout) as e:
            error_message = f"Network error: {str(e)}"
            logger.error(error_message)
            return False, error_message
            
        except Exception as e:
            error_message = f"Unexpected error: {str(e)}"
            logger.error(error_message)
            return False, error_message
    
    def _handle_jira_error(self, error: JIRAError) -> str:
        """Handle different types of Jira errors with specific messages."""
        if hasattr(error, 'status_code'):
            if error.status_code == 401:
                return "Invalid credentials - check your email and API token"
            elif error.status_code == 403:
                return "Access denied - insufficient permissions"
            elif error.status_code == 404:
                return "Jira instance not found - check your URL"
            elif error.status_code == 429:
                return "Rate limit exceeded - please wait and try again"
        return f"Jira API error: {str(error)}"

    def test_connection(self) -> Tuple[bool, str]:
        """
        Test if current connection is still valid.
        
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self.client:
            return self.connect()
        
        try:
            server_info = self.client.server_info()
            version = server_info.get('version', 'unknown') if isinstance(server_info, dict) else 'unknown'
            return True, f"Connection valid - Jira version {version}"
            
        except Exception as e:
            self.is_connected = False
            return False, f"Connection test failed: {str(e)}"

    def get_all_projects(self) -> List[Dict[str, Any]]:
        """
        Get all accessible projects from Jira.
        
        Returns:
            List of project dictionaries
        """
        if not self._ensure_connected():
            return []
        
        try:
            self._rate_limit()
            if self.client is None:
                logger.error("Jira client is None despite connection check")
                return []
            projects = self.client.projects()
            
            project_list = []
            for project in projects:
                project_dict = {
                    'key': getattr(project, 'key', ''),
                    'name': getattr(project, 'name', ''),
                    'description': getattr(project, 'description', ''),
                    'projectTypeKey': getattr(project, 'projectTypeKey', ''),
                    'url': f"{self.jira_url}/browse/{getattr(project, 'key', '')}"
                }
                project_list.append(project_dict)
            
            logger.info(f"Retrieved {len(project_list)} projects")
            return project_list
            
        except Exception as e:
            logger.error(f"Failed to get projects: {str(e)}")
            return []

    def get_project_issues(
        self, 
        project_key: str, 
        max_results: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Fetch issues from a Jira project.
        
        Args:
            project_key: Jira project key
            max_results: Maximum number of issues to fetch
            
        Returns:
            List of issue dictionaries
        """
        if not self._ensure_connected():
            return []
        
        try:
            self._rate_limit()
            
            # Check if client is None despite successful connection check
            if self.client is None:
                logger.error("Jira client is None despite connection check")
                return []
                
            # Build JQL query
            jql = f"project = {project_key} ORDER BY created DESC"
            
            logger.info(f"Fetching issues from project {project_key} (max: {max_results})")
            
            # Search issues
            issues = self.client.search_issues(
                jql,
                maxResults=max_results,
                expand='changelog'
            )
            
            # Convert to dictionaries
            issue_list = []
            for issue in issues:
                try:
                    issue_dict = self._convert_issue_to_dict(issue)
                    if issue_dict:
                        issue_list.append(issue_dict)
                except Exception as e:
                    logger.warning(f"Could not convert issue {getattr(issue, 'key', 'unknown')}: {str(e)}")
                    continue
            
            logger.info(f"Successfully fetched {len(issue_list)} issues from {project_key}")
            return issue_list
            
        except JIRAError as e:
            logger.error(f"Failed to fetch issues from {project_key}: {self._handle_jira_error(e)}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching issues: {str(e)}")
            return []

    def _convert_issue_to_dict(self, issue) -> Optional[Dict[str, Any]]:
        """Convert a Jira issue object to a dictionary."""
        try:
            if hasattr(issue, 'raw') and isinstance(issue.raw, dict):
                return issue.raw
            
            # Manual conversion
            fields = getattr(issue, 'fields', {})
            if hasattr(fields, '__dict__'):
                fields = fields.__dict__
            
            return {
                'key': getattr(issue, 'key', ''),
                'id': getattr(issue, 'id', ''),
                'fields': fields
            }
        except Exception:
            return None

    def _ensure_connected(self) -> bool:
        """
        Ensure client is connected, attempt reconnection if not.
        
        Returns:
            True if connected, False otherwise
        """
        if not self.is_connected or not self.client:
            success, _ = self.connect()
            return success
        return True
    
    def _rate_limit(self):
        """Simple rate limiting to avoid overwhelming Jira API."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    @classmethod
    def from_encrypted_credentials(cls, jira_url: str, email: str, encrypted_api_token: str):
        """
        Create a JiraClient instance from encrypted credentials.
        
        Args:
            jira_url: Jira server URL
            email: Email for authentication
            encrypted_api_token: Encrypted API token
            
        Returns:
            JiraClient: Configured client instance
        """
        return cls(
            jira_url=jira_url,
            email=email,
            api_token=encrypted_api_token,
            is_encrypted=True
        )
    
    @classmethod
    def from_plaintext_credentials(cls, jira_url: str, email: str, api_token: str):
        """
        Create a JiraClient instance from plaintext credentials.
        
        Args:
            jira_url: Jira server URL
            email: Email for authentication
            api_token: Plaintext API token
            
        Returns:
            JiraClient: Configured client instance
        """
        return cls(
            jira_url=jira_url,
            email=email,
            api_token=api_token,
            is_encrypted=False
        )
    
    def close(self):
        """Close the Jira client connection."""
        if self.client:
            try:
                self.client.close()
            except:
                pass
        self.client = None
        self.is_connected = False
        logger.info("Jira client connection closed")
