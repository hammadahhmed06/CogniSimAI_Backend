# Jira Integration Services

This directory contains all Jira-related services including client connections, data synchronization, and field mapping.

## 📁 Structure

```
jira/
├── jira_client.py      # Jira API client with encryption support
├── jira_sync_service.py # Credential management and sync operations  
└── jira_mapper.py      # Field mapping between Jira and CogniSim
```

## 🔗 JiraClient (`jira_client.py`)

Secure Jira API client with built-in encryption support.

### Features
- **Encryption Support**: Handles both encrypted and plaintext API tokens
- **Automatic Decryption**: Transparently decrypts encrypted tokens
- **Connection Management**: Robust connection handling with retries
- **Rate Limiting**: Built-in rate limiting to respect Jira API limits
- **Error Handling**: Comprehensive error handling and logging

### Usage

```python
from app.services.jira.jira_client import JiraClient

# With encrypted credentials
client = JiraClient.from_encrypted_credentials(
    jira_url="https://your-domain.atlassian.net",
    email="your-email@domain.com",
    encrypted_api_token="encrypted_token_here"
)

# With plaintext credentials (backward compatibility)
client = JiraClient.from_plaintext_credentials(
    jira_url="https://your-domain.atlassian.net", 
    email="your-email@domain.com",
    api_token="plaintext_token_here"
)

# Test connection
success, message = client.test_connection()
if success:
    # Get projects
    projects = client.get_projects()
```

### Constructor Parameters
- `jira_url`: Jira instance URL
- `email`: User email for authentication
- `api_token`: API token (encrypted or plaintext)
- `is_encrypted`: Whether the token is encrypted (default: False)

### Methods
- `connect()`: Establish connection to Jira
- `test_connection()`: Test if connection works
- `get_projects()`: Retrieve available projects
- `close()`: Close the connection
- `_rate_limit()`: Internal rate limiting

## 🔄 JiraSyncService (`jira_sync_service.py`)

Manages Jira credentials and synchronization operations with encryption.

### Features
- **Credential Encryption**: All API tokens encrypted before database storage
- **Migration Support**: Built-in migration from old encoding
- **Sync Operations**: Synchronize Jira data with CogniSim
- **Connection Testing**: Validate credentials before saving
- **Batch Processing**: Efficient processing of multiple items

### Usage

```python
from app.services.jira.jira_sync_service import JiraSyncService

# Initialize service
service = JiraSyncService(supabase_client)

# Save and test credentials (automatically encrypts)
result = await service.save_and_test_credentials(
    workspace_id="workspace-123",
    jira_url="https://your-domain.atlassian.net",
    jira_email="your-email@domain.com", 
    jira_api_token="your_api_token"
)

# Sync project data
sync_result = await service.sync_project(
    workspace_id="workspace-123",
    project_id="project-456",
    jira_project_key="PROJ"
)

# Migrate existing credentials to encryption
migration_result = await service.migrate_credentials_to_encryption()
```

### Key Methods
- `save_and_test_credentials()`: Save encrypted credentials after testing
- `get_integration_status()`: Get current integration status
- `sync_project()`: Synchronize Jira project data
- `migrate_credentials_to_encryption()`: Migrate old credentials
- `_get_credentials()`: Internal credential retrieval

## 🗂️ JiraFieldMapper (`jira_mapper.py`)

Handles field mapping between Jira and CogniSim data structures.

### Features
- **Field Transformation**: Convert between Jira and CogniSim formats
- **Type Mapping**: Handle different field types appropriately
- **Integration Mapping**: Create integration mappings for synced items

### Usage

```python
from app.services.jira.jira_mapper import JiraFieldMapper

mapper = JiraFieldMapper()

# Transform Jira issue to CogniSim format
cognisim_item = mapper.transform_jira_issue_to_cognisim(jira_issue)

# Create integration mapping
mapping = mapper.create_integration_mapping(
    cognisim_id="item-123",
    jira_key="PROJ-456", 
    jira_id="10001",
    jira_url="https://domain.atlassian.net"
)
```

## 🔒 Security Implementation

### Encryption Flow
1. **Save Credentials**: API tokens encrypted using AES-256-GCM
2. **Store Database**: Encrypted tokens stored in `integration_credentials` table
3. **Retrieve Credentials**: Encrypted tokens automatically decrypted when needed
4. **Use Client**: JiraClient receives decrypted tokens transparently

### Migration Process
- **Detection**: Automatically detects old vs new encryption format
- **Fallback Handling**: Gracefully handles mixed credential formats
- **Validation**: Verifies migration success through decryption tests
- **Batch Processing**: Processes credentials in configurable batches

## 🧪 Testing

Test files for Jira services:
```bash
# Test Jira client encryption
python -m pytest tests/test_jira_client_encryption.py -v

# Test sync service encryption  
python -m pytest tests/test_jira_sync_encryption.py -v

# All Jira-related tests
python -m pytest tests/ -k "jira" -v
```

## 🔧 Configuration

### Environment Variables
```bash
# Required for Jira integration
SUPABASE_URL=your-supabase-url
SUPABASE_ANON_KEY=your-supabase-key
ENCRYPTION_SECRET_KEY=your-encryption-key
```

### Database Tables
- `integration_credentials`: Stores encrypted Jira credentials
- `integration_mappings`: Maps between Jira and CogniSim items
- `sync_logs`: Tracks synchronization operations

## 🚨 Error Handling

### Common Errors
- **Authentication Failed**: Invalid Jira credentials
- **Connection Timeout**: Network connectivity issues
- **Rate Limit Exceeded**: Too many API requests
- **Encryption Error**: Invalid or corrupted encrypted tokens

### Error Recovery
- **Retry Logic**: Automatic retries for transient failures
- **Graceful Degradation**: Continues processing other items on individual failures
- **Detailed Logging**: Comprehensive error logging for debugging

## 📊 Performance

### Rate Limiting
- **Default Interval**: 0.5 seconds between requests
- **Configurable**: Adjustable via `min_request_interval`
- **Jira Compliance**: Respects Atlassian API rate limits

### Batch Processing
- **Sync Operations**: Process multiple issues efficiently
- **Database Operations**: Bulk database operations where possible
- **Memory Management**: Streaming for large datasets

## 🔄 Migration Guide

### From Old Encoding
If you have existing credentials with simple Base64 encoding:

```python
# Run migration
service = JiraSyncService(supabase_client)
result = await service.migrate_credentials_to_encryption()

# Check results
print(f"Migrated: {result['migrated_count']}")
print(f"Failed: {result['failed_count']}")
```

### Validation
```python
# Validate all credentials are properly encrypted
python migrate_credentials.py --validate
```

## 🛠️ Development

### Adding New Jira Features
1. **Add method to JiraClient**: Implement new Jira API call
2. **Update JiraSyncService**: Add business logic for new feature
3. **Add field mapping**: Update JiraFieldMapper if needed
4. **Write tests**: Add comprehensive test coverage
5. **Update documentation**: Document new functionality

### Code Style
- Follow existing patterns for encryption handling
- Use async/await for database operations
- Include comprehensive error handling
- Add detailed logging for debugging
