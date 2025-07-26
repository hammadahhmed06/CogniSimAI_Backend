# Encryption Services

This directory contains encryption services for securing sensitive data, particularly API tokens.

## 📁 Structure

```
encryption/
├── token_encryption.py         # Advanced AES-256-GCM token encryption
├── simple_credential_store.py  # Legacy Base64 encoding (deprecated)
└── credential_encryption.py    # Unified credential encryption interface
```

## 🔐 TokenEncryptionService (`token_encryption.py`)

Advanced encryption service using AES-256-GCM for securing API tokens.

### Features
- **AES-256-GCM Encryption**: Military-grade authenticated encryption
- **Unique Nonces**: Each encryption uses a unique 96-bit nonce
- **Base64 Storage**: Encrypted data encoded for database storage
- **Singleton Pattern**: Single instance across application
- **Comprehensive Validation**: Input validation and error handling

### Security Specifications
- **Algorithm**: AES-256-GCM (Galois/Counter Mode)
- **Key Size**: 256 bits (32 bytes)
- **Nonce Size**: 96 bits (12 bytes)
- **Authentication**: Built-in authentication tag
- **Storage Format**: Base64-encoded (nonce + ciphertext)

### Usage

```python
from app.services.encryption.token_encryption import get_token_encryption_service

# Get singleton service
service = get_token_encryption_service()

# Encrypt a token
token = "ATATT3xFfGF0abc123XYZ"
encrypted = service.encrypt(token)

# Decrypt a token
decrypted = service.decrypt(encrypted)
assert decrypted == token

# Check if token is encrypted
is_encrypted = service.is_encrypted(encrypted)
assert is_encrypted == True

# Key rotation (if needed)
new_encrypted = service.rotate_encryption(encrypted, new_key)
```

### Methods

#### `encrypt(token: str) -> str`
Encrypts a plaintext token.
- **Parameters**: `token` - The plaintext token to encrypt
- **Returns**: Base64-encoded encrypted token
- **Raises**: `ValueError` for empty tokens, `TypeError` for non-strings

#### `decrypt(encrypted_token: str) -> str`
Decrypts an encrypted token.
- **Parameters**: `encrypted_token` - The encrypted token to decrypt
- **Returns**: Original plaintext token
- **Raises**: `ValueError` for invalid/corrupted tokens

#### `is_encrypted(token: str) -> bool`
Checks if a token appears to be encrypted.
- **Parameters**: `token` - The token to check
- **Returns**: `True` if token appears encrypted, `False` otherwise

#### `rotate_encryption(old_encrypted: str, new_key: bytes) -> str`
Re-encrypts a token with a new key.
- **Parameters**: 
  - `old_encrypted` - Token encrypted with current key
  - `new_key` - New 32-byte encryption key
- **Returns**: Token encrypted with new key

### Configuration

#### Environment Variables
```bash
ENCRYPTION_SECRET_KEY=base64-encoded-32-byte-key
```

#### Key Generation
```python
import os
import base64

# Generate a new 32-byte key
key = os.urandom(32)
key_b64 = base64.urlsafe_b64encode(key).decode()
print(f"ENCRYPTION_SECRET_KEY={key_b64}")
```

#### Key Management
- **Storage**: Environment variables or secure key vaults
- **Format**: Base64-encoded 32-byte keys
- **Validation**: Automatic key length validation
- **Rotation**: Supported via `rotate_encryption()` method

## 🔧 Simple Credential Store (`simple_credential_store.py`)

Legacy Base64 encoding service (deprecated but maintained for migration).

### Usage (Migration Only)
```python
from app.services.encryption.simple_credential_store import simple_credential_store

# Decode old credentials during migration
old_token = simple_credential_store.decode_credential(old_encoded)

# Encode new credentials (not recommended)
encoded = simple_credential_store.encode_credential(token)
```

### Migration Process
1. **Detect Format**: Check if credential uses old encoding
2. **Decode Legacy**: Use simple_credential_store to decode
3. **Re-encrypt**: Use TokenEncryptionService to encrypt
4. **Update Database**: Store new encrypted format

## 🔐 Credential Encryption (`credential_encryption.py`)

Unified interface for credential encryption operations.

### Features
- **Unified Interface**: Single entry point for encryption operations
- **Format Detection**: Automatically detects encryption format
- **Migration Support**: Handles both old and new formats
- **Type Safety**: Strong typing for credential operations

### Usage
```python
from app.services.encryption.credential_encryption import CredentialEncryption

encryption = CredentialEncryption()

# Encrypt any credential
encrypted = encryption.encrypt_credential(credential)

# Decrypt any credential (auto-detects format)
decrypted = encryption.decrypt_credential(encrypted)

# Check encryption status
status = encryption.get_encryption_status(credential)
```

