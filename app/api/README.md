# API Documentation

This directory contains all REST API endpoints and routing logic for the CogniSim AI Backend.

## 📁 Structure

```
api/
├── __init__.py
└── routes/
    ├── __init__.py
    └── integrations.py    # Jira integration endpoints
```

## 🛣️ Routes Overview

### Integration Routes (`routes/integrations.py`)

Handles all third-party integration endpoints, currently focused on Jira integration.

#### Base Path: `/api/integrations`

### Jira Integration Endpoints

#### `POST /api/integrations/jira/credentials`
Save and test Jira credentials with automatic encryption.

**Request Body:**
```json
{
  "workspace_id": "string",
  "jira_url": "https://your-domain.atlassian.net",
  "jira_email": "user@domain.com", 
  "jira_api_token": "your_api_token"
}
```

**Response (Success):**
```json
{
  "success": true,
  "message": "Connection successful",
  "connection_status": "CONNECTED",
  "integration_id": "uuid"
}
```

**Response (Error):**
```json
{
  "success": false,
  "message": "Invalid credentials",
  "connection_status": "FAILED"
}
```

**Security:**
- API token automatically encrypted before storage
- Connection tested before saving
- Rate limited to prevent abuse

#### `GET /api/integrations/jira/status/{workspace_id}`
Get the current status of Jira integration for a workspace.

**Parameters:**
- `workspace_id` (path): The workspace identifier

**Response:**
```json
{
  "integration_id": "uuid",
  "connection_status": "CONNECTED",
  "last_tested_at": "2025-01-26T12:00:00Z",
  "jira_url": "https://domain.atlassian.net",
  "jira_email": "user@domain.com"
}
```

#### `POST /api/integrations/jira/sync`
Synchronize Jira project data with CogniSim.

**Request Body:**
```json
{
  "workspace_id": "string",
  "project_id": "string", 
  "jira_project_key": "PROJ"
}
```

**Response:**
```json
{
  "success": true,
  "sync_log_id": "uuid",
  "items_synced": 25,
  "items_created": 20,
  "items_updated": 5,
  "errors_count": 0,
  "sync_status": "COMPLETED"
}
```

**Features:**
- Automatically uses encrypted credentials
- Creates detailed sync logs
- Handles errors gracefully
- Returns comprehensive statistics

## 🔒 Authentication

All endpoints require authentication via JWT tokens.

### Headers
```
Authorization: Bearer <jwt_token>
Content-Type: application/json
```

### Authentication Flow
1. **Login**: Get JWT token from auth endpoints
2. **Include Token**: Add to Authorization header
3. **Token Validation**: Automatic validation on each request
4. **Refresh**: Refresh token before expiry

## 📝 Request/Response Format

### Standard Request Format
```json
{
  "required_field": "value",
  "optional_field": "value"
}
```

### Standard Response Format
```json
{
  "success": boolean,
  "message": "string",
  "data": {},
  "error_code": "string (optional)"
}
```

### Error Response Format
```json
{
  "success": false,
  "message": "Detailed error message",
  "error_code": "VALIDATION_ERROR",
  "details": {
    "field": "Error description"
  }
}
```

## 🚦 Rate Limiting

### Default Limits
- **General Endpoints**: 100 requests per minute
- **Integration Endpoints**: 10 requests per minute
- **Sync Operations**: 1 request per minute

### Headers
Rate limit information included in response headers:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1642781234
```

### Rate Limit Exceeded
```json
{
  "success": false,
  "message": "Rate limit exceeded",
  "error_code": "RATE_LIMIT_EXCEEDED",
  "retry_after": 60
}
```

## ✅ Status Codes

### Success Codes
- `200 OK`: Request successful
- `201 Created`: Resource created successfully
- `202 Accepted`: Request accepted for processing

### Client Error Codes
- `400 Bad Request`: Invalid request format/data
- `401 Unauthorized`: Authentication required
- `403 Forbidden`: Insufficient permissions
- `404 Not Found`: Resource not found
- `409 Conflict`: Resource conflict
- `422 Unprocessable Entity`: Validation error
- `429 Too Many Requests`: Rate limit exceeded

### Server Error Codes
- `500 Internal Server Error`: Server error
- `502 Bad Gateway`: Upstream service error
- `503 Service Unavailable`: Service temporarily unavailable

## 🔍 Validation

### Request Validation
All endpoints include comprehensive input validation:

```python
# Example validation schema
{
  "workspace_id": {
    "type": "string",
    "required": True,
    "min_length": 1,
    "max_length": 100
  },
  "jira_url": {
    "type": "string", 
    "required": True,
    "format": "url",
    "pattern": r"https://.*\.atlassian\.net"
  }
}
```

### Validation Errors
```json
{
  "success": false,
  "message": "Validation failed",
  "error_code": "VALIDATION_ERROR",
  "details": {
    "jira_url": "Invalid URL format",
    "workspace_id": "Field is required"
  }
}
```

## 🧪 Testing

### Test Endpoints
```bash
# Test specific route
python -m pytest tests/test_api_routes.py::test_jira_credentials -v

