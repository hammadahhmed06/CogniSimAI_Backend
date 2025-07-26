# Tests Directory

# Testing Guide

Comprehensive testing framework and practices for the CogniSim AI Backend.

## 📁 Test Structure

```
tests/
├── test_token_encryption.py      # Core encryption service tests (4 tests)
├── test_jira_client_encryption.py # Jira client encryption tests (7 tests)  
├── test_jira_sync_encryption.py  # Sync service encryption tests (5 tests)
├── test_migration_script.py      # Migration script tests (12 tests)
├── test_api_endpoints.py         # API endpoint tests
├── test_authenticated_apis.py    # Authentication tests
├── test_database_fix.py          # Database operation tests
├── test_direct_integration.py    # Direct integration tests
└── test_jira_integration.py      # Legacy Jira integration tests
```

**Current Focus**: 28 encryption-related tests across core components

## 🧪 Test Categories

### 1. Core Encryption Tests (`test_token_encryption.py`)

Tests the fundamental encryption service functionality.

**Test Functions:**
- `test_basic_encryption()`: Round-trip encryption/decryption
- `test_error_handling()`: Invalid inputs and edge cases
- `test_singleton_service()`: Service instance management
- `test_jira_token_format()`: Real Jira token format handling

**Coverage:**
- ✅ AES-256-GCM encryption/decryption
- ✅ Error handling for invalid inputs
- ✅ Singleton pattern validation
- ✅ Various token format support

### 2. Jira Client Tests (`test_jira_client_encryption.py`)

Tests JiraClient integration with encryption services.

**Test Functions:**
- `test_jira_client_with_encrypted_token()`: Client with encrypted credentials
- `test_jira_client_with_plaintext_token()`: Client with plaintext credentials
- `test_from_encrypted_credentials_factory()`: Factory method for encrypted creds
- `test_from_plaintext_credentials_factory()`: Factory method for plaintext creds
- `test_invalid_encrypted_token_raises_error()`: Error handling for invalid tokens
- `test_encryption_service_error_handling()`: Service error scenarios
- `test_backward_compatibility()`: Existing code compatibility

**Coverage:**
- ✅ Encrypted credential handling
- ✅ Factory method functionality
- ✅ Error handling and validation
- ✅ Backward compatibility assurance

### 3. Sync Service Tests (`test_jira_sync_encryption.py`)

Tests JiraSyncService encryption integration and database operations.

**Test Functions:**
- `test_save_credentials_with_encryption()`: Credential saving with encryption
- `test_sync_with_encrypted_credentials()`: Sync operations with encryption
- `test_migration_from_old_encoding()`: Migration from legacy encoding
- `test_migration_skips_already_encrypted()`: Migration skip logic
- `test_no_credentials_to_migrate()`: Empty migration scenarios

**Coverage:**
- ✅ Database integration with encryption
- ✅ Credential migration logic
- ✅ Sync operations with encrypted tokens
- ✅ Edge cases and error scenarios

### 4. Migration Script Tests (`test_migration_script.py`)

Tests the production migration script functionality.

**Test Functions:**
- `test_get_all_credentials()`: Database credential retrieval
- `test_analyze_credential_already_encrypted()`: Encrypted credential detection
- `test_analyze_credential_needs_migration()`: Migration need detection
- `test_analyze_credential_decode_fails()`: Decode failure handling
- `test_migrate_credential_success()`: Successful migration
- `test_migrate_credential_no_migration_needed()`: Skip logic
- `test_migrate_credential_no_plaintext()`: Error handling
- `test_run_migration_complete_flow()`: End-to-end migration
- `test_run_migration_mixed_credentials()`: Mixed credential types
- `test_validate_migration_success()`: Migration validation
- `test_validate_migration_with_failures()`: Validation error handling
- `test_batch_processing()`: Batch processing configuration

**Coverage:**
- ✅ Complete migration workflow
- ✅ Batch processing logic
- ✅ Validation and error handling
- ✅ Mixed credential scenarios

