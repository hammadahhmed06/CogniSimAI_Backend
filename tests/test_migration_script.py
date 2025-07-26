"""
Test suite for the credential migration script.
Tests the migration from old encoding to new encryption.
"""

import pytest
import pytest_asyncio
from unittest.mock import Mock, patch, AsyncMock
from migrate_credentials import CredentialMigrationScript
from app.services.encryption.token_encryption import get_token_encryption_service


@pytest.mark.asyncio
class TestCredentialMigrationScript:
    """Test the credential migration script functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_supabase = Mock()
        self.migration_script = CredentialMigrationScript(
            supabase_client=self.mock_supabase,
            dry_run=True,
            batch_size=5
        )
        
        self.encryption_service = get_token_encryption_service()
        self.test_token = "ATATT3xFfGF0T5JNjBdN-QhWDmAEI7YIjKLMNO"
        self.encrypted_token = self.encryption_service.encrypt(self.test_token)
        
        # Mock credentials data
        self.mock_old_credential = {
            'id': 'cred-123',
            'workspace_id': 'workspace-456',
            'jira_api_token_encrypted': 'old_encoded_token_data',
            'integration_type': 'jira',
            'is_active': True
        }
        
        self.mock_encrypted_credential = {
            'id': 'cred-789',
            'workspace_id': 'workspace-999',
            'jira_api_token_encrypted': self.encrypted_token,
            'integration_type': 'jira',
            'is_active': True
        }
    
    async def test_get_all_credentials(self):
        """Test retrieving all credentials from database."""
        # Mock database response
        mock_response = Mock()
        mock_response.data = [self.mock_old_credential, self.mock_encrypted_credential]
        
        self.mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_response
        
        # Test credential retrieval
        credentials = await self.migration_script.get_all_credentials()
        
        # Verify results
        assert len(credentials) == 2
        assert self.migration_script.stats['total_found'] == 2
        
        # Verify database query
        self.mock_supabase.table.assert_called_with("integration_credentials")
    
    def test_analyze_credential_already_encrypted(self):
        """Test analyzing a credential that's already encrypted."""
        analysis = self.migration_script.analyze_credential(self.mock_encrypted_credential)
        
        assert analysis['id'] == 'cred-789'
        assert analysis['workspace_id'] == 'workspace-999'
        assert analysis['is_encrypted'] is True
        assert analysis['needs_migration'] is False
        assert analysis['error'] is None
    
    @patch('migrate_credentials.simple_credential_store')
    def test_analyze_credential_needs_migration(self, mock_store):
        """Test analyzing a credential that needs migration."""
        # Mock old credential store
        mock_store.decode_credential.return_value = self.test_token
        
        analysis = self.migration_script.analyze_credential(self.mock_old_credential)
        
        assert analysis['id'] == 'cred-123'
        assert analysis['workspace_id'] == 'workspace-456'
        assert analysis['is_encrypted'] is False
        assert analysis['needs_migration'] is True
        assert analysis['can_decode_old'] is True
        assert analysis['plaintext_token'] == self.test_token
        assert analysis['error'] is None
    
    @patch('migrate_credentials.simple_credential_store')
    def test_analyze_credential_decode_fails(self, mock_store):
        """Test analyzing a credential where old decoding fails."""
        # Mock decode failure
        mock_store.decode_credential.side_effect = Exception("Decode failed")
        
        analysis = self.migration_script.analyze_credential(self.mock_old_credential)
        
        assert analysis['id'] == 'cred-123'
        assert analysis['needs_migration'] is True
        assert analysis['can_decode_old'] is False
        assert analysis['plaintext_token'] == 'old_encoded_token_data'  # Falls back to treating as plaintext
    
    async def test_migrate_credential_success(self):
        """Test successful credential migration."""
        analysis = {
            'id': 'cred-123',
            'workspace_id': 'workspace-456',
            'needs_migration': True,
            'plaintext_token': self.test_token,
            'error': None
        }
        
        # Mock successful database update
        mock_response = Mock()
        mock_response.data = [{'id': 'cred-123'}]
        self.mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = mock_response
        
        # Test migration (dry run mode)
        result = await self.migration_script.migrate_credential(self.mock_old_credential, analysis)
        
        assert result is True
    
    async def test_migrate_credential_no_migration_needed(self):
        """Test migrating a credential that doesn't need migration."""
        analysis = {
            'id': 'cred-789',
            'workspace_id': 'workspace-999',
            'needs_migration': False,
            'error': None
        }
        
        result = await self.migration_script.migrate_credential(self.mock_encrypted_credential, analysis)
        
        assert result is True
        # Should not call database update
        self.mock_supabase.table.return_value.update.assert_not_called()
    
    async def test_migrate_credential_no_plaintext(self):
        """Test migrating a credential without plaintext token."""
        analysis = {
            'id': 'cred-123',
            'workspace_id': 'workspace-456',
            'needs_migration': True,
            'plaintext_token': None,
            'error': None
        }
        
        result = await self.migration_script.migrate_credential(self.mock_old_credential, analysis)
        
        assert result is False
    
    @patch('migrate_credentials.simple_credential_store')
    async def test_run_migration_complete_flow(self, mock_store):
        """Test the complete migration flow."""
        # Mock old credential store
        mock_store.decode_credential.return_value = self.test_token
        
        # Mock database responses
        mock_get_response = Mock()
        mock_get_response.data = [self.mock_old_credential]
        
        self.mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_get_response
        
        # Run migration
        stats = await self.migration_script.run_migration()
        
        # Verify statistics
        assert stats['total_found'] == 1
        assert stats['migrated'] == 1
        assert stats['failed'] == 0
        assert stats['already_encrypted'] == 0
    
    @patch('migrate_credentials.simple_credential_store')
    async def test_run_migration_mixed_credentials(self, mock_store):
        """Test migration with mixed old and new credentials."""
        # Mock old credential store
        mock_store.decode_credential.return_value = self.test_token
        
        # Mock database response with both types
        mock_get_response = Mock()
        mock_get_response.data = [self.mock_old_credential, self.mock_encrypted_credential]
        
        self.mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_get_response
        
        # Run migration
        stats = await self.migration_script.run_migration()
        
        # Verify statistics
        assert stats['total_found'] == 2
        assert stats['migrated'] == 1  # Only the old one
        assert stats['failed'] == 0
        assert stats['already_encrypted'] == 1  # The encrypted one
    
    async def test_validate_migration_success(self):
        """Test successful migration validation."""
        # Mock database response with encrypted credentials
        mock_response = Mock()
        mock_response.data = [self.mock_encrypted_credential]
        
        self.mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_response
        
        # Run validation
        validation_stats = await self.migration_script.validate_migration()
        
        # Verify results
        assert validation_stats['total_checked'] == 1
        assert validation_stats['properly_encrypted'] == 1
        assert validation_stats['validation_failed'] == 0
        assert len(validation_stats['errors']) == 0
    
    async def test_validate_migration_with_failures(self):
        """Test validation with some failed credentials."""
        # Create invalid encrypted credential
        invalid_credential = {
            'id': 'cred-invalid',
            'workspace_id': 'workspace-invalid',
            'jira_api_token_encrypted': 'not_encrypted_data',
            'integration_type': 'jira',
            'is_active': True
        }
        
        # Mock database response
        mock_response = Mock()
        mock_response.data = [self.mock_encrypted_credential, invalid_credential]
        
        self.mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_response
        
        # Run validation
        validation_stats = await self.migration_script.validate_migration()
        
        # Verify results
        assert validation_stats['total_checked'] == 2
        assert validation_stats['properly_encrypted'] == 1
        assert validation_stats['validation_failed'] == 1
        assert len(validation_stats['errors']) == 1
        assert 'cred-invalid' in validation_stats['errors'][0]
    
    def test_batch_processing(self):
        """Test that batch size is respected."""
        # Test with larger batch
        large_batch_script = CredentialMigrationScript(
            supabase_client=self.mock_supabase,
            dry_run=True,
            batch_size=50
        )
        
        assert large_batch_script.batch_size == 50
        
        # Test with small batch
        small_batch_script = CredentialMigrationScript(
            supabase_client=self.mock_supabase,
            dry_run=True,
            batch_size=1
        )
        
        assert small_batch_script.batch_size == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
