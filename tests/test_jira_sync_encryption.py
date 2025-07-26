"""
Test suite for JiraSyncService encryption integration.
Tests the integration between JiraSyncService and TokenEncryptionService.
"""

import pytest
import pytest_asyncio
from unittest.mock import Mock, patch, AsyncMock
from app.services.jira.jira_sync_service import JiraSyncService
from app.services.encryption.token_encryption import get_token_encryption_service


@pytest.mark.asyncio
class TestJiraSyncServiceEncryption:
    """Test JiraSyncService integration with encryption service."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_supabase = Mock()
        self.service = JiraSyncService(self.mock_supabase)
        self.test_workspace_id = "test-workspace-123"
        self.test_jira_url = "https://test.atlassian.net"
        self.test_email = "test@example.com"
        self.test_token = "ATATT3xFfGF0T5JNjBdN-QhWDmAEI7YIjKLMNO"
        
        # Get encryption service and encrypt test token
        self.encryption_service = get_token_encryption_service()
        self.encrypted_token = self.encryption_service.encrypt(self.test_token)
    
    @patch('app.services.jira.jira_sync_service.JiraClient')
    async def test_save_credentials_with_encryption(self, mock_jira_client):
        """Test saving credentials with encryption."""
        # Mock successful connection
        mock_client_instance = Mock()
        mock_client_instance.connect.return_value = (True, "Connection successful")
        mock_client_instance.close.return_value = None
        mock_jira_client.return_value = mock_client_instance
        
        # Mock database operations
        self.mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        self.mock_supabase.table.return_value.insert.return_value.execute.return_value = Mock()
        
        # Test credential saving
        result = await self.service.save_and_test_credentials(
            workspace_id=self.test_workspace_id,
            jira_url=self.test_jira_url,
            jira_email=self.test_email,
            jira_api_token=self.test_token
        )
        
        # Verify result
        assert result['success'] is True
        assert 'integration_id' in result
        
        # Verify insert was called with encrypted token
        insert_call = self.mock_supabase.table.return_value.insert.call_args
        credential_data = insert_call[0][0]
        
        # The token should be encrypted (different from original)
        assert credential_data['jira_api_token_encrypted'] != self.test_token
        
        # The encrypted token should be decryptable to original
        decrypted = self.encryption_service.decrypt(credential_data['jira_api_token_encrypted'])
        assert decrypted == self.test_token
    
    @patch('app.services.jira.jira_sync_service.JiraClient')
    async def test_sync_with_encrypted_credentials(self, mock_jira_client):
        """Test sync operation using encrypted credentials."""
        # Mock credential retrieval
        mock_credentials = {
            'jira_url': self.test_jira_url,
            'jira_email': self.test_email,
            'jira_api_token_encrypted': self.encrypted_token
        }
        
        self.service._get_credentials = AsyncMock(return_value=mock_credentials)
        
        # Mock JiraClient factory method
        mock_client_instance = Mock()
        mock_client_instance.test_connection.return_value = (True, "Connection successful")
        mock_client_instance.get_projects.return_value = []
        mock_client_instance.close.return_value = None
        mock_jira_client.from_encrypted_credentials.return_value = mock_client_instance
        
        # Mock database operations
        self.service._create_sync_log = AsyncMock()
        self.service._update_sync_log = AsyncMock()
        
        # Test sync operation
        result = await self.service.sync_project(
            workspace_id=self.test_workspace_id,
            project_id="test-project",
            jira_project_key="TEST"
        )
        
        # Verify the factory method was called correctly
        mock_jira_client.from_encrypted_credentials.assert_called_once_with(
            jira_url=self.test_jira_url,
            email=self.test_email,
            encrypted_api_token=self.encrypted_token
        )
        
        # Verify sync completed
        assert 'sync_log_id' in result
    
    async def test_migration_from_old_encoding(self):
        """Test migration from old simple encoding to new encryption."""
        # Mock old encoded credential
        old_encoded = "old_encoded_token_data"
        
        mock_credential = {
            'id': 'cred-123',
            'workspace_id': self.test_workspace_id,
            'jira_api_token_encrypted': old_encoded
        }
        
        # Mock database responses
        self.mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [mock_credential]
        self.mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [mock_credential]
        
        # Mock simple credential store
        with patch('app.services.jira.jira_sync_service.simple_credential_store') as mock_store:
            mock_store.decode_credential.return_value = self.test_token
            
            # Test migration
            result = await self.service.migrate_credentials_to_encryption()
            
            # Verify migration success
            assert result['success'] is True
            assert result['migrated_count'] == 1
            assert result['failed_count'] == 0
            
            # Verify update was called
            update_call = self.mock_supabase.table.return_value.update.call_args
            update_data = update_call[0][0]
            
            # The new token should be encrypted and different from old
            assert update_data['jira_api_token_encrypted'] != old_encoded
            
            # The new token should decrypt to original
            decrypted = self.encryption_service.decrypt(update_data['jira_api_token_encrypted'])
            assert decrypted == self.test_token
    
    async def test_migration_skips_already_encrypted(self):
        """Test that migration skips credentials that are already encrypted."""
        mock_credential = {
            'id': 'cred-123',
            'workspace_id': self.test_workspace_id,
            'jira_api_token_encrypted': self.encrypted_token  # Already encrypted
        }
        
        # Mock database responses
        self.mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [mock_credential]
        
        # Test migration
        result = await self.service.migrate_credentials_to_encryption()
        
        # Verify no migration was attempted
        assert result['success'] is True
        assert result['migrated_count'] == 0
        assert result['failed_count'] == 0
        
        # Verify no update was called
        self.mock_supabase.table.return_value.update.assert_not_called()
    
    async def test_no_credentials_to_migrate(self):
        """Test migration when no credentials exist."""
        # Mock empty database response
        self.mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        
        # Test migration
        result = await self.service.migrate_credentials_to_encryption()
        
        # Verify result
        assert result['success'] is True
        assert result['migrated_count'] == 0
        assert result['failed_count'] == 0
        assert "No credentials found" in result['message']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