## 🚀 Running Tests

### All Tests
```bash
# Run complete test suite
python -m pytest

# Run with verbose output
python -m pytest -v

# Run with coverage
python -m pytest --cov=app
```

### Specific Test Categories
```bash
# Core encryption tests
python -m pytest tests/test_token_encryption.py -v

# Jira client tests
python -m pytest tests/test_jira_client_encryption.py -v

# Sync service tests  
python -m pytest tests/test_jira_sync_encryption.py -v

# Migration tests
python -m pytest tests/test_migration_script.py -v
```

### Filtered Tests
```bash
# All encryption-related tests
python -m pytest -k "encryption" -v

# All Jira-related tests
python -m pytest -k "jira" -v

# All migration tests
python -m pytest -k "migration" -v
```

### Test Output Control
```bash
# Short output
python -m pytest --tb=short

# No output capture (see print statements)
python -m pytest -s

# Stop on first failure
python -m pytest -x
```

## 🔧 Test Configuration

### pytest Configuration
Create `pytest.ini` (optional):
```ini
[tool:pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
```

### Async Testing
For async tests, ensure pytest-asyncio is installed:
```bash
pip install pytest-asyncio
```

Tests use `@pytest.mark.asyncio` decorator:
```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_function()
    assert result is not None
```

## 🎭 Mocking and Fixtures

### Common Mocking Patterns

#### Database Mocking
```python
from unittest.mock import Mock, patch

@patch('app.services.jira.jira_sync_service.supabase_client')
def test_with_mocked_db(mock_supabase):
    mock_supabase.table.return_value.select.return_value.execute.return_value.data = []
    # Test logic here
```

#### Service Mocking
```python
@patch('app.services.jira.jira_client.JiraClient')
def test_with_mocked_client(mock_client):
    mock_client.return_value.connect.return_value = (True, "Success")
    # Test logic here
```

### Test Fixtures
```python
@pytest.fixture
def encryption_service():
    return get_token_encryption_service()

@pytest.fixture
def sample_token():
    return "ATATT3xFfGF0T5JNjBdN-QhWDmAEI7YIjKLMNO"

def test_with_fixtures(encryption_service, sample_token):
    encrypted = encryption_service.encrypt(sample_token)
    assert encrypted != sample_token
```

## 📊 Test Coverage

### Current Coverage
- **Core Encryption**: 100% (4/4 tests passing)
- **Jira Client**: 100% (7/7 tests passing)
- **Sync Service**: 100% (5/5 tests passing)
- **Migration Script**: 100% (12/12 tests passing)
- **Total**: 28/28 tests passing (100%)

### Coverage Report
```bash
# Generate coverage report
python -m pytest --cov=app --cov-report=html

# View report
open htmlcov/index.html
```

### Coverage Configuration
```bash
# .coveragerc file
[run]
source = app
omit = 
    */tests/*
    */venv/*
    */__pycache__/*

[report]
exclude_lines =
    pragma: no cover
    def __repr__
    raise AssertionError
    raise NotImplementedError
```

## 🔍 Test Data

### Sample Test Data
```python
# Test tokens
TEST_TOKENS = [
    "ATATT3xFfGF0abc123XYZ",                    # Standard format
    "simple-test-token-123",                    # Simple format
    "complex!@#$%^&*()token-with-symbols",     # Special characters
    "very-long-token-that-exceeds-normal-length" # Long token
]

# Test credentials
TEST_CREDENTIALS = {
    "workspace_id": "test-workspace-123",
    "jira_url": "https://test.atlassian.net",
    "jira_email": "test@example.com",
    "jira_api_token": "test-token"
}
```

### Test Database Setup
```python
def setup_test_db():
    """Setup test database with sample data"""
    # Create test tables
    # Insert sample data
    pass

def teardown_test_db():
    """Clean up test database"""
    # Remove test data
    # Drop test tables
    pass
```

