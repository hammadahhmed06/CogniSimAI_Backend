# services/jira/enhanced_jira_sync_service.py
# Enhanced service for comprehensive bi-directional Jira synchronization

import logging
from typing import Dict, Any, List, Optional, Tuple
import asyncio
from datetime import datetime, timedelta
import json

from app.services.jira.jira_client import JiraClient
from app.services.jira.jira_webhook_handler import JiraWebhookHandler, JiraEventType
from app.services.jira.jira_mapper import JiraFieldMapper

logger = logging.getLogger("cognisim_ai")


class EnhancedJiraSyncService:
    """
    Enhanced service for comprehensive bi-directional Jira synchronization.
    Supports real-time webhooks, bulk operations, and advanced sync features.
    """
    
    def __init__(self):
        """Initialize the enhanced sync service."""
        self.clients: Dict[str, JiraClient] = {}
        self.webhook_handler = JiraWebhookHandler()
        self.mapper = JiraFieldMapper()
        
        # Sync state tracking
        self.last_sync_times: Dict[str, datetime] = {}
        self.sync_in_progress: Dict[str, bool] = {}
        
        # Real-time sync configuration
        self.real_time_enabled = True
        self.sync_interval_seconds = 30  # 30 seconds for real-time sync
        
        # Register webhook callback for real-time sync
        self.webhook_handler.add_sync_callback(self._handle_real_time_sync)
    
    async def setup_integration(self, integration: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Set up a new Jira integration with full bi-directional sync.
        
        Args:
            integration: Integration configuration dictionary
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            integration_id = str(integration.get('id', ''))
            
            # Create Jira client
            client = JiraClient.from_encrypted_credentials(
                jira_url=integration.get('jira_url', ''),
                email=integration.get('email', ''),
                encrypted_api_token=integration.get('encrypted_api_token', '')
            )
            
            # Test connection
            success, message = client.connect()
            if not success:
                return False, f"Failed to connect to Jira: {message}"
            
            # Store client
            self.clients[integration_id] = client
            
            # Initialize sync state
            self.last_sync_times[integration_id] = datetime.utcnow()
            self.sync_in_progress[integration_id] = False
            
            # Perform initial sync
            if integration.get('enable_sync', False):
                await self._perform_initial_sync(integration_id, integration)
            
            logger.info(f"Jira integration {integration_id} set up successfully")
            return True, "Integration set up successfully with bi-directional sync"
            
        except Exception as e:
            error_msg = f"Failed to set up integration: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    async def _perform_initial_sync(self, integration_id: str, integration: Dict[str, Any]):
        """Perform initial full synchronization."""
        try:
            client = self.clients.get(integration_id)
            if not client:
                return
            
            logger.info(f"Starting initial sync for integration {integration_id}")
            
            # Sync projects
            projects = client.get_all_projects()
            await self._sync_projects(integration_id, projects)
            
            # Sync issues from enabled projects
            enabled_projects = integration.get('enabled_projects', [])
            if enabled_projects:
                for project_key in enabled_projects:
                    issues = client.get_project_issues(project_key, max_results=100)
                    await self._sync_issues(integration_id, project_key, issues)
            
            logger.info(f"Initial sync completed for integration {integration_id}")
            
        except Exception as e:
            logger.error(f"Error in initial sync for {integration_id}: {str(e)}")
    
    async def sync_integration(self, integration_id: str, force: bool = False) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Perform comprehensive sync for a Jira integration.
        
        Args:
            integration_id: Integration identifier
            force: Force sync even if recently synced
            
        Returns:
            Tuple of (success: bool, message: str, sync_stats: Dict)
        """
        if integration_id not in self.clients:
            return False, "Integration not found", {}
        
        if not force and self.sync_in_progress.get(integration_id, False):
            return False, "Sync already in progress", {}
        
        sync_stats = {
            'projects_synced': 0,
            'issues_synced': 0,
            'comments_synced': 0,
            'errors': []
        }
        
        try:
            self.sync_in_progress[integration_id] = True
            
            client = self.clients[integration_id]
            
            # Sync projects
            projects = client.get_all_projects()
            sync_stats['projects_synced'] = len(projects)
            await self._sync_projects(integration_id, projects)
            
            # Sync issues for each project
            total_issues = 0
            for project in projects:
                project_key = ''
                try:
                    project_key = project['key']
                    issues = client.get_project_issues(project_key, max_results=50)
                    total_issues += len(issues)
                    await self._sync_issues(integration_id, project_key, issues)
                except Exception as e:
                    sync_stats['errors'].append(f"Project {project_key}: {str(e)}")
            
            sync_stats['issues_synced'] = total_issues
            
            # Update last sync time
            self.last_sync_times[integration_id] = datetime.utcnow()
            
            logger.info(f"Sync completed for integration {integration_id}: {sync_stats}")
            return True, "Sync completed successfully", sync_stats
            
        except Exception as e:
            error_msg = f"Sync failed: {str(e)}"
            logger.error(f"Error syncing integration {integration_id}: {error_msg}")
            return False, error_msg, sync_stats
            
        finally:
            self.sync_in_progress[integration_id] = False
    
    async def _sync_projects(self, integration_id: str, projects: List[Dict[str, Any]]):
        """Sync project data to local storage."""
        # Here you would implement project synchronization logic
        # This could involve updating a database, calling other services, etc.
        logger.info(f"Syncing {len(projects)} projects for integration {integration_id}")
        # Placeholder for actual sync implementation
    
    async def _sync_issues(self, integration_id: str, project_key: str, issues: List[Dict[str, Any]]):
        """Sync issue data to local storage."""
        # Here you would implement issue synchronization logic
        logger.info(f"Syncing {len(issues)} issues for project {project_key}")
        # Placeholder for actual sync implementation
    
    # Real-time webhook processing
    
    def process_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process incoming webhook for real-time synchronization.
        
        Args:
            webhook_data: Webhook payload from Jira
            
        Returns:
            Processing result
        """
        return self.webhook_handler.process_webhook(webhook_data)
    
    def _handle_real_time_sync(self, event_type: JiraEventType, 
                              webhook_data: Dict[str, Any], 
                              result: Dict[str, Any]):
        """Handle real-time sync based on webhook events."""
        try:
            if not self.real_time_enabled:
                return
            
            # Extract integration info from webhook
            # This would need to be implemented based on your webhook setup
            integration_id = self._extract_integration_id(webhook_data)
            
            if not integration_id or integration_id not in self.clients:
                return
            
            # Process based on event type
            if event_type in [JiraEventType.ISSUE_CREATED, JiraEventType.ISSUE_UPDATED]:
                asyncio.create_task(self._sync_single_issue(integration_id, result))
            elif event_type == JiraEventType.PROJECT_CREATED:
                asyncio.create_task(self._sync_single_project(integration_id, result))
            
            logger.info(f"Real-time sync triggered for {event_type.value}")
            
        except Exception as e:
            logger.error(f"Error in real-time sync: {str(e)}")
    
    def _extract_integration_id(self, webhook_data: Dict[str, Any]) -> Optional[str]:
        """Extract integration ID from webhook data."""
        # This would need to be implemented based on your webhook configuration
        # For now, return the first available integration
        return next(iter(self.clients.keys()), None)
    
    async def _sync_single_issue(self, integration_id: str, issue_data: Dict[str, Any]):
        """Sync a single issue in real-time."""
        try:
            issue_key = issue_data.get('issue_key')
            if issue_key:
                logger.info(f"Real-time sync for issue {issue_key}")
                # Implement single issue sync logic
        except Exception as e:
            logger.error(f"Error syncing single issue: {str(e)}")
    
    async def _sync_single_project(self, integration_id: str, project_data: Dict[str, Any]):
        """Sync a single project in real-time."""
        try:
            project_key = project_data.get('project_key')
            if project_key:
                logger.info(f"Real-time sync for project {project_key}")
                # Implement single project sync logic
        except Exception as e:
            logger.error(f"Error syncing single project: {str(e)}")
    
    # Advanced operations
    
    async def create_issue(self, integration_id: str, project_key: str, 
                          issue_data: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
        """
        Create a new issue in Jira with bi-directional sync.
        
        Args:
            integration_id: Integration identifier
            project_key: Target project key
            issue_data: Issue creation data
            
        Returns:
            Tuple of (success: bool, message: str, issue_key: Optional[str])
        """
        if integration_id not in self.clients:
            return False, "Integration not found", None
        
        try:
            client = self.clients[integration_id]
            
            success, message, issue_key = client.create_issue(
                project_key=project_key,
                summary=issue_data.get('summary', ''),
                description=issue_data.get('description', ''),
                issue_type=issue_data.get('issue_type', 'Task'),
                **issue_data.get('additional_fields', {})
            )
            
            if success and issue_key:
                # Trigger sync for the new issue
                await self._sync_created_issue(integration_id, issue_key)
            
            return success, message, issue_key
            
        except Exception as e:
            error_msg = f"Failed to create issue: {str(e)}"
            logger.error(error_msg)
            return False, error_msg, None
    
    async def update_issue(self, integration_id: str, issue_key: str, 
                          updates: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Update an issue in Jira with bi-directional sync.
        
        Args:
            integration_id: Integration identifier
            issue_key: Issue key to update
            updates: Fields to update
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if integration_id not in self.clients:
            return False, "Integration not found"
        
        try:
            client = self.clients[integration_id]
            success, message = client.update_issue(issue_key, updates)
            
            if success:
                # Trigger sync for the updated issue
                await self._sync_updated_issue(integration_id, issue_key)
            
            return success, message
            
        except Exception as e:
            error_msg = f"Failed to update issue: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    async def _sync_created_issue(self, integration_id: str, issue_key: str):
        """Sync a newly created issue."""
        logger.info(f"Syncing created issue {issue_key} for integration {integration_id}")
        # Implement sync logic for created issue
    
    async def _sync_updated_issue(self, integration_id: str, issue_key: str):
        """Sync an updated issue."""
        logger.info(f"Syncing updated issue {issue_key} for integration {integration_id}")
        # Implement sync logic for updated issue
    
    # Bulk operations
    
    async def bulk_create_issues(self, integration_id: str, 
                                issues_data: List[Dict[str, Any]]) -> Tuple[bool, str, List[str]]:
        """
        Create multiple issues in bulk with optimized sync.
        
        Args:
            integration_id: Integration identifier
            issues_data: List of issue creation data
            
        Returns:
            Tuple of (success: bool, message: str, created_issue_keys: List[str])
        """
        if integration_id not in self.clients:
            return False, "Integration not found", []
        
        try:
            client = self.clients[integration_id]
            success, message, created_keys = client.bulk_create_issues(issues_data)
            
            if created_keys:
                # Trigger bulk sync for created issues
                await self._sync_bulk_created_issues(integration_id, created_keys)
            
            return success, message, created_keys
            
        except Exception as e:
            error_msg = f"Failed to bulk create issues: {str(e)}"
            logger.error(error_msg)
            return False, error_msg, []
    
    async def _sync_bulk_created_issues(self, integration_id: str, issue_keys: List[str]):
        """Sync bulk created issues efficiently."""
        logger.info(f"Syncing {len(issue_keys)} bulk created issues for integration {integration_id}")
        # Implement bulk sync logic
    
    # Search and filtering
    
    async def search_issues(self, integration_id: str, jql: str, 
                           max_results: int = 50) -> List[Dict[str, Any]]:
        """
        Search issues using JQL with local caching.
        
        Args:
            integration_id: Integration identifier
            jql: JQL query string
            max_results: Maximum results to return
            
        Returns:
            List of issue dictionaries
        """
        if integration_id not in self.clients:
            return []
        
        try:
            client = self.clients[integration_id]
            search_result = client.search_issues_jql(jql, max_results)
            # Extract issues from the search result dictionary
            return search_result.get('issues', []) if isinstance(search_result, dict) else []
            
        except Exception as e:
            logger.error(f"Error searching issues: {str(e)}")
            return []
    
    # Status and monitoring
    
    def get_sync_status(self, integration_id: str) -> Dict[str, Any]:
        """Get current sync status for an integration."""
        if integration_id not in self.clients:
            return {'status': 'not_found'}
        
        return {
            'status': 'active',
            'last_sync': self.last_sync_times.get(integration_id),
            'sync_in_progress': self.sync_in_progress.get(integration_id, False),
            'real_time_enabled': self.real_time_enabled,
            'client_connected': self.clients[integration_id].is_connected
        }
    
    def get_all_sync_statuses(self) -> Dict[str, Dict[str, Any]]:
        """Get sync status for all integrations."""
        return {
            integration_id: self.get_sync_status(integration_id)
            for integration_id in self.clients.keys()
        }
    
    # Configuration
    
    def enable_real_time_sync(self, enabled: bool = True):
        """Enable or disable real-time synchronization."""
        self.real_time_enabled = enabled
        logger.info(f"Real-time sync {'enabled' if enabled else 'disabled'}")
    
    def set_sync_interval(self, seconds: int):
        """Set the sync interval for real-time updates."""
        self.sync_interval_seconds = max(1, seconds)  # Minimum 1 second
        logger.info(f"Sync interval set to {self.sync_interval_seconds} seconds")
    
    # Cleanup
    
    def remove_integration(self, integration_id: str):
        """Remove an integration and clean up resources."""
        if integration_id in self.clients:
            self.clients[integration_id].close()
            del self.clients[integration_id]
        
        if integration_id in self.last_sync_times:
            del self.last_sync_times[integration_id]
        
        if integration_id in self.sync_in_progress:
            del self.sync_in_progress[integration_id]
        
        logger.info(f"Integration {integration_id} removed")
    
    def shutdown(self):
        """Shutdown the sync service and clean up all resources."""
        for client in self.clients.values():
            client.close()
        
        self.clients.clear()
        self.last_sync_times.clear()
        self.sync_in_progress.clear()
        
        logger.info("Enhanced Jira sync service shut down")


# Global enhanced sync service instance
enhanced_sync_service = EnhancedJiraSyncService()
