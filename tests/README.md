# Tests Directory

This directory contains all test files for the CogniSim AI Jira Integration system.

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
