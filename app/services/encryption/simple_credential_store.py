# services/encryption/simple_credential_store.py
# Simple credential storage for development (can be enhanced with encryption later)

import base64
import logging
from typing import Optional
from app.core.config import settings

logger = logging.getLogger("cognisim_ai")


class SimpleCredentialStore:
    """
    Simple credential storage for development.
    In production, this should be replaced with proper encryption.
    """
    
    def encode_credential(self, credential: str) -> str:
        """
        Encode a credential for storage (base64 encoding for development).
        
        Args:
            credential: The plain text credential to encode
            
        Returns:
            Base64 encoded credential
        """
        try:
            if not credential:
                raise ValueError("Credential cannot be empty")
                
            # Simple base64 encoding for development
            encoded_bytes = base64.b64encode(credential.encode('utf-8'))
            encoded_string = encoded_bytes.decode('utf-8')
            
            logger.info("Credential encoded successfully")
            return encoded_string
            
        except Exception as e:
            logger.error(f"Credential encoding failed: {str(e)}")
            raise ValueError("Failed to encode credential")
    
    def decode_credential(self, encoded_credential: str) -> str:
        """
        Decode an encoded credential.
        
        Args:
            encoded_credential: The base64 encoded credential
            
        Returns:
            Plain text credential
        """
        try:
            if not encoded_credential:
                raise ValueError("Encoded credential cannot be empty")
                
            decoded_bytes = base64.b64decode(encoded_credential.encode('utf-8'))
            decoded_string = decoded_bytes.decode('utf-8')
            
            logger.info("Credential decoded successfully")
            return decoded_string
            
        except Exception as e:
            logger.error(f"Credential decoding failed: {str(e)}")
            raise ValueError("Failed to decode credential")


# Create a global instance
simple_credential_store = SimpleCredentialStore()
