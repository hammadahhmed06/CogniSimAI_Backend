# services/jira/jira_sync_service.py
# Simplified Jira integration service

import logging
from typing import Dict, Any, Optional, List
from uuid import uuid4
from datetime import datetime
from supabase import Client

from app.services.jira.jira_client import JiraClient
from app.services.jira.jira_mapper import JiraFieldMapper
from app.services.encryption.simple_credential_store import simple_credential_store
from app.services.encryption.token_encryption import get_token_encryption_service
from app.models.integration_models import ConnectionStatus, SyncStatus

logger = logging.getLogger("cognisim_ai")


class JiraSyncService:
    """
    Simplified Jira integration service for CogniSim.
    Handles credentials, connections, and synchronization.
    """
    
    def __init__(self, supabase_client: Client):
        """
        Initialize Jira sync service.
        
        Args:
            supabase_client: Supabase client instance
        """
        self.supabase = supabase_client
        self.field_mapper = JiraFieldMapper()
    
    async def save_and_test_credentials(
        self,
        workspace_id: str,
        jira_url: str,
        jira_email: str,
        jira_api_token: str
    ) -> Dict[str, Any]:
        """
        Save Jira credentials and test connection.
        
        Args:
            workspace_id: Workspace ID
            jira_url: Jira instance URL
            jira_email: Jira user email
            jira_api_token: Jira API token
            
        Returns:
            Connection result
        """
        try:
            logger.info(f"Testing Jira connection for workspace {workspace_id}")
            
            # Test connection first
            jira_client = JiraClient(jira_url, jira_email, jira_api_token)
            connection_success, connection_message = jira_client.connect()
            
            if not connection_success:
                return {
                    'success': False,
                    'message': connection_message,
                    'connection_status': ConnectionStatus.FAILED
                }
            
            # Encrypt the API token for storage
            encryption_service = get_token_encryption_service()
            encrypted_token = encryption_service.encrypt(jira_api_token)
            
            # Check if credentials already exist for this workspace
            existing_creds = self.supabase.table("integration_credentials").select("*").eq("workspace_id", workspace_id).eq("integration_type", "jira").execute()
            
            credential_data = {
                'workspace_id': workspace_id,
                'integration_type': 'jira',
                'jira_url': jira_url,
                'jira_email': jira_email,
                'jira_api_token_encrypted': encrypted_token,
                'is_active': True,
                'connection_status': ConnectionStatus.CONNECTED.value,
                'last_tested_at': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat()
            }
            
            if existing_creds.data:
                # Update existing credentials
                result = self.supabase.table("integration_credentials").update(credential_data).eq("workspace_id", workspace_id).eq("integration_type", "jira").execute()
                integration_id = existing_creds.data[0]['id']
            else:
                # Create new credentials
                credential_data['id'] = str(uuid4())
                credential_data['created_at'] = datetime.utcnow().isoformat()
                result = self.supabase.table("integration_credentials").insert(credential_data).execute()
                integration_id = credential_data['id']
            
            jira_client.close()
            
            logger.info(f"Jira credentials saved successfully for workspace {workspace_id}")
            
            return {
                'success': True,
                'message': connection_message,
                'connection_status': ConnectionStatus.CONNECTED,
                'integration_id': integration_id
            }
            
        except Exception as e:
            logger.error(f"Failed to save Jira credentials: {str(e)}")
            return {
                'success': False,
                'message': f"Failed to save credentials: {str(e)}",
                'connection_status': ConnectionStatus.FAILED
            }

    async def get_integration_status(self, workspace_id: str) -> Dict[str, Any]:
        """
        Get the current status of Jira integration for the workspace.
        
        Args:
            workspace_id: Workspace ID
            
        Returns:
            Integration status information
        """
        try:
            # Get credentials from database
            credentials = self.supabase.table("integration_credentials").select("*").eq("workspace_id", workspace_id).eq("integration_type", "jira").eq("is_active", True).execute()
            
            if not credentials.data:
                return {
                    'is_connected': False,
                    'connection_status': ConnectionStatus.DISCONNECTED,
                    'integration_type': 'jira',
                    'jira_url': None,
                    'jira_email': None,
                    'last_tested_at': None,
                    'last_sync_at': None
                }
            
            cred = credentials.data[0]
            
            # Get last sync information
            last_sync = self.supabase.table("sync_logs").select("completed_at").eq("workspace_id", workspace_id).eq("integration_type", "jira").order("completed_at", desc=True).limit(1).execute()
            
            last_sync_at = None
            if last_sync.data:
                last_sync_at = last_sync.data[0]['completed_at']
            
            return {
                'is_connected': cred['connection_status'] == ConnectionStatus.CONNECTED.value,
                'connection_status': cred['connection_status'],
                'integration_type': 'jira',
                'jira_url': cred['jira_url'],
                'jira_email': cred['jira_email'],
                'last_tested_at': cred['last_tested_at'],
                'last_sync_at': last_sync_at
            }
            
        except Exception as e:
            logger.error(f"Failed to get integration status: {str(e)}")
            return {
                'is_connected': False,
                'connection_status': ConnectionStatus.FAILED,
                'integration_type': 'jira',
                'jira_url': None,
                'jira_email': None,
                'last_tested_at': None,
                'last_sync_at': None
            }

    async def sync_project(
        self,
        workspace_id: str,
        project_id: str,
        jira_project_key: str,
        max_results: int = 50
    ) -> Dict[str, Any]:
        """
        Sync a Jira project with CogniSim.
        
        Args:
            workspace_id: Workspace ID
            project_id: CogniSim project ID
            jira_project_key: Jira project key
            max_results: Maximum number of issues to sync
            
        Returns:
            Sync result
        """
        sync_log_id = str(uuid4())
        
        try:
            logger.info(f"Starting sync for project {project_id} with Jira project {jira_project_key}")
            
            # Create initial sync log
            await self._create_sync_log(
                sync_log_id, workspace_id, project_id, 'manual', SyncStatus.IN_PROGRESS
            )
            
            # Get credentials
            credentials = await self._get_credentials(workspace_id)
            if not credentials:
                await self._update_sync_log(sync_log_id, SyncStatus.FAILED, error_details={'error': 'No Jira credentials found'})
                return {
                    'success': False,
                    'message': 'No Jira credentials found for this workspace',
                    'sync_log_id': sync_log_id,
                    'items_synced': 0,
                    'items_created': 0,
                    'items_updated': 0,
                    'errors_count': 1,
                    'sync_status': SyncStatus.FAILED
                }
            
            # Initialize Jira client with encrypted credentials
            jira_client = JiraClient.from_encrypted_credentials(
                jira_url=credentials['jira_url'],
                email=credentials['jira_email'],
                encrypted_api_token=credentials['jira_api_token_encrypted']
            )
            
            # Test connection
            connection_success, connection_message = jira_client.test_connection()
            if not connection_success:
                await self._update_sync_log(
                    sync_log_id, SyncStatus.FAILED, 
                    error_details={'error': f'Connection failed: {connection_message}'}
                )
                return {
                    'success': False,
                    'message': f'Jira connection failed: {connection_message}',
                    'sync_log_id': sync_log_id,
                    'items_synced': 0,
                    'items_created': 0,
                    'items_updated': 0,
                    'errors_count': 1,
                    'sync_status': SyncStatus.FAILED
                }
            
            # Get Jira issues
            jira_issues = jira_client.get_project_issues(jira_project_key, max_results)
            if not jira_issues:
                await self._update_sync_log(sync_log_id, SyncStatus.SUCCESS, items_synced=0)
                return {
                    'success': True,
                    'message': f'No issues found in Jira project {jira_project_key}',
                    'sync_log_id': sync_log_id,
                    'items_synced': 0,
                    'items_created': 0,
                    'items_updated': 0,
                    'errors_count': 0,
                    'sync_status': SyncStatus.SUCCESS
                }
            
            # Process issues
            sync_results = await self._process_issues(
                jira_issues, project_id, workspace_id, credentials['jira_url']
            )
            
            # Update sync log with results
            status = SyncStatus.SUCCESS if sync_results['errors_count'] == 0 else SyncStatus.PARTIAL
            await self._update_sync_log(
                sync_log_id,
                status,
                items_synced=sync_results['items_synced'],
                items_created=sync_results['items_created'],
                items_updated=sync_results['items_updated'],
                errors_count=sync_results['errors_count']
            )
            
            jira_client.close()
            
            return {
                'success': True,
                'message': f'Synced {sync_results["items_synced"]} issues from {jira_project_key}',
                'sync_log_id': sync_log_id,
                'items_synced': sync_results['items_synced'],
                'items_created': sync_results['items_created'],
                'items_updated': sync_results['items_updated'],
                'errors_count': sync_results['errors_count'],
                'sync_status': status
            }
            
        except Exception as e:
            logger.error(f"Sync failed: {str(e)}")
            await self._update_sync_log(
                sync_log_id, SyncStatus.FAILED, 
                error_details={'error': str(e)}
            )
            return {
                'success': False,
                'message': f'Sync failed: {str(e)}',
                'sync_log_id': sync_log_id,
                'items_synced': 0,
                'items_created': 0,
                'items_updated': 0,
                'errors_count': 1,
                'sync_status': SyncStatus.FAILED
            }

    async def _get_credentials(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Get Jira credentials for workspace."""
        try:
            result = self.supabase.table("integration_credentials").select("*").eq("workspace_id", workspace_id).eq("integration_type", "jira").eq("is_active", True).execute()
            return result.data[0] if result.data else None
        except Exception:
            return None

    async def _create_sync_log(
        self, 
        sync_log_id: str, 
        workspace_id: str, 
        project_id: str, 
        sync_type: str, 
        status: SyncStatus
    ):
        """Create a sync log entry."""
        try:
            sync_log = {
                'id': sync_log_id,
                'workspace_id': workspace_id,
                'project_id': project_id,
                'integration_type': 'jira',
                'sync_type': sync_type,
                'status': status.value,
                'items_synced': 0,
                'items_created': 0,
                'items_updated': 0,
                'errors_count': 0,
                'started_at': datetime.utcnow().isoformat()
            }
            self.supabase.table("sync_logs").insert(sync_log).execute()
        except Exception as e:
            logger.error(f"Failed to create sync log: {str(e)}")

    async def _update_sync_log(
        self, 
        sync_log_id: str, 
        status: SyncStatus, 
        items_synced: int = 0,
        items_created: int = 0,
        items_updated: int = 0,
        errors_count: int = 0,
        error_details: Optional[Dict] = None
    ):
        """Update a sync log entry."""
        try:
            update_data = {
                'status': status.value,
                'items_synced': items_synced,
                'items_created': items_created,
                'items_updated': items_updated,
                'errors_count': errors_count,
                'completed_at': datetime.utcnow().isoformat()
            }
            if error_details:
                update_data['error_details'] = error_details
            
            self.supabase.table("sync_logs").update(update_data).eq("id", sync_log_id).execute()
        except Exception as e:
            logger.error(f"Failed to update sync log: {str(e)}")

    async def _process_issues(
        self, 
        jira_issues: List[Dict[str, Any]], 
        project_id: str, 
        workspace_id: str, 
        jira_url: str
    ) -> Dict[str, int]:
        """Process Jira issues and sync them to CogniSim."""
        items_synced = 0
        items_created = 0
        items_updated = 0
        errors_count = 0
        
        for jira_issue in jira_issues:
            try:
                # Map Jira issue to CogniSim item
                mapped_item = self.field_mapper.jira_to_cognisim_item(
                    jira_issue, project_id, workspace_id
                )
                
                # Check if item already exists (by Jira key)
                jira_key = jira_issue.get('key', '')
                existing_mapping = self.supabase.table("integration_mappings").select("item_id").eq("external_item_id", jira_key).eq("external_system", "jira").execute()
                
                if existing_mapping.data:
                    # Update existing item
                    item_id = existing_mapping.data[0]['item_id']
                    mapped_item['id'] = item_id
                    self.supabase.table("items").update(mapped_item).eq("id", item_id).execute()
                    items_updated += 1
                else:
                    # Create new item
                    self.supabase.table("items").insert(mapped_item).execute()
                    
                    # Create integration mapping
                    mapping = self.field_mapper.create_integration_mapping(
                        mapped_item['id'], jira_key, jira_issue.get('id', ''), jira_url
                    )
                    self.supabase.table("integration_mappings").insert(mapping).execute()
                    items_created += 1
                
                items_synced += 1
                
            except Exception as e:
                logger.error(f"Failed to process issue {jira_issue.get('key', 'unknown')}: {str(e)}")
                errors_count += 1
                continue
        
        return {
            'items_synced': items_synced,
            'items_created': items_created,
            'items_updated': items_updated,
            'errors_count': errors_count
        }
    
    async def migrate_credentials_to_encryption(self) -> Dict[str, Any]:
        """
        Migrate existing credentials from simple encoding to AES encryption.
        This method should be run once during deployment to upgrade existing credentials.
        
        Returns:
            Migration result with statistics
        """
        try:
            logger.info("Starting credential migration to encryption")
            
            # Get all existing credentials
            result = self.supabase.table("integration_credentials").select("*").eq("integration_type", "jira").eq("is_active", True).execute()
            
            if not result.data:
                return {
                    'success': True,
                    'message': 'No credentials found to migrate',
                    'migrated_count': 0,
                    'failed_count': 0
                }
            
            migrated_count = 0
            failed_count = 0
            encryption_service = get_token_encryption_service()
            
            for credential in result.data:
                try:
                    # Skip if already encrypted (check if it looks like encrypted data)
                    encrypted_token = credential['jira_api_token_encrypted']
                    if encryption_service.is_encrypted(encrypted_token):
                        logger.info(f"Credential for workspace {credential['workspace_id']} already encrypted, skipping")
                        continue
                    
                    # Try to decode with old system
                    try:
                        plaintext_token = simple_credential_store.decode_credential(encrypted_token)
                        logger.info(f"Successfully decoded old credential for workspace {credential['workspace_id']}")
                    except Exception as decode_error:
                        logger.warning(f"Failed to decode old credential for workspace {credential['workspace_id']}: {decode_error}")
                        # Assume it might already be plaintext
                        plaintext_token = encrypted_token
                    
                    # Encrypt with new system
                    new_encrypted_token = encryption_service.encrypt(plaintext_token)
                    
                    # Update database
                    update_result = self.supabase.table("integration_credentials").update({
                        'jira_api_token_encrypted': new_encrypted_token,
                        'updated_at': datetime.utcnow().isoformat()
                    }).eq("id", credential['id']).execute()
                    
                    if update_result.data:
                        migrated_count += 1
                        logger.info(f"Successfully migrated credential for workspace {credential['workspace_id']}")
                    else:
                        failed_count += 1
                        logger.error(f"Failed to update credential for workspace {credential['workspace_id']}")
                
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Failed to migrate credential for workspace {credential['workspace_id']}: {str(e)}")
                    continue
            
            logger.info(f"Credential migration completed: {migrated_count} migrated, {failed_count} failed")
            
            return {
                'success': True,
                'message': f'Migration completed: {migrated_count} credentials migrated, {failed_count} failed',
                'migrated_count': migrated_count,
                'failed_count': failed_count
            }
            
        except Exception as e:
            logger.error(f"Credential migration failed: {str(e)}")
            return {
                'success': False,
                'message': f'Migration failed: {str(e)}',
                'migrated_count': 0,
                'failed_count': 0
            }
