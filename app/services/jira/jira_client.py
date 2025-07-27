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

    # Writing Operations for Complete CRUD Support
    
    def create_issue(self, project_key: str, summary: str, description: str = "", 
                    issue_type: str = "Task", **kwargs) -> Tuple[bool, str, Optional[str]]:
        """
        Create a new issue in Jira.
        
        Args:
            project_key: Project key where issue will be created
            summary: Issue summary/title
            description: Issue description
            issue_type: Issue type (Task, Bug, Story, etc.)
            **kwargs: Additional fields (priority, assignee, labels, etc.)
            
        Returns:
            Tuple of (success: bool, message: str, issue_key: Optional[str])
        """
        if not self._ensure_connected():
            return False, "Not connected to Jira", None
        
        try:
            self._rate_limit()
            
            # Build issue fields
            issue_fields = {
                'project': {'key': project_key},
                'summary': summary,
                'description': description,
                'issuetype': {'name': issue_type}
            }
            
            # Add optional fields
            if 'priority' in kwargs:
                issue_fields['priority'] = {'name': kwargs['priority']}
            if 'assignee' in kwargs:
                issue_fields['assignee'] = {'name': kwargs['assignee']}
            if 'labels' in kwargs and isinstance(kwargs['labels'], list):
                issue_fields['labels'] = kwargs['labels']
            if 'components' in kwargs and isinstance(kwargs['components'], list):
                issue_fields['components'] = [{'name': comp} for comp in kwargs['components']]
            
            # Create the issue
            new_issue = self.client.create_issue(fields=issue_fields)
            issue_key = getattr(new_issue, 'key', str(new_issue))
            
            logger.info(f"Successfully created issue {issue_key}")
            return True, f"Issue {issue_key} created successfully", issue_key
            
        except JIRAError as e:
            error_msg = self._handle_jira_error(e)
            logger.error(f"Failed to create issue: {error_msg}")
            return False, f"Failed to create issue: {error_msg}", None
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg, None
    
    def update_issue(self, issue_key: str, fields_to_update: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Update an existing issue in Jira.
        
        Args:
            issue_key: Key of the issue to update
            fields_to_update: Dictionary of fields to update
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self._ensure_connected():
            return False, "Not connected to Jira"
        
        try:
            self._rate_limit()
            
            # Get the issue first
            issue = self.client.issue(issue_key)
            
            # Update fields
            update_fields = {}
            for field, value in fields_to_update.items():
                if field == 'summary':
                    update_fields['summary'] = value
                elif field == 'description':
                    update_fields['description'] = value
                elif field == 'priority':
                    update_fields['priority'] = {'name': value}
                elif field == 'assignee':
                    update_fields['assignee'] = {'name': value}
                elif field == 'status':
                    # Status updates require transitions
                    continue
                else:
                    update_fields[field] = value
            
            if update_fields:
                issue.update(fields=update_fields)
                logger.info(f"Successfully updated issue {issue_key}")
                return True, f"Issue {issue_key} updated successfully"
            else:
                return True, "No fields to update"
                
        except JIRAError as e:
            error_msg = self._handle_jira_error(e)
            logger.error(f"Failed to update issue {issue_key}: {error_msg}")
            return False, f"Failed to update issue: {error_msg}"
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def transition_issue(self, issue_key: str, transition_name: str) -> Tuple[bool, str]:
        """
        Transition an issue to a new status.
        
        Args:
            issue_key: Key of the issue to transition
            transition_name: Name of the transition (e.g., "Done", "In Progress")
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self._ensure_connected():
            return False, "Not connected to Jira"
        
        try:
            self._rate_limit()
            
            issue = self.client.issue(issue_key)
            transitions = self.client.transitions(issue)
            
            # Find the transition
            transition_id = None
            for transition in transitions:
                if transition['name'].lower() == transition_name.lower():
                    transition_id = transition['id']
                    break
            
            if not transition_id:
                available = [t['name'] for t in transitions]
                return False, f"Transition '{transition_name}' not available. Available: {available}"
            
            # Perform transition
            self.client.transition_issue(issue, transition_id)
            logger.info(f"Successfully transitioned issue {issue_key} to {transition_name}")
            return True, f"Issue {issue_key} transitioned to {transition_name}"
            
        except JIRAError as e:
            error_msg = self._handle_jira_error(e)
            logger.error(f"Failed to transition issue {issue_key}: {error_msg}")
            return False, f"Failed to transition issue: {error_msg}"
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def add_comment(self, issue_key: str, comment_body: str) -> Tuple[bool, str]:
        """
        Add a comment to an issue.
        
        Args:
            issue_key: Key of the issue
            comment_body: Comment text
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self._ensure_connected():
            return False, "Not connected to Jira"
        
        try:
            self._rate_limit()
            
            self.client.add_comment(issue_key, comment_body)
            logger.info(f"Successfully added comment to issue {issue_key}")
            return True, f"Comment added to issue {issue_key}"
            
        except JIRAError as e:
            error_msg = self._handle_jira_error(e)
            logger.error(f"Failed to add comment to {issue_key}: {error_msg}")
            return False, f"Failed to add comment: {error_msg}"
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def delete_issue(self, issue_key: str) -> Tuple[bool, str]:
        """
        Delete an issue from Jira.
        
        Args:
            issue_key: Key of the issue to delete
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self._ensure_connected():
            return False, "Not connected to Jira"
        
        try:
            self._rate_limit()
            
            issue = self.client.issue(issue_key)
            issue.delete()
            logger.info(f"Successfully deleted issue {issue_key}")
            return True, f"Issue {issue_key} deleted successfully"
            
        except JIRAError as e:
            error_msg = self._handle_jira_error(e)
            logger.error(f"Failed to delete issue {issue_key}: {error_msg}")
            return False, f"Failed to delete issue: {error_msg}"
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg

    # User and Project Management Operations
    
    def get_project_users(self, project_key: str) -> List[Dict[str, Any]]:
        """
        Get all users with access to a project.
        
        Args:
            project_key: Project key
            
        Returns:
            List of user dictionaries
        """
        if not self._ensure_connected():
            return []
        
        try:
            self._rate_limit()
            
            # Get assignable users for the project
            users = self.client.search_assignable_users_for_projects('', projectKeys=project_key)
            
            user_list = []
            for user in users:
                user_dict = {
                    'accountId': getattr(user, 'accountId', ''),
                    'displayName': getattr(user, 'displayName', ''),
                    'emailAddress': getattr(user, 'emailAddress', ''),
                    'active': getattr(user, 'active', True)
                }
                user_list.append(user_dict)
            
            logger.info(f"Retrieved {len(user_list)} users for project {project_key}")
            return user_list
            
        except Exception as e:
            logger.error(f"Failed to get users for project {project_key}: {str(e)}")
            return []
    
    def get_issue_types(self, project_key: str) -> List[Dict[str, Any]]:
        """
        Get available issue types for a project.
        
        Args:
            project_key: Project key
            
        Returns:
            List of issue type dictionaries
        """
        if not self._ensure_connected():
            return []
        
        try:
            self._rate_limit()
            
            project = self.client.project(project_key)
            issue_types = getattr(project, 'issueTypes', [])
            
            type_list = []
            for issue_type in issue_types:
                type_dict = {
                    'id': getattr(issue_type, 'id', ''),
                    'name': getattr(issue_type, 'name', ''),
                    'description': getattr(issue_type, 'description', ''),
                    'subtask': getattr(issue_type, 'subtask', False)
                }
                type_list.append(type_dict)
            
            logger.info(f"Retrieved {len(type_list)} issue types for project {project_key}")
            return type_list
            
        except Exception as e:
            logger.error(f"Failed to get issue types for project {project_key}: {str(e)}")
            return []
    
    # Sprint Management (for Agile projects)
    
    def get_active_sprints(self, board_id: str) -> List[Dict[str, Any]]:
        """
        Get active sprints for a board.
        
        Args:
            board_id: Board ID
            
        Returns:
            List of sprint dictionaries
        """
        if not self._ensure_connected():
            return []
        
        try:
            self._rate_limit()
            
            sprints = self.client.sprints(board_id, state='active')
            
            sprint_list = []
            for sprint in sprints:
                sprint_dict = {
                    'id': getattr(sprint, 'id', ''),
                    'name': getattr(sprint, 'name', ''),
                    'state': getattr(sprint, 'state', ''),
                    'startDate': getattr(sprint, 'startDate', ''),
                    'endDate': getattr(sprint, 'endDate', ''),
                    'goal': getattr(sprint, 'goal', '')
                }
                sprint_list.append(sprint_dict)
            
            logger.info(f"Retrieved {len(sprint_list)} active sprints for board {board_id}")
            return sprint_list
            
        except Exception as e:
            logger.error(f"Failed to get active sprints for board {board_id}: {str(e)}")
            return []
    
    def add_issues_to_sprint(self, sprint_id: str, issue_keys: List[str]) -> Tuple[bool, str]:
        """
        Add issues to a sprint.
        
        Args:
            sprint_id: Sprint ID
            issue_keys: List of issue keys to add
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self._ensure_connected():
            return False, "Not connected to Jira"
        
        try:
            self._rate_limit()
            
            self.client.add_issues_to_sprint(sprint_id, issue_keys)
            logger.info(f"Successfully added {len(issue_keys)} issues to sprint {sprint_id}")
            return True, f"Added {len(issue_keys)} issues to sprint"
            
        except Exception as e:
            error_msg = f"Failed to add issues to sprint: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    # Bulk Operations
    
    def bulk_create_issues(self, issues_data: List[Dict[str, Any]]) -> Tuple[bool, str, List[str]]:
        """
        Create multiple issues in bulk.
        
        Args:
            issues_data: List of issue data dictionaries
            
        Returns:
            Tuple of (success: bool, message: str, created_issue_keys: List[str])
        """
        if not self._ensure_connected():
            return False, "Not connected to Jira", []
        
        created_keys = []
        failed_count = 0
        
        for issue_data in issues_data:
            try:
                success, message, issue_key = self.create_issue(**issue_data)
                if success and issue_key:
                    created_keys.append(issue_key)
                else:
                    failed_count += 1
                    logger.warning(f"Failed to create issue: {message}")
            except Exception as e:
                failed_count += 1
                logger.error(f"Error creating issue: {str(e)}")
        
        total_count = len(issues_data)
        success_count = len(created_keys)
        
        if success_count == total_count:
            return True, f"Successfully created all {total_count} issues", created_keys
        elif success_count > 0:
            return True, f"Created {success_count}/{total_count} issues ({failed_count} failed)", created_keys
        else:
            return False, f"Failed to create any issues (0/{total_count})", created_keys
    
    def bulk_update_issues(self, updates: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """
        Update multiple issues in bulk.
        
        Args:
            updates: List of update dictionaries with 'issue_key' and 'fields'
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self._ensure_connected():
            return False, "Not connected to Jira"
        
        success_count = 0
        failed_count = 0
        
        for update_data in updates:
            try:
                issue_key = update_data['issue_key']
                fields = update_data['fields']
                
                success, message = self.update_issue(issue_key, fields)
                if success:
                    success_count += 1
                else:
                    failed_count += 1
                    logger.warning(f"Failed to update {issue_key}: {message}")
            except Exception as e:
                failed_count += 1
                logger.error(f"Error updating issue: {str(e)}")
        
        total_count = len(updates)
        
        if success_count == total_count:
            return True, f"Successfully updated all {total_count} issues"
        elif success_count > 0:
            return True, f"Updated {success_count}/{total_count} issues ({failed_count} failed)"
        else:
            return False, f"Failed to update any issues (0/{total_count})"
    
    # Advanced Search and Filtering
    
    def search_issues_jql(self, jql: str, max_results: int = 50, 
                         fields: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Search issues using JQL (Jira Query Language).
        
        Args:
            jql: JQL query string
            max_results: Maximum number of results
            fields: Specific fields to retrieve
            
        Returns:
            List of issue dictionaries
        """
        if not self._ensure_connected():
            return []
        
        try:
            self._rate_limit()
            
            # Set default fields if none specified
            if fields is None:
                fields = ['summary', 'status', 'assignee', 'created', 'updated']
            
            issues = self.client.search_issues(
                jql, 
                maxResults=max_results,
                fields=fields,
                expand='changelog'
            )
            
            issue_list = []
            for issue in issues:
                try:
                    issue_dict = self._convert_issue_to_dict(issue)
                    if issue_dict:
                        issue_list.append(issue_dict)
                except Exception as e:
                    logger.warning(f"Could not convert issue: {str(e)}")
                    continue
            
            logger.info(f"JQL search returned {len(issue_list)} issues")
            return issue_list
            
        except Exception as e:
            logger.error(f"JQL search failed: {str(e)}")
            return []
    
    def get_issue_history(self, issue_key: str) -> List[Dict[str, Any]]:
        """
        Get change history for an issue.
        
        Args:
            issue_key: Issue key
            
        Returns:
            List of change history dictionaries
        """
        if not self._ensure_connected():
            return []
        
        try:
            self._rate_limit()
            
            issue = self.client.issue(issue_key, expand='changelog')
            changelog = getattr(issue, 'changelog', {})
            histories = getattr(changelog, 'histories', [])
            
            history_list = []
            for history in histories:
                history_dict = {
                    'id': getattr(history, 'id', ''),
                    'author': getattr(history, 'author', {}),
                    'created': getattr(history, 'created', ''),
                    'items': getattr(history, 'items', [])
                }
                history_list.append(history_dict)
            
            logger.info(f"Retrieved {len(history_list)} history entries for {issue_key}")
            return history_list
            
        except Exception as e:
            logger.error(f"Failed to get issue history for {issue_key}: {str(e)}")
            return []

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