## 🚨 Test Best Practices

### Test Structure
```python
def test_function_name():
    # Arrange: Set up test data
    service = get_service()
    test_input = "test_data"
    
    # Act: Execute the function
    result = service.process(test_input)
    
    # Assert: Verify the result
    assert result is not None
    assert result.success is True
```

### Test Naming
- **Descriptive Names**: `test_encrypt_token_with_valid_input()`
- **Behavior Description**: `test_decrypt_raises_error_for_invalid_token()`
- **Scenario Specific**: `test_migration_skips_already_encrypted_credentials()`

### Test Organization
- **One Concept Per Test**: Each test should test one specific behavior
- **Independent Tests**: Tests should not depend on each other
- **Clear Setup**: Use fixtures for common setup code
- **Clean Teardown**: Ensure proper cleanup after tests

### Error Testing
```python
def test_error_handling():
    service = get_service()
    
    # Test for specific exception
    with pytest.raises(ValueError, match="Token cannot be empty"):
        service.encrypt("")
    
    # Test for any exception
    with pytest.raises(Exception):
        service.decrypt("invalid_data")
```

## 🔄 Continuous Integration

### GitHub Actions (Example)
```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: 3.13
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        pip install pytest pytest-cov
    - name: Run tests
      run: pytest --cov=app
```

### Pre-commit Hooks
```bash
# Install pre-commit
pip install pre-commit

# Setup hooks
pre-commit install

# Run manually
pre-commit run --all-files
```

## 🛠️ Development Testing

### Test-Driven Development (TDD)
1. **Write Test First**: Create failing test for new functionality
2. **Implement Feature**: Write minimal code to pass the test
3. **Refactor**: Improve code while keeping tests passing
4. **Repeat**: Continue cycle for next feature

### Testing New Features
```python
# Step 1: Write failing test
def test_new_feature():
    service = get_service()
    result = service.new_feature("input")
    assert result == "expected_output"

# Step 2: Implement feature
def new_feature(self, input_data):
    # Implementation here
    return "expected_output"

# Step 3: Ensure test passes
# Step 4: Refactor if needed
```

### Debug Tests
```bash
# Run specific test with debugging
python -m pytest tests/test_file.py::test_function -v -s

# Use Python debugger
import pdb; pdb.set_trace()

# Use pytest debugging
python -m pytest --pdb
```

## 📋 Test Checklist

Before submitting code, ensure:

- [ ] All tests pass locally
- [ ] New functionality has tests
- [ ] Edge cases are covered
- [ ] Error scenarios are tested
- [ ] Mock external dependencies
- [ ] Tests are independent
- [ ] Documentation is updated
- [ ] No hardcoded values in tests
- [ ] Descriptive test names used
- [ ] Coverage remains high

## 🔧 Troubleshooting

### Common Test Issues

**"ImportError: No module named 'app'"**
```bash
# Add to pytest.ini or run with
PYTHONPATH=. python -m pytest
```

**"Async tests not running"**
```bash
# Install async support
pip install pytest-asyncio

# Use decorator
@pytest.mark.asyncio
async def test_async_function():
    pass
```

**"Database connection errors"**
```bash
# Mock database connections
@patch('app.database.get_client')
def test_with_mocked_db(mock_client):
    pass
```

**"Tests running slowly"**
```bash
# Run in parallel
pip install pytest-xdist
python -m pytest -n auto
```

### Debug Test Output
```bash
# See all output
python -m pytest -v -s

# See specific test
python -m pytest tests/test_file.py::test_name -v -s
```

## 📁 Test Files

### Core Integration Tests
- **`test_jira_integration.py`** - Interactive test for Jira client functionality
  - Tests Jira connection with user-provided credentials
  - Validates project fetching and issue retrieval
  - Tests credential encoding/decoding

- **`test_database_fix.py`** - Database workspace validation test
  - Tests database foreign key constraints
  - Validates workspace ID resolution
  - Tests credential storage in database