# Test all API routes
python -m pytest tests/ -k "api" -v
```

### Example API Test
```python
def test_save_jira_credentials(client):
    response = client.post("/api/integrations/jira/credentials", json={
        "workspace_id": "test-workspace",
        "jira_url": "https://test.atlassian.net",
        "jira_email": "test@test.com",
        "jira_api_token": "test-token"
    })
    assert response.status_code == 200
    assert response.json()["success"] == True
```

## 📊 Monitoring

### Health Check
```
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-01-26T12:00:00Z",
  "version": "1.0.0",
  "services": {
    "database": "healthy",
    "encryption": "healthy"
  }
}
```

### Metrics
- **Request Count**: Total API requests
- **Response Time**: Average response times
- **Error Rate**: Error rate by endpoint
- **Rate Limit Usage**: Rate limit utilization

## 🔧 Configuration

### Environment Variables
```bash
# API Configuration
RATE_LIMIT_ENABLED=true
MAX_REQUESTS_PER_MINUTE=100
JWT_SECRET_KEY=your-secret-key

# CORS Settings
ALLOWED_ORIGINS=http://localhost:3000,https://yourdomain.com
```

### CORS Configuration
```python
# Allowed origins for cross-origin requests
origins = [
    "http://localhost:3000",  # Development frontend
    "https://yourdomain.com"  # Production frontend
]
```

## 🚨 Error Handling

### Global Error Handler
All unhandled exceptions are caught and return consistent error responses:

```json
{
  "success": false,
  "message": "An internal error occurred",
  "error_code": "INTERNAL_ERROR",
  "timestamp": "2025-01-26T12:00:00Z"
}
```

### Custom Exceptions
```python
class ValidationError(Exception):
    """Raised when request validation fails"""
    pass

class AuthenticationError(Exception):
    """Raised when authentication fails"""
    pass

class RateLimitError(Exception):
    """Raised when rate limit is exceeded"""
    pass
```

## 📚 API Examples

### Save Jira Credentials
```bash
curl -X POST http://localhost:8000/api/integrations/jira/credentials \
  -H "Authorization: Bearer your-jwt-token" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "workspace-123",
    "jira_url": "https://mycompany.atlassian.net",
    "jira_email": "user@mycompany.com",
    "jira_api_token": "ATATT3xFfGF0..."
  }'
```

### Get Integration Status
```bash
curl -X GET http://localhost:8000/api/integrations/jira/status/workspace-123 \
  -H "Authorization: Bearer your-jwt-token"
```

### Sync Project Data
```bash
curl -X POST http://localhost:8000/api/integrations/jira/sync \
  -H "Authorization: Bearer your-jwt-token" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "workspace-123",
    "project_id": "project-456",
    "jira_project_key": "MYPROJ"
  }'
```

## 🛠️ Development

### Adding New Endpoints
1. **Define Route**: Add route to appropriate router file
2. **Add Validation**: Define request/response schemas
3. **Implement Logic**: Add business logic
4. **Add Tests**: Comprehensive test coverage
5. **Update Documentation**: Document new endpoint

### Code Structure
```python
@router.post("/new-endpoint")
async def new_endpoint(
    request: RequestModel,
    current_user: User = Depends(get_current_user)
):
    """Endpoint description"""
    try:
        # Validation
        validate_request(request)
        
        # Business logic
        result = await service.process_request(request)
        
        # Response
        return {
            "success": True,
            "data": result
        }
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Endpoint error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
```

### Best Practices
- **Consistent Naming**: Use consistent endpoint naming
- **Proper Status Codes**: Return appropriate HTTP status codes
- **Comprehensive Validation**: Validate all inputs
- **Error Handling**: Handle all exceptions gracefully
- **Documentation**: Document all endpoints thoroughly
