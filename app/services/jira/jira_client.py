# services/jira/jira_client.py
# Simplified Jira API client for communication with Jira instances

import logging
from jira import JIRA
from jira.exceptions import JIRAError
from typing import List, Dict, Any, Optional, Tuple, Union
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
            client = self.client
            assert client is not None
            user_info = client.myself()
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
        """Handle different types of Jira errors with specific messages.

        Attempts to surface the server-provided error payload for 400 responses so
        users can see field-level messages like "Field 'priority' cannot be set".
        """
        try:
            status = getattr(error, 'status_code', None)
            # Try to extract detailed message from response text if present
            detail = None
            resp = getattr(error, 'response', None)
            if resp is not None:
                try:
                    text = getattr(resp, 'text', '') or ''
                    if text:
                        import json as _json
                        try:
                            payload = _json.loads(text)
                            # Common Jira error shapes
                            if isinstance(payload, dict):
                                if payload.get('errorMessages'):
                                    detail = "; ".join([str(m) for m in payload.get('errorMessages', [])])
                                elif payload.get('errors'):
                                    # Collect key: message pairs
                                    pairs = [f"{k}: {v}" for k, v in payload.get('errors', {}).items()]
                                    if pairs:
                                        detail = "; ".join(pairs)
                        except Exception:
                            # Fallback to raw text
                            detail = text[:500]
                except Exception:
                    pass

            if status == 401:
                return "Invalid credentials - check your email and API token"
            elif status == 403:
                return "Access denied - insufficient permissions"
            elif status == 404:
                return "Jira instance not found - check your URL"
            elif status == 429:
                return "Rate limit exceeded - please wait and try again"
            elif status == 400 and detail:
                return f"Bad request: {detail}"
        except Exception:
            pass
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
            client = self.client
            assert client is not None
            server_info = client.server_info()
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
            client = self.client
            assert client is not None
            projects = client.projects()
            
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
            client = self.client
            assert client is not None
            issues = client.search_issues(
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
            client = self.client
            assert client is not None  # for type checkers
            new_issue = client.create_issue(fields=issue_fields)
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
            client = self.client
            assert client is not None
            issue = client.issue(issue_key)
            
            # Update fields
            update_fields: Dict[str, Any] = {}
            for field, value in fields_to_update.items():
                # Pass through dictionaries as-is to allow callers to specify exact payloads
                if isinstance(value, dict):
                    update_fields[field] = value
                    continue

                if field == 'summary':
                    update_fields['summary'] = value
                elif field == 'description':
                    update_fields['description'] = value
                elif field == 'priority':
                    # Accept either name or id
                    if isinstance(value, str) and value.isdigit():
                        update_fields['priority'] = {'id': value}
                    else:
                        update_fields['priority'] = {'name': value}
                elif field == 'assignee':
                    # Jira Cloud uses accountId, Jira Server/Data Center may use name
                    if isinstance(value, str):
                        # Heuristic: accountId often contains ':' or is long/UUID-like
                        if ':' in value or len(value) >= 16:
                            update_fields['assignee'] = {'accountId': value}
                        else:
                            update_fields['assignee'] = {'name': value}
                    else:
                        update_fields['assignee'] = value  # fallback
                elif field == 'status':
                    # Status updates require transitions API
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
            
            client = self.client
            assert client is not None
            issue = client.issue(issue_key)
            transitions = client.transitions(issue)
            
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
            client.transition_issue(issue, transition_id)
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

    def get_issue(self, issue_key: str) -> Optional[Dict[str, Any]]:
        """Fetch a single issue by key and return as dict."""
        if not self._ensure_connected():
            return None
        try:
            self._rate_limit()
            client = self.client
            assert client is not None
            issue = client.issue(issue_key, expand='changelog')
            return self._convert_issue_to_dict(issue)
        except Exception as e:
            logger.error(f"Failed to fetch issue {issue_key}: {str(e)}")
            return None

    def get_issue_editmeta(self, issue_key: str) -> Dict[str, Any]:
        """Return edit metadata for an issue, including which fields are editable.

        Useful for checking if 'priority' is editable and what values are allowed.
        """
        if not self._ensure_connected():
            return {}
        try:
            self._rate_limit()
            client = self.client
            assert client is not None
            meta = client.editmeta(issue_key)
            # Ensure it's JSON-serializable
            if hasattr(meta, 'raw'):
                return getattr(meta, 'raw', {})
            if isinstance(meta, dict):
                return meta
            return {}
        except Exception as e:
            logger.error(f"Failed to get editmeta for {issue_key}: {str(e)}")
            return {}
    
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
            
            client = self.client
            assert client is not None
            client.add_comment(issue_key, comment_body)
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
            
            client = self.client
            assert client is not None
            issue = client.issue(issue_key)
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
            client = self.client
            assert client is not None
            users = client.search_assignable_users_for_projects('', projectKeys=project_key)
            
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
            client = self.client
            assert client is not None
            project = client.project(project_key)
            issue_types = getattr(project, 'issueTypes', [])
            type_list: List[Dict[str, Any]] = []
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

    def get_priorities(self) -> List[Dict[str, Any]]:
        """Return global list of priorities (id + name)."""
        if not self._ensure_connected():
            return []
        try:
            self._rate_limit()
            client = self.client
            assert client is not None
            pris = client.priorities()
            out: List[Dict[str, Any]] = []
            for p in pris:
                out.append({
                    'id': getattr(p, 'id', ''),
                    'name': getattr(p, 'name', ''),
                    'statusColor': getattr(p, 'statusColor', None),
                })
            return out
        except Exception as e:
            logger.error(f"Failed to get priorities: {str(e)}")
            return []
    
    # Sprint Management (for Agile projects)
    
    def get_active_sprints(self, board_id: Optional[Union[str, int]]) -> List[Dict[str, Any]]:
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
            
            client = self.client
            assert client is not None
            # Normalize board id to int when possible, otherwise to str
            bid: Union[str, int]
            if board_id is None:
                bid = ''
            elif isinstance(board_id, int):
                bid = board_id
            else:
                bid = int(board_id) if str(board_id).isdigit() else str(board_id)
            sprints = client.sprints(bid, state='active')
            
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
    
    def add_issues_to_sprint(self, sprint_id: Optional[Union[str, int]], issue_keys: List[str]) -> Tuple[bool, str]:
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
            
            client = self.client
            assert client is not None
            # Convert to int if possible for API expectations
            sid_int: int
            # Normalize sprint id to int if possible; if not provided, raise
            if sprint_id is None:
                raise ValueError('Sprint ID is required')
            if isinstance(sprint_id, int):
                sid_int = sprint_id
            else:
                sid_int = int(sprint_id) if str(sprint_id).isdigit() else 0
            if sid_int == 0:
                raise ValueError(f'Invalid sprint id: {sprint_id}')
            client.add_issues_to_sprint(sid_int, issue_keys)
            logger.info(f"Successfully added {len(issue_keys)} issues to sprint {sprint_id}")
            return True, f"Added {len(issue_keys)} issues to sprint"
            
        except Exception as e:
            error_msg = f"Failed to add issues to sprint: {str(e)}"
            logger.error(error_msg)
            return False, error_msg

    def get_board_id_by_key(self, board_key: str) -> Optional[int]:
        """
        Get numeric board ID from board key/name.
        
        Args:
            board_key: Board key or name (e.g., "KAN")
            
        Returns:
            Numeric board ID if found, None otherwise
        """
        if not self._ensure_connected():
            return None
        
        try:
            # Try the REST API approach first
            url = f"{self.jira_url}/rest/agile/1.0/board"
            session = getattr(self.client, '_session', None)
            if session and hasattr(session, 'get'):
                response = session.get(url)
                if response.status_code == 200:
                    boards_data = response.json()
                    for board in boards_data.get('values', []):
                        if (board.get('name', '').upper() == str(board_key).upper() or 
                            board.get('key', '').upper() == str(board_key).upper()):
                            return int(board['id'])
            
            # Fallback to client method if available
            if self.client and hasattr(self.client, 'boards'):
                boards = self.client.boards()
                for board in boards:
                    if (hasattr(board, 'name') and str(board.name).upper() == str(board_key).upper()) or \
                       (hasattr(board, 'key') and str(board.key).upper() == str(board_key).upper()):
                        return int(board.id)
        except Exception as e:
            logger.warning(f"Could not retrieve board ID for '{board_key}': {str(e)}")
        
        return None

    def list_available_boards(self) -> List[Dict[str, Any]]:
        """
        Get list of available boards for debugging.
        
        Returns:
            List of board dictionaries with id, name, and key
        """
        if not self._ensure_connected():
            return []
        
        boards = []
        try:
            # Try REST API first
            url = f"{self.jira_url}/rest/agile/1.0/board"
            session = getattr(self.client, '_session', None)
            if session and hasattr(session, 'get'):
                response = session.get(url)
                if response.status_code == 200:
                    boards_data = response.json()
                    for board in boards_data.get('values', []):
                        boards.append({
                            'id': board.get('id'),
                            'name': board.get('name'),
                            'key': board.get('key', ''),
                            'type': board.get('type', '')
                        })
                    return boards
            
            # Fallback to client method
            if self.client and hasattr(self.client, 'boards'):
                jira_boards = self.client.boards()
                for board in jira_boards:
                    boards.append({
                        'id': getattr(board, 'id', ''),
                        'name': getattr(board, 'name', ''),
                        'key': getattr(board, 'key', ''),
                        'type': getattr(board, 'type', '')
                    })
        except Exception as e:
            logger.error(f"Error listing boards: {str(e)}")
        
        return boards

    def create_sprint(self, board_id: Optional[Union[str, int]], name: str, start_date: Optional[str] = None, end_date: Optional[str] = None, goal: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Create a new sprint for a board using direct REST API call.
        
        Args:
            board_id: Board ID (required)
            name: Sprint name (required)
            start_date: Sprint start date (ISO format, optional)
            end_date: Sprint end date (ISO format, optional)
            goal: Sprint goal (optional)
            
        Returns:
            Tuple of (success: bool, message: str, sprint_data: Dict)
        """
        if not self._ensure_connected():
            return False, "Not connected to Jira", {}
        
        try:
            self._rate_limit()
            
            # Convert board_id to integer if possible
            bid = None
            if board_id is not None:
                if isinstance(board_id, int):
                    bid = board_id
                elif str(board_id).isdigit():
                    bid = int(board_id)
                else:
                    # For non-numeric board IDs like "KAN", we need to get the numeric board ID
                    # Try to find the board by key/name using different API approaches
                    try:
                        if self.client:
                            # Method 1: Try using the agile API to get boards
                            try:
                                url = f"{self.jira_url}/rest/agile/1.0/board"
                                session = getattr(self.client, '_session', None)
                                if session and hasattr(session, 'get'):
                                    response = session.get(url)
                                    if response.status_code == 200:
                                        boards_data = response.json()
                                        for board in boards_data.get('values', []):
                                            board_name = board.get('name', '')
                                            board_key = board.get('key', '')
                                            
                                            # Check multiple matching patterns
                                            if (board_name.upper() == str(board_id).upper() or 
                                                board_key.upper() == str(board_id).upper() or
                                                board_name.upper().startswith(str(board_id).upper()) or
                                                str(board_id).upper() in board_name.upper()):
                                                bid = int(board['id'])
                                                logger.info(f"Resolved board '{board_id}' to ID: {bid} (matched with '{board_name}')")
                                                break
                            except Exception as e:
                                logger.debug(f"Method 1 failed: {e}")
                            
                            # Method 2: Try the client's boards method if available
                            if bid is None:
                                try:
                                    boards = self.client.boards() if hasattr(self.client, 'boards') else []
                                    for board in boards:
                                        board_name = str(getattr(board, 'name', ''))
                                        board_key = str(getattr(board, 'key', ''))
                                        
                                        # Check multiple matching patterns
                                        if (board_name.upper() == str(board_id).upper() or
                                            board_key.upper() == str(board_id).upper() or
                                            board_name.upper().startswith(str(board_id).upper()) or
                                            str(board_id).upper() in board_name.upper()):
                                            bid = int(board.id)
                                            logger.info(f"Resolved board '{board_id}' to ID: {bid} (via client, matched with '{board_name}')") 
                                            break
                                except Exception as e:
                                    logger.debug(f"Method 2 failed: {e}")
                            
                            # Method 3: Try searching for the board by name in projects
                            if bid is None:
                                try:
                                    projects = self.client.projects() if hasattr(self.client, 'projects') else []
                                    for project in projects:
                                        if (hasattr(project, 'key') and str(project.key).upper() == str(board_id).upper()) or \
                                           (hasattr(project, 'name') and str(project.name).upper() == str(board_id).upper()):
                                            # For project-based boards, often board ID = project ID or similar
                                            # This is a fallback - try the project key as board identifier
                                            logger.info(f"Found project '{project.key}' matching '{board_id}', trying as board identifier")
                                            # We'll use the project key for now and let Jira handle it
                                            bid = str(project.key)  # Keep as string for project-based boards
                                            break
                                except Exception as e:
                                    logger.debug(f"Method 3 failed: {e}")
                    except Exception as e:
                        logger.warning(f"Board resolution failed: {e}")
                    
                    if bid is None:
                        return False, f"Could not resolve board '{board_id}' to a board ID. Available resolution methods failed. Please check if the board exists and you have access to it.", {}
            
            if bid is None:
                return False, "Board ID is required", {}
            
            # Ensure board ID is numeric as required by Jira API
            try:
                numeric_board_id = int(bid)
            except (ValueError, TypeError):
                return False, f"Board ID '{bid}' must be numeric", {}

            # Check if the board supports sprints before attempting to create one
            try:
                board_details_url = f"{self.jira_url}/rest/agile/1.0/board/{numeric_board_id}"
                session = getattr(self.client, '_session')
                response = session.get(board_details_url)
                if response.status_code == 200:
                    board_data = response.json()
                    board_type = board_data.get('type')
                    board_name = board_data.get('name', f"Board {numeric_board_id}")
                    logger.info(f"Verifying board type for '{board_name}' (ID: {numeric_board_id}). Type: {board_type}")
                    if board_type != 'scrum':
                        error_msg = f"Board '{board_name}' is a '{board_type}' board and does not support sprints. Please select a Scrum board."
                        return False, error_msg, {}
                else:
                    logger.warning(f"Could not verify board type for board {numeric_board_id} (status: {response.status_code}). Proceeding with sprint creation attempt.")
            except Exception as e:
                logger.warning(f"An exception occurred while verifying board type for board {numeric_board_id}: {e}. Proceeding with sprint creation attempt.")
            
            # Create the exact payload Jira REST API expects
            payload = {
                "name": name,
                "originBoardId": numeric_board_id  # Must be numeric integer
            }
            
            # Add optional fields only if they have valid values
            if start_date and start_date.strip():
                # Ensure proper ISO 8601 format with timezone
                try:
                    from datetime import datetime
                    # Parse and reformat to ensure proper timezone
                    if 'T' not in start_date:
                        start_date = f"{start_date}T00:00:00.000Z"
                    elif not start_date.endswith('Z') and '+' not in start_date:
                        start_date = f"{start_date}.000Z" if '.' not in start_date else f"{start_date}Z"
                    payload["startDate"] = start_date
                except Exception as e:
                    logger.warning(f"Invalid start date format: {start_date}, error: {e}")
            
            if end_date and end_date.strip():
                # Ensure proper ISO 8601 format with timezone
                try:
                    if 'T' not in end_date:
                        end_date = f"{end_date}T23:59:59.999Z"
                    elif not end_date.endswith('Z') and '+' not in end_date:
                        end_date = f"{end_date}.999Z" if '.' not in end_date else f"{end_date}Z"
                    payload["endDate"] = end_date
                except Exception as e:
                    logger.warning(f"Invalid end date format: {end_date}, error: {e}")
            
            if goal and goal.strip():
                payload["goal"] = goal.strip()
            
            # Make direct REST API call using the authenticated session
            url = f"{self.jira_url}/rest/agile/1.0/sprint"
            
            logger.info(f"Creating sprint '{name}' on board ID {numeric_board_id} via REST API")
            logger.info(f"Sprint creation payload: {payload}")
            
            # Use the authenticated session from the JIRA client
            if not self.client:
                return False, "Client not connected", {}
                
            if not hasattr(self.client, '_session'):
                return False, "Client session not available", {}
                
            session = getattr(self.client, '_session', None)
            if not session or not hasattr(session, 'post'):
                return False, "Invalid client session", {}
                
            response = session.post(url, json=payload)
            
            logger.info(f"Sprint creation response status: {response.status_code}")
            logger.info(f"Sprint creation response headers: {dict(response.headers)}")
            logger.info(f"Sprint creation response body: {response.text}")
            
            if response.status_code in (200, 201):
                result = response.json()
                sprint_data = {
                    'id': result.get('id', ''),
                    'name': result.get('name', name),
                    'state': result.get('state', 'future'),
                    'startDate': result.get('startDate', ''),
                    'endDate': result.get('endDate', ''),
                    'goal': result.get('goal', ''),
                    'originBoardId': result.get('originBoardId', numeric_board_id)
                }
                
                logger.info(f"Successfully created sprint '{name}' (ID: {result.get('id', 'unknown')})")
                return True, f"Created sprint: {name}", sprint_data
            else:
                error_text = response.text
                logger.error(f"Sprint creation failed - Status: {response.status_code}")
                logger.error(f"Sprint creation failed - Request URL: {url}")
                logger.error(f"Sprint creation failed - Request payload: {payload}")
                logger.error(f"Sprint creation failed - Response: {error_text}")
                
                try:
                    error_data = response.json()
                    error_messages = error_data.get('errorMessages', [])
                    if error_messages:
                        error_text = '; '.join(error_messages)
                except:
                    pass
                
                error_msg = f"HTTP {response.status_code}: {error_text}"
                logger.error(f"Failed to create sprint: {error_msg}")
                return False, error_msg, {}
                
        except Exception as e:
            error_msg = f"Error creating sprint: {str(e)}"
            logger.error(error_msg)
            return False, error_msg, {}
    
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
                         fields: Optional[List[str]] = None,
                         start_at: int = 0) -> Dict[str, Any]:
        """
        Search issues using JQL (Jira Query Language).
        
        Args:
            jql: JQL query string
            max_results: Maximum number of results
            fields: Specific fields to retrieve
            
        Returns:
            Dict with issues list and total count { 'issues': [...], 'total': int }
        """
        if not self._ensure_connected():
            return { 'issues': [], 'total': 0 }
        
        try:
            self._rate_limit()
            
            # Set default fields if none specified
            if fields is None:
                # Include priority and issuetype so UI can render them in the table
                fields = ['summary', 'status', 'assignee', 'priority', 'issuetype', 'created', 'updated']
            
            client = self.client
            assert client is not None
            issues_result = client.search_issues(
                jql,
                maxResults=max_results,
                startAt=start_at,
                fields=fields,
                expand='changelog'
            )
            
            issue_list = []
            total_count = getattr(issues_result, 'total', None)
            for issue in issues_result:
                try:
                    issue_dict = self._convert_issue_to_dict(issue)
                    if issue_dict:
                        issue_list.append(issue_dict)
                except Exception as e:
                    logger.warning(f"Could not convert issue: {str(e)}")
                    continue
            
            logger.info(f"JQL search returned {len(issue_list)} issues (total={total_count})")
            return { 'issues': issue_list, 'total': int(total_count) if isinstance(total_count, int) else len(issue_list) }
            
        except Exception as e:
            logger.error(f"JQL search failed: {str(e)}")
            return { 'issues': [], 'total': 0 }

    def get_transitions(self, issue_key: str) -> List[Dict[str, Any]]:
        """Return available transitions for an issue."""
        if not self._ensure_connected():
            return []
        try:
            self._rate_limit()
            client = self.client
            assert client is not None
            issue = client.issue(issue_key)
            transitions = client.transitions(issue)
            out: List[Dict[str, Any]] = []
            for t in transitions:
                out.append({
                    'id': t.get('id'),
                    'name': t.get('name'),
                    'to': (t.get('to') or {}).get('name'),
                })
            return out
        except Exception as e:
            logger.error(f"Failed to get transitions for {issue_key}: {str(e)}")
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
            
            client = self.client
            assert client is not None
            issue = client.issue(issue_key, expand='changelog')
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
                client = self.client
                if client is not None:
                    client.close()
            except:
                pass
        self.client = None
        self.is_connected = False
        logger.info("Jira client connection closed")
