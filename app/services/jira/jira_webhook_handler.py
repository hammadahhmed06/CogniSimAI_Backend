# services/jira/jira_webhook_handler.py
# Webhook handler for real-time Jira event processing

import logging
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
import json
from enum import Enum

from app.services.jira.jira_client import JiraClient
from app.services.encryption.token_encryption import get_token_encryption_service

logger = logging.getLogger("cognisim_ai")


class JiraEventType(Enum):
    """Enumeration of Jira webhook event types."""
    ISSUE_CREATED = "jira:issue_created"
    ISSUE_UPDATED = "jira:issue_updated"
    ISSUE_DELETED = "jira:issue_deleted"
    COMMENT_CREATED = "comment_created"
    COMMENT_UPDATED = "comment_updated"
    COMMENT_DELETED = "comment_deleted"
    WORKLOG_CREATED = "worklog_created"
    WORKLOG_UPDATED = "worklog_updated"
    WORKLOG_DELETED = "worklog_deleted"
    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    PROJECT_DELETED = "project_deleted"
    SPRINT_CREATED = "sprint_created"
    SPRINT_UPDATED = "sprint_updated"
    SPRINT_CLOSED = "sprint_closed"
    SPRINT_STARTED = "sprint_started"


class JiraWebhookHandler:
    """
    Handler for processing Jira webhook events in real-time.
    Provides bi-directional synchronization capabilities.
    """
    
    def __init__(self):
        """Initialize the webhook handler."""
        self.event_handlers = {
            JiraEventType.ISSUE_CREATED: self._handle_issue_created,
            JiraEventType.ISSUE_UPDATED: self._handle_issue_updated,
            JiraEventType.ISSUE_DELETED: self._handle_issue_deleted,
            JiraEventType.COMMENT_CREATED: self._handle_comment_created,
            JiraEventType.COMMENT_UPDATED: self._handle_comment_updated,
            JiraEventType.COMMENT_DELETED: self._handle_comment_deleted,
            JiraEventType.WORKLOG_CREATED: self._handle_worklog_created,
            JiraEventType.WORKLOG_UPDATED: self._handle_worklog_updated,
            JiraEventType.WORKLOG_DELETED: self._handle_worklog_deleted,
            JiraEventType.PROJECT_CREATED: self._handle_project_created,
            JiraEventType.PROJECT_UPDATED: self._handle_project_updated,
            JiraEventType.PROJECT_DELETED: self._handle_project_deleted,
            JiraEventType.SPRINT_CREATED: self._handle_sprint_created,
            JiraEventType.SPRINT_UPDATED: self._handle_sprint_updated,
            JiraEventType.SPRINT_CLOSED: self._handle_sprint_closed,
            JiraEventType.SPRINT_STARTED: self._handle_sprint_started,
        }
        
        # Store for real-time sync callbacks
        self.sync_callbacks: List[Callable] = []
    
    def process_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process incoming Jira webhook data.
        
        Args:
            webhook_data: Raw webhook payload from Jira
            
        Returns:
            Processing result dictionary
        """
        try:
            # Extract event type
            webhook_event = webhook_data.get('webhookEvent', '')
            
            # Map webhook event to our enum
            event_type = self._map_webhook_event(webhook_event)
            
            if not event_type:
                logger.warning(f"Unhandled webhook event type: {webhook_event}")
                return {
                    'success': False,
                    'message': f'Unhandled event type: {webhook_event}',
                    'event_type': webhook_event,
                    'timestamp': datetime.utcnow().isoformat()
                }
            
            # Get appropriate handler
            handler = self.event_handlers.get(event_type)
            if not handler:
                logger.warning(f"No handler for event type: {event_type}")
                return {
                    'success': False,
                    'message': f'No handler for event type: {event_type.value}',
                    'event_type': event_type.value,
                    'timestamp': datetime.utcnow().isoformat()
                }
            
            # Process the event
            result = handler(webhook_data)
            
            # Trigger sync callbacks for real-time updates
            self._trigger_sync_callbacks(event_type, webhook_data, result)
            
            return {
                'success': True,
                'message': 'Webhook processed successfully',
                'event_type': event_type.value,
                'result': result,
                'timestamp': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error processing webhook: {str(e)}")
            return {
                'success': False,
                'message': f'Error processing webhook: {str(e)}',
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def _map_webhook_event(self, webhook_event: str) -> Optional[JiraEventType]:
        """Map Jira webhook event strings to our enum values."""
        event_mapping = {
            'jira:issue_created': JiraEventType.ISSUE_CREATED,
            'jira:issue_updated': JiraEventType.ISSUE_UPDATED,
            'jira:issue_deleted': JiraEventType.ISSUE_DELETED,
            'comment_created': JiraEventType.COMMENT_CREATED,
            'comment_updated': JiraEventType.COMMENT_UPDATED,
            'comment_deleted': JiraEventType.COMMENT_DELETED,
            'worklog_created': JiraEventType.WORKLOG_CREATED,
            'worklog_updated': JiraEventType.WORKLOG_UPDATED,
            'worklog_deleted': JiraEventType.WORKLOG_DELETED,
            'project_created': JiraEventType.PROJECT_CREATED,
            'project_updated': JiraEventType.PROJECT_UPDATED,
            'project_deleted': JiraEventType.PROJECT_DELETED,
            'sprint_created': JiraEventType.SPRINT_CREATED,
            'sprint_updated': JiraEventType.SPRINT_UPDATED,
            'sprint_closed': JiraEventType.SPRINT_CLOSED,
            'sprint_started': JiraEventType.SPRINT_STARTED,
        }
        return event_mapping.get(webhook_event)
    
    # Event Handlers
    
    def _handle_issue_created(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle issue creation events."""
        try:
            issue_data = webhook_data.get('issue', {})
            issue_key = issue_data.get('key', '')
            project_key = issue_data.get('fields', {}).get('project', {}).get('key', '')
            
            logger.info(f"Issue created: {issue_key} in project {project_key}")
            
            return {
                'action': 'issue_created',
                'issue_key': issue_key,
                'project_key': project_key,
                'summary': issue_data.get('fields', {}).get('summary', ''),
                'status': issue_data.get('fields', {}).get('status', {}).get('name', ''),
                'assignee': self._extract_user_info(issue_data.get('fields', {}).get('assignee')),
                'reporter': self._extract_user_info(issue_data.get('fields', {}).get('reporter')),
                'issue_type': issue_data.get('fields', {}).get('issuetype', {}).get('name', ''),
                'priority': issue_data.get('fields', {}).get('priority', {}).get('name', ''),
                'created': issue_data.get('fields', {}).get('created', ''),
            }
            
        except Exception as e:
            logger.error(f"Error handling issue creation: {str(e)}")
            return {'error': str(e)}
    
    def _handle_issue_updated(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle issue update events."""
        try:
            issue_data = webhook_data.get('issue', {})
            changelog = webhook_data.get('changelog', {})
            
            issue_key = issue_data.get('key', '')
            changes = self._extract_changelog(changelog)
            
            logger.info(f"Issue updated: {issue_key} with {len(changes)} changes")
            
            return {
                'action': 'issue_updated',
                'issue_key': issue_key,
                'changes': changes,
                'updated': issue_data.get('fields', {}).get('updated', ''),
                'current_status': issue_data.get('fields', {}).get('status', {}).get('name', ''),
                'current_assignee': self._extract_user_info(issue_data.get('fields', {}).get('assignee')),
            }
            
        except Exception as e:
            logger.error(f"Error handling issue update: {str(e)}")
            return {'error': str(e)}
    
    def _handle_issue_deleted(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle issue deletion events."""
        try:
            issue_data = webhook_data.get('issue', {})
            issue_key = issue_data.get('key', '')
            
            logger.info(f"Issue deleted: {issue_key}")
            
            return {
                'action': 'issue_deleted',
                'issue_key': issue_key,
                'deleted_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling issue deletion: {str(e)}")
            return {'error': str(e)}
    
    def _handle_comment_created(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle comment creation events."""
        try:
            issue_data = webhook_data.get('issue', {})
            comment_data = webhook_data.get('comment', {})
            
            issue_key = issue_data.get('key', '')
            comment_id = comment_data.get('id', '')
            
            logger.info(f"Comment created on issue {issue_key}: {comment_id}")
            
            return {
                'action': 'comment_created',
                'issue_key': issue_key,
                'comment_id': comment_id,
                'author': self._extract_user_info(comment_data.get('author')),
                'body': comment_data.get('body', ''),
                'created': comment_data.get('created', ''),
            }
            
        except Exception as e:
            logger.error(f"Error handling comment creation: {str(e)}")
            return {'error': str(e)}
    
    def _handle_comment_updated(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle comment update events."""
        try:
            issue_data = webhook_data.get('issue', {})
            comment_data = webhook_data.get('comment', {})
            
            issue_key = issue_data.get('key', '')
            comment_id = comment_data.get('id', '')
            
            logger.info(f"Comment updated on issue {issue_key}: {comment_id}")
            
            return {
                'action': 'comment_updated',
                'issue_key': issue_key,
                'comment_id': comment_id,
                'author': self._extract_user_info(comment_data.get('author')),
                'updated_author': self._extract_user_info(comment_data.get('updateAuthor')),
                'body': comment_data.get('body', ''),
                'updated': comment_data.get('updated', ''),
            }
            
        except Exception as e:
            logger.error(f"Error handling comment update: {str(e)}")
            return {'error': str(e)}
    
    def _handle_comment_deleted(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle comment deletion events."""
        try:
            issue_data = webhook_data.get('issue', {})
            comment_data = webhook_data.get('comment', {})
            
            issue_key = issue_data.get('key', '')
            comment_id = comment_data.get('id', '')
            
            logger.info(f"Comment deleted from issue {issue_key}: {comment_id}")
            
            return {
                'action': 'comment_deleted',
                'issue_key': issue_key,
                'comment_id': comment_id,
                'deleted_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling comment deletion: {str(e)}")
            return {'error': str(e)}
    
    def _handle_worklog_created(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle worklog creation events."""
        try:
            issue_data = webhook_data.get('issue', {})
            worklog_data = webhook_data.get('worklog', {})
            
            issue_key = issue_data.get('key', '')
            worklog_id = worklog_data.get('id', '')
            
            logger.info(f"Worklog created for issue {issue_key}: {worklog_id}")
            
            return {
                'action': 'worklog_created',
                'issue_key': issue_key,
                'worklog_id': worklog_id,
                'author': self._extract_user_info(worklog_data.get('author')),
                'time_spent': worklog_data.get('timeSpent', ''),
                'time_spent_seconds': worklog_data.get('timeSpentSeconds', 0),
                'started': worklog_data.get('started', ''),
                'comment': worklog_data.get('comment', ''),
            }
            
        except Exception as e:
            logger.error(f"Error handling worklog creation: {str(e)}")
            return {'error': str(e)}
    
    def _handle_worklog_updated(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle worklog update events."""
        try:
            issue_data = webhook_data.get('issue', {})
            worklog_data = webhook_data.get('worklog', {})
            
            issue_key = issue_data.get('key', '')
            worklog_id = worklog_data.get('id', '')
            
            logger.info(f"Worklog updated for issue {issue_key}: {worklog_id}")
            
            return {
                'action': 'worklog_updated',
                'issue_key': issue_key,
                'worklog_id': worklog_id,
                'update_author': self._extract_user_info(worklog_data.get('updateAuthor')),
                'time_spent': worklog_data.get('timeSpent', ''),
                'time_spent_seconds': worklog_data.get('timeSpentSeconds', 0),
                'updated': worklog_data.get('updated', ''),
            }
            
        except Exception as e:
            logger.error(f"Error handling worklog update: {str(e)}")
            return {'error': str(e)}
    
    def _handle_worklog_deleted(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle worklog deletion events."""
        try:
            issue_data = webhook_data.get('issue', {})
            worklog_data = webhook_data.get('worklog', {})
            
            issue_key = issue_data.get('key', '')
            worklog_id = worklog_data.get('id', '')
            
            logger.info(f"Worklog deleted from issue {issue_key}: {worklog_id}")
            
            return {
                'action': 'worklog_deleted',
                'issue_key': issue_key,
                'worklog_id': worklog_id,
                'deleted_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling worklog deletion: {str(e)}")
            return {'error': str(e)}
    
    def _handle_project_created(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle project creation events."""
        try:
            project_data = webhook_data.get('project', {})
            project_key = project_data.get('key', '')
            
            logger.info(f"Project created: {project_key}")
            
            return {
                'action': 'project_created',
                'project_key': project_key,
                'project_name': project_data.get('name', ''),
                'project_type': project_data.get('projectTypeKey', ''),
                'lead': self._extract_user_info(project_data.get('lead')),
            }
            
        except Exception as e:
            logger.error(f"Error handling project creation: {str(e)}")
            return {'error': str(e)}
    
    def _handle_project_updated(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle project update events."""
        try:
            project_data = webhook_data.get('project', {})
            project_key = project_data.get('key', '')
            
            logger.info(f"Project updated: {project_key}")
            
            return {
                'action': 'project_updated',
                'project_key': project_key,
                'project_name': project_data.get('name', ''),
                'updated_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling project update: {str(e)}")
            return {'error': str(e)}
    
    def _handle_project_deleted(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle project deletion events."""
        try:
            project_data = webhook_data.get('project', {})
            project_key = project_data.get('key', '')
            
            logger.info(f"Project deleted: {project_key}")
            
            return {
                'action': 'project_deleted',
                'project_key': project_key,
                'deleted_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling project deletion: {str(e)}")
            return {'error': str(e)}
    
    def _handle_sprint_created(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle sprint creation events."""
        try:
            sprint_data = webhook_data.get('sprint', {})
            sprint_id = sprint_data.get('id', '')
            
            logger.info(f"Sprint created: {sprint_id}")
            
            return {
                'action': 'sprint_created',
                'sprint_id': sprint_id,
                'sprint_name': sprint_data.get('name', ''),
                'state': sprint_data.get('state', ''),
                'start_date': sprint_data.get('startDate', ''),
                'end_date': sprint_data.get('endDate', ''),
            }
            
        except Exception as e:
            logger.error(f"Error handling sprint creation: {str(e)}")
            return {'error': str(e)}
    
    def _handle_sprint_updated(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle sprint update events."""
        try:
            sprint_data = webhook_data.get('sprint', {})
            sprint_id = sprint_data.get('id', '')
            
            logger.info(f"Sprint updated: {sprint_id}")
            
            return {
                'action': 'sprint_updated',
                'sprint_id': sprint_id,
                'sprint_name': sprint_data.get('name', ''),
                'state': sprint_data.get('state', ''),
                'updated_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling sprint update: {str(e)}")
            return {'error': str(e)}
    
    def _handle_sprint_closed(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle sprint closure events."""
        try:
            sprint_data = webhook_data.get('sprint', {})
            sprint_id = sprint_data.get('id', '')
            
            logger.info(f"Sprint closed: {sprint_id}")
            
            return {
                'action': 'sprint_closed',
                'sprint_id': sprint_id,
                'sprint_name': sprint_data.get('name', ''),
                'closed_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling sprint closure: {str(e)}")
            return {'error': str(e)}
    
    def _handle_sprint_started(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle sprint start events."""
        try:
            sprint_data = webhook_data.get('sprint', {})
            sprint_id = sprint_data.get('id', '')
            
            logger.info(f"Sprint started: {sprint_id}")
            
            return {
                'action': 'sprint_started',
                'sprint_id': sprint_id,
                'sprint_name': sprint_data.get('name', ''),
                'started_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error handling sprint start: {str(e)}")
            return {'error': str(e)}
    
    # Helper Methods
    
    def _extract_user_info(self, user_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Extract user information from Jira user object."""
        if not user_data:
            return None
        
        return {
            'account_id': user_data.get('accountId', ''),
            'display_name': user_data.get('displayName', ''),
            'email': user_data.get('emailAddress', ''),
            'active': user_data.get('active', True)
        }
    
    def _extract_changelog(self, changelog: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract and format changelog items."""
        items = changelog.get('items', [])
        
        changes = []
        for item in items:
            change = {
                'field': item.get('field', ''),
                'field_type': item.get('fieldtype', ''),
                'from_value': item.get('fromString', ''),
                'to_value': item.get('toString', ''),
                'from_id': item.get('from', ''),
                'to_id': item.get('to', '')
            }
            changes.append(change)
        
        return changes
    
    def add_sync_callback(self, callback: Callable):
        """Add a callback function for real-time synchronization."""
        self.sync_callbacks.append(callback)
    
    def remove_sync_callback(self, callback: Callable):
        """Remove a sync callback function."""
        if callback in self.sync_callbacks:
            self.sync_callbacks.remove(callback)
    
    def _trigger_sync_callbacks(self, event_type: JiraEventType, 
                               webhook_data: Dict[str, Any], 
                               result: Dict[str, Any]):
        """Trigger all registered sync callbacks."""
        for callback in self.sync_callbacks:
            try:
                callback(event_type, webhook_data, result)
            except Exception as e:
                logger.error(f"Error in sync callback: {str(e)}")
    
    def validate_webhook_signature(self, payload: str, signature: str, secret: str) -> bool:
        """
        Validate webhook signature for security.
        
        Args:
            payload: Raw webhook payload
            signature: Signature from webhook headers
            secret: Shared secret for validation
            
        Returns:
            True if signature is valid, False otherwise
        """
        import hmac
        import hashlib
        
        try:
            # Create expected signature
            expected_signature = hmac.new(
                secret.encode('utf-8'),
                payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Compare signatures
            return hmac.compare_digest(signature, expected_signature)
            
        except Exception as e:
            logger.error(f"Error validating webhook signature: {str(e)}")
            return False


# Global webhook handler instance
webhook_handler = JiraWebhookHandler()
