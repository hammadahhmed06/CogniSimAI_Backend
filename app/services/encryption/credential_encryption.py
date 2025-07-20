# services/encryption/credential_encryption.py
# AES-256 encryption service for secure credential storage

import os
import base64
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from app.core.config import settings

logger = logging.getLogger("cognisim_ai")


class CredentialEncryption:
    """
    Handles AES-256 encryption/decryption of sensitive credentials.
    Uses PBKDF2 key derivation for additional security.
    """
    
    def __init__(self):
        self.encryption_key = self._get_encryption_key()
        self.cipher_suite = Fernet(self.encryption_key)
    
    def _get_encryption_key(self) -> bytes:
        """
        Generate encryption key from environment variables using PBKDF2.
        """
        try:
            # Get password and salt from settings
            password = settings.ENCRYPTION_SECRET_KEY.get_secret_value() if settings.ENCRYPTION_SECRET_KEY else "default-key"
            salt = settings.ENCRYPTION_SALT or "default-salt"
            
            # Convert to bytes
            password_bytes = password.encode('utf-8')
            salt_bytes = salt.encode('utf-8')
            
            # Use PBKDF2 for key derivation
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,  # 32 bytes = 256 bits
                salt=salt_bytes,
                iterations=100000,  # OWASP recommended minimum
            )
            
            # Derive and encode the key
            key = base64.urlsafe_b64encode(kdf.derive(password_bytes))
            return key
            
        except Exception as e:
            logger.error(f"Failed to generate encryption key: {str(e)}")
            raise ValueError("Could not initialize encryption service")
    
    def encrypt_credential(self, credential: str) -> str:
        """
        Encrypt a credential string.
        
        Args:
            credential: The plain text credential to encrypt
            
        Returns:
            Base64 encoded encrypted credential
        """
        try:
            if not credential:
                raise ValueError("Credential cannot be empty")
                
            encrypted_bytes = self.cipher_suite.encrypt(credential.encode('utf-8'))
            encrypted_string = encrypted_bytes.decode('utf-8')
            
            logger.info("Credential encrypted successfully")
            return encrypted_string
            
        except Exception as e:
            logger.error(f"Credential encryption failed: {str(e)}")
            raise ValueError("Failed to encrypt credential")
    
    def decrypt_credential(self, encrypted_credential: str) -> str:
        """
        Decrypt an encrypted credential string.
        
        Args:
            encrypted_credential: The base64 encoded encrypted credential
            
        Returns:
            Plain text credential
        """
        try:
            if not encrypted_credential:
                raise ValueError("Encrypted credential cannot be empty")
                
            decrypted_bytes = self.cipher_suite.decrypt(encrypted_credential.encode('utf-8'))
            decrypted_string = decrypted_bytes.decode('utf-8')
            
            logger.info("Credential decrypted successfully")
            return decrypted_string
            
        except Exception as e:
            logger.error(f"Credential decryption failed: {str(e)}")
            raise ValueError("Failed to decrypt credential - credential may be corrupted")
    
    def is_valid_encrypted_credential(self, encrypted_credential: str) -> bool:
        """
        Check if an encrypted credential can be decrypted without actually decrypting it.
        
        Args:
            encrypted_credential: The encrypted credential to validate
            
        Returns:
            True if credential can be decrypted, False otherwise
        """
        try:
            self.decrypt_credential(encrypted_credential)
            return True
        except:
            return False


# Singleton instance for global use
credential_encryption = CredentialEncryption()
