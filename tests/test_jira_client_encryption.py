"""
Test suite for JiraClient encryption integration.
Tests the integration between JiraClient and TokenEncryptionService.
"""

import pytest
from unittest.mock import Mock, patch
from app.services.jira.jira_client import JiraClient
from app.services.encryption.token_encryption import get_token_encryption_service


class TestJiraClientEncryption:
    """Test JiraClient integration with encryption service."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.test_jira_url = "https://test.atlassian.net"
        self.test_email = "test@example.com"
        self.test_token = "ATATT3xFfGF0T5JNjBdN-QhWDmAEI7YIjKLMNO"
        
        # Get encryption service and encrypt test token
        self.encryption_service = get_token_encryption_service()
        self.encrypted_token = self.encryption_service.encrypt(self.test_token)
    
    def test_jira_client_with_encrypted_token(self):
        """Test JiraClient initialization with encrypted token."""
        # Create client with encrypted token
        client = JiraClient(
            jira_url=self.test_jira_url,
            email=self.test_email,
            api_token=self.encrypted_token,
            is_encrypted=True
        )
        
        # Verify the token was decrypted correctly
        assert client.api_token == self.test_token
        assert client.jira_url == self.test_jira_url
        assert client.email == self.test_email
        assert client.is_encrypted is True
    
    def test_jira_client_with_plaintext_token(self):
        """Test JiraClient initialization with plaintext token."""
        client = JiraClient(
            jira_url=self.test_jira_url,
            email=self.test_email,
            api_token=self.test_token,
            is_encrypted=False
        )
        
        # Verify the token remains unchanged
        assert client.api_token == self.test_token
        assert client.jira_url == self.test_jira_url
        assert client.email == self.test_email
        assert client.is_encrypted is False
    
    def test_from_encrypted_credentials_factory(self):
        """Test factory method for encrypted credentials."""
        client = JiraClient.from_encrypted_credentials(
            jira_url=self.test_jira_url,
            email=self.test_email,
            encrypted_api_token=self.encrypted_token
        )
        
        # Verify the token was decrypted correctly
        assert client.api_token == self.test_token
        assert client.jira_url == self.test_jira_url
        assert client.email == self.test_email
        assert client.is_encrypted is True
    
    def test_from_plaintext_credentials_factory(self):
        """Test factory method for plaintext credentials."""
        client = JiraClient.from_plaintext_credentials(
            jira_url=self.test_jira_url,
            email=self.test_email,
            api_token=self.test_token
        )
        
        # Verify the token remains unchanged
        assert client.api_token == self.test_token
        assert client.jira_url == self.test_jira_url
        assert client.email == self.test_email
        assert client.is_encrypted is False
    
    def test_invalid_encrypted_token_raises_error(self):
        """Test that invalid encrypted token raises appropriate error."""
        invalid_encrypted_token = "invalid_encrypted_data"
        
        with pytest.raises(ValueError, match="Invalid encrypted API token"):
            JiraClient(
                jira_url=self.test_jira_url,
                email=self.test_email,
                api_token=invalid_encrypted_token,
                is_encrypted=True
            )
    
    @patch('app.services.jira.jira_client.get_token_encryption_service')
    def test_encryption_service_error_handling(self, mock_get_service):
        """Test handling of encryption service errors."""
        # Mock encryption service to raise an exception
        mock_service = Mock()
        mock_service.decrypt.side_effect = Exception("Decryption failed")
        mock_get_service.return_value = mock_service
        
        with pytest.raises(ValueError, match="Invalid encrypted API token"):
            JiraClient(
                jira_url=self.test_jira_url,
                email=self.test_email,
                api_token=self.encrypted_token,
                is_encrypted=True
            )
    
    def test_backward_compatibility(self):
        """Test that existing code works without is_encrypted parameter."""
        # This should default to plaintext (is_encrypted=False)
        client = JiraClient(
            jira_url=self.test_jira_url,
            email=self.test_email,
            api_token=self.test_token
        )
        
        assert client.api_token == self.test_token
        assert client.is_encrypted is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