- **`test_direct_integration.py`** - Direct integration test (bypasses API)
  - Tests core integration functionality without authentication
  - Validates sync service operations
  - Tests credential management

### API Tests
- **`test_api_endpoints.py`** - Comprehensive API endpoint testing
  - Tests all Jira integration endpoints
  - Validates API documentation accessibility
  - Tests OpenAPI specification
  - Validates authentication requirements

## 🚀 Running Tests

### Run All Tests
```bash
cd cognisim_ai_backend
python tests/run_tests.py --test all
```

### Run Individual Tests
```bash
# Test Jira integration
python tests/run_tests.py --test jira

# Test database functionality
python tests/run_tests.py --test database

# Test API endpoints
python tests/run_tests.py --test api

# Test direct integration
python tests/run_tests.py --test direct
```

### List Available Tests
```bash
python tests/run_tests.py --list
```

### Run Tests Manually
```bash
# Run individual test files directly
python tests/test_jira_integration.py
python tests/test_database_fix.py
python tests/test_api_endpoints.py
python tests/test_direct_integration.py
```

## 📋 Test Requirements

### Prerequisites
1. **Environment Setup**:
   - `.env` file with proper Supabase credentials
   - Virtual environment activated
   - All dependencies installed (`pip install -r requirements.txt`)

2. **Server Running** (for API tests):
   ```bash
   python run_server.py
   ```

3. **Database Setup**:
   - Supabase database with proper schema
   - Workspace ID `84e53826-b670-41fa-96d3-211ebdbc080c` exists

### Test Data
- Tests use placeholder Jira credentials by default
- For real testing, replace with valid Jira API tokens
- Some tests require interactive input (Jira credentials)

## 🎯 Test Coverage

### ✅ What's Tested
- Jira API connection and authentication
- Project and issue fetching from Jira
- Database credential storage and retrieval
- Workspace ID resolution and foreign key constraints
- API endpoint availability and structure
- Rate limiting and authentication middleware
- Error handling and logging
- Credential encoding/decoding

### 🔄 Test Flow
1. **Unit Tests**: Individual component testing
2. **Integration Tests**: End-to-end workflow testing
3. **API Tests**: HTTP endpoint validation
4. **Database Tests**: Data persistence validation

## 📊 Expected Results

### Successful Test Run
```
🚀 CogniSim AI Jira Integration Test Suite
============================================================
Running all available tests...

🧪 Running Jira Integration Test
✅ Jira Integration Test completed successfully

🧪 Running Database Fix Test  
✅ Database Fix Test completed successfully

🧪 Running API Endpoints Test
✅ API Endpoints Test completed successfully

🧪 Running Direct Integration Test
✅ Direct Integration Test completed successfully

📊 TEST SUMMARY
============================================================
Jira Integration Test          ✅ PASS
Database Fix Test              ✅ PASS
API Endpoints Test             ✅ PASS
Direct Integration Test        ✅ PASS

🎯 Overall Result: 4/4 tests passed
🎉 All tests passed! Your integration is working correctly.
```

## 🐛 Troubleshooting

### Common Issues
1. **Import Errors**: Ensure you're running from the correct directory
2. **Database Connection**: Check Supabase credentials in `.env`
3. **Server Not Running**: Start server before running API tests
4. **Invalid Credentials**: Update test files with valid Jira tokens

### Debug Mode
To debug issues, run tests individually and check the output:
```bash
python tests/test_jira_integration.py
```

## 📝 Adding New Tests

1. Create test file in `tests/` directory
2. Use naming convention: `test_<feature_name>.py`
3. Add import path adjustment for app modules:
   ```python
   import sys
   from pathlib import Path
   sys.path.append(str(Path(__file__).parent.parent))
   ```
4. Update `run_tests.py` to include your new test
5. Document the test in this README

---

*Last Updated: July 24, 2025*