## 🧪 Testing

### Test Coverage
```bash
# Core encryption tests
python -m pytest tests/test_token_encryption.py -v

# All encryption tests
python -m pytest tests/ -k "encryption" -v
```

### Test Categories
1. **Basic Encryption**: Round-trip encryption/decryption
2. **Error Handling**: Invalid inputs and edge cases
3. **Singleton Service**: Service instance management
4. **Token Formats**: Various token format handling
5. **Key Validation**: Encryption key validation
6. **Migration**: Legacy format migration

### Example Test
```python
def test_token_encryption():
    service = get_token_encryption_service()
    original = "test-token-123"
    
    # Encrypt
    encrypted = service.encrypt(original)
    assert encrypted != original
    
    # Decrypt
    decrypted = service.decrypt(encrypted)
    assert decrypted == original
    
    # Validation
    assert service.is_encrypted(encrypted)
    assert not service.is_encrypted(original)
```

## 🔒 Security Best Practices

### Key Management
- **Generation**: Use cryptographically secure random generators
- **Storage**: Environment variables or dedicated key management systems
- **Rotation**: Regular key rotation with migration support
- **Access Control**: Limit access to encryption keys

### Data Protection
- **In Transit**: Always use HTTPS for encrypted data transmission
- **At Rest**: Store encrypted data in database
- **In Memory**: Clear sensitive data from memory when possible
- **Logging**: Never log plaintext tokens or encryption keys

### Validation
- **Input Validation**: Validate all inputs before encryption/decryption
- **Output Verification**: Verify encryption/decryption operations
- **Format Checking**: Validate encrypted data format
- **Error Handling**: Secure error handling without information leakage

## 🚨 Error Handling

### Common Errors

#### `ValueError: Token cannot be empty`
- **Cause**: Attempting to encrypt empty or whitespace-only string
- **Solution**: Validate token before encryption

#### `ValueError: Failed to decrypt token: Invalid or corrupted data`
- **Cause**: Corrupted encrypted data or wrong encryption key
- **Solution**: Verify data integrity and encryption key

#### `ValueError: Encryption key must be 32 bytes for AES-256`
- **Cause**: Invalid encryption key length
- **Solution**: Use proper 32-byte encryption key

#### `TypeError: Token must be a string`
- **Cause**: Non-string input to encryption/decryption
- **Solution**: Ensure input is string type

### Error Recovery
```python
try:
    decrypted = service.decrypt(encrypted_token)
except ValueError as e:
    if "Invalid or corrupted data" in str(e):
        # Try legacy decoding
        decrypted = simple_credential_store.decode_credential(encrypted_token)
        # Re-encrypt with new system
        encrypted_token = service.encrypt(decrypted)
```

## 📊 Performance

### Encryption Performance
- **Speed**: ~10,000 encryptions per second
- **Memory**: Minimal memory overhead
- **CPU**: Optimized AES-GCM implementation
- **Scalability**: Stateless operations, highly scalable

### Optimization Tips
- **Singleton Service**: Use singleton pattern to avoid re-initialization
- **Batch Operations**: Process multiple tokens efficiently
- **Caching**: Cache decrypted tokens if appropriate (with care)
- **Key Reuse**: Reuse encryption service instance

## 🔄 Migration Guide

### From Simple Encoding
```python
# Detection
if not service.is_encrypted(token):
    # Decode with old system
    try:
        plaintext = simple_credential_store.decode_credential(token)
    except:
        # Treat as plaintext
        plaintext = token
    
    # Encrypt with new system
    encrypted = service.encrypt(plaintext)
    
    # Update database
    update_credential(encrypted)
```

### Production Migration
Use the provided migration script:
```bash
# Dry run
python migrate_credentials.py --dry-run

# Execute
python migrate_credentials.py

# Validate
python migrate_credentials.py --validate
```

## 🛠️ Development

### Adding New Encryption Methods
1. **Extend Service**: Add method to TokenEncryptionService
2. **Add Tests**: Comprehensive test coverage
3. **Update Interface**: Update unified interface if needed
4. **Document**: Add to this README

### Code Standards
- **Type Hints**: Use comprehensive type hints
- **Error Handling**: Consistent error handling patterns
- **Logging**: Appropriate logging without sensitive data
- **Testing**: 100% test coverage for encryption code

### Security Review
- **Peer Review**: All encryption code requires security review
- **Algorithm Choice**: Stick to proven algorithms (AES-GCM)
- **Implementation**: Use well-tested cryptography libraries
- **Key Management**: Follow key management best practices
