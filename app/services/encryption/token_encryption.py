# app/services/encryption/token_encryption.py
"""
Advanced Token Encryption Service for Jira API Tokens

This service provides secure encryption and decryption of sensitive API tokens
using AES-256-GCM encryption with unique nonces for each token.

Security Features:
- AES-256-GCM encryption (authenticated encryption)
- Unique nonce per encryption operation
- Base64 encoding for database storage
- Comprehensive error handling
"""

import os
import base64
import logging
from typing import Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from app.core.config import settings

logger = logging.getLogger("cognisim_ai")


class TokenEncryptionService:
    """
    Service for encrypting and decrypting sensitive tokens using AES-256-GCM.
    
    This service ensures that sensitive API tokens (like Jira API tokens) are
    stored securely in the database with strong encryption.
    """
    
    def __init__(self, encryption_key: Optional[bytes] = None):
        """
        Initialize the encryption service.
        
        Args:
            encryption_key: Optional encryption key. If not provided, will use
                          the key from settings.
        """
        self.encryption_key = encryption_key or self._get_encryption_key()
        self._validate_key()
    
    def _get_encryption_key(self) -> bytes:
        """
        Get the encryption key from application settings.
        
        Returns:
            bytes: The encryption key for AES-256
            
        Raises:
            ValueError: If encryption key is not configured or invalid
        """
        try:
            # Get the key from settings
            if hasattr(settings, 'ENCRYPTION_SECRET_KEY'):
                key_str = settings.ENCRYPTION_SECRET_KEY
                if hasattr(key_str, 'get_secret_value'):
                    key_str = key_str.get_secret_value()
            else:
                # Fallback to environment variable
                key_str = os.getenv('ENCRYPTION_SECRET_KEY')
            
            if not key_str:
                raise ValueError("Encryption key not configured in settings or environment")
            
            # If key is base64 encoded, decode it
            try:
                return base64.urlsafe_b64decode(key_str)
            except Exception:
                # If not base64, use the string directly but hash it to get proper length
                import hashlib
                return hashlib.sha256(key_str.encode('utf-8')).digest()
                
        except Exception as e:
            logger.error(f"Failed to get encryption key: {str(e)}")
            raise ValueError(f"Invalid encryption key configuration: {str(e)}")
    
    def _validate_key(self):
        """
        Validate that the encryption key is suitable for AES-256.
        
        Raises:
            ValueError: If the key is not 32 bytes (256 bits)
        """
        if len(self.encryption_key) != 32:
            raise ValueError(f"Encryption key must be 32 bytes for AES-256, got {len(self.encryption_key)} bytes")
    
    def encrypt(self, token: str) -> str:
        """
        Encrypt a token using AES-256-GCM with a unique nonce.
        
        Args:
            token: The plaintext token to encrypt
            
        Returns:
            str: Base64-encoded encrypted token (nonce + ciphertext)
            
        Raises:
            ValueError: If token is empty or encryption fails
            TypeError: If token is not a string
        """
        if not isinstance(token, str):
            raise TypeError("Token must be a string")
        
        if not token.strip():
            raise ValueError("Token cannot be empty")
        
        try:
            # Generate a random 96-bit (12 bytes) nonce for GCM
            nonce = os.urandom(12)
            
            # Create AESGCM cipher
            aesgcm = AESGCM(self.encryption_key)
            
            # Encrypt the token
            ciphertext = aesgcm.encrypt(nonce, token.encode('utf-8'), None)
            
            # Combine nonce and ciphertext for storage
            encrypted_data = nonce + ciphertext
            
            # Encode as base64 for database storage
            encoded_data = base64.urlsafe_b64encode(encrypted_data)
            
            logger.info("Token encrypted successfully")
            return encoded_data.decode('utf-8')
            
        except Exception as e:
            logger.error(f"Encryption failed: {type(e).__name__}")
            raise ValueError(f"Failed to encrypt token: {str(e)}")
    
    def decrypt(self, encrypted_token: str) -> str:
        """
        Decrypt an encrypted token.
        
        Args:
            encrypted_token: Base64-encoded encrypted token (nonce + ciphertext)
            
        Returns:
            str: The decrypted plaintext token
            
        Raises:
            ValueError: If decryption fails or token is invalid
            TypeError: If encrypted_token is not a string
        """
        if not isinstance(encrypted_token, str):
            raise TypeError("Encrypted token must be a string")
        
        if not encrypted_token.strip():
            raise ValueError("Encrypted token cannot be empty")
        
        try:
            # Decode the base64 data
            encrypted_data = base64.urlsafe_b64decode(encrypted_token.encode('utf-8'))
            
            # Extract nonce and ciphertext
            if len(encrypted_data) < 12:
                raise ValueError("Invalid encrypted token format")
            
            nonce = encrypted_data[:12]  # First 12 bytes is the nonce
            ciphertext = encrypted_data[12:]  # The rest is the ciphertext
            
            # Create AESGCM cipher
            aesgcm = AESGCM(self.encryption_key)
            
            # Decrypt the token
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            
            logger.info("Token decrypted successfully")
            return plaintext.decode('utf-8')
            
        except InvalidTag:
            logger.error("Decryption failed: Invalid authentication tag")
            raise ValueError("Failed to decrypt token: Invalid or corrupted data")
        except Exception as e:
            logger.error(f"Decryption failed: {type(e).__name__}")
            raise ValueError(f"Failed to decrypt token: {str(e)}")
    
    def is_encrypted(self, token: str) -> bool:
        """
        Check if a token appears to be encrypted (basic heuristic check).
        
        Args:
            token: The token to check
            
        Returns:
            bool: True if the token appears to be encrypted
        """
        if not isinstance(token, str) or not token.strip():
            return False
        
        try:
            # Try to decode as base64
            decoded = base64.urlsafe_b64decode(token.encode('utf-8'))
            # Encrypted tokens should be at least 12 bytes (nonce) + some ciphertext
            return len(decoded) > 12
        except Exception:
            return False
    
    def rotate_encryption(self, old_encrypted_token: str, new_encryption_key: bytes) -> str:
        """
        Re-encrypt a token with a new encryption key.
        
        This is useful for key rotation scenarios.
        
        Args:
            old_encrypted_token: Token encrypted with the current key
            new_encryption_key: New encryption key to use
            
        Returns:
            str: Token encrypted with the new key
        """
        # Decrypt with current key
        plaintext_token = self.decrypt(old_encrypted_token)
        
        # Create new service with new key
        new_service = TokenEncryptionService(new_encryption_key)
        
        # Encrypt with new key
        return new_service.encrypt(plaintext_token)


# Create a singleton instance for application use
_token_encryption_service = None

def get_token_encryption_service() -> TokenEncryptionService:
    """
    Get the singleton token encryption service instance.
    
    Returns:
        TokenEncryptionService: The encryption service instance
    """
    global _token_encryption_service
    if _token_encryption_service is None:
        _token_encryption_service = TokenEncryptionService()
    return _token_encryption_service
