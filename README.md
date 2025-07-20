# CogniSim AI Backend - Jira Integration

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![Supabase](https://img.shields.io/badge/Supabase-Database-green.svg)](https://supabase.com)

A comprehensive Jira integration system for CogniSim AI, enabling seamless synchronization between Jira projects and the CogniSim platform.

## 🚀 Features

### ✨ Core Functionality
- **Jira Connection Management**: Secure credential storage and connection testing
- **Real-time Sync**: Bi-directional synchronization of issues and projects
- **Field Mapping**: Intelligent mapping between Jira and CogniSim data models
- **Error Handling**: Comprehensive error handling with detailed logging
- **Rate Limiting**: Built-in rate limiting to respect Jira API limits

### 🔐 Security
- **Credential Encryption**: Secure storage of Jira API tokens
- **JWT Authentication**: Role-based access control
- **Environment Configuration**: Secure configuration management

### 🏗️ Architecture
- **FastAPI Backend**: High-performance async API framework
- **Supabase Integration**: PostgreSQL database with real-time capabilities
- **Modular Design**: Clean separation of concerns with service layers

## 📋 Prerequisites

- Python 3.11+
- Jira Cloud instance with API access
- Supabase project
- Valid Jira API token

## 🛠️ Installation

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd cognisim_ai_backend
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   # or
   source .venv/bin/activate  # Linux/Mac
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment setup**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

## ⚙️ Configuration

### Environment Variables

```properties
# Supabase Configuration
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
SUPABASE_ANON_KEY=your_anon_key

# CORS Origins
CORS_ORIGINS=["http://localhost:3000"]

# Optional: Encryption (for production)
ENCRYPTION_SECRET_KEY=your_secret_key
ENCRYPTION_SALT=your_salt
```

### Database Tables

Ensure these tables exist in your Supabase database:

```sql
-- Integration credentials
CREATE TABLE integration_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES teams(id),
    integration_type TEXT NOT NULL,
    jira_url TEXT,
    jira_email TEXT,
    jira_api_token_encrypted TEXT,
    is_active BOOLEAN DEFAULT true,
    connection_status TEXT DEFAULT 'pending',
    last_tested_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Sync logs
CREATE TABLE sync_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL,
    project_id UUID,
    integration_type TEXT NOT NULL,
    sync_type TEXT DEFAULT 'manual',
    status TEXT DEFAULT 'in_progress',
    items_synced INTEGER DEFAULT 0,
    items_created INTEGER DEFAULT 0,
    items_updated INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    sync_details JSONB,
    error_details JSONB,
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

-- Integration mappings
CREATE TABLE integration_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL,
    external_system TEXT NOT NULL,
    external_item_id TEXT NOT NULL,
    external_url TEXT,
    last_synced_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

## 🚀 Running the Application

### Development Server
```bash
python run_server.py
```

The server will start on `http://localhost:8000`

### API Documentation
Access the interactive API documentation at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## 📡 API Endpoints

### Jira Integration

#### Connect Jira
```http
POST /api/integrations/jira/connect
```
**Request Body:**
```json
{
  "jira_url": "https://yourcompany.atlassian.net",
  "jira_email": "your@email.com",
  "jira_api_token": "your_api_token"
}
```

#### Test Connection
```http
GET /api/integrations/jira/test
```

#### Get Integration Status
```http
GET /api/integrations/jira/status
```

#### Sync Project
```http
POST /api/integrations/jira/sync/{project_id}
```
**Request Body:**
```json
{
  "jira_project_key": "PROJ",
  "max_results": 50
}
```

## 🧪 Testing

### Unit Tests
```bash
python test_jira_integration.py
```

### API Testing
Use the provided test script:
```bash
python test_jira_apis.py
```

Or test manually with tools like:
- Postman
- curl
- FastAPI interactive docs

## 📊 Field Mapping

### Jira → CogniSim Mapping

| Jira Field | CogniSim Field | Notes |
|------------|----------------|-------|
| summary | title | Issue title |
| description | description | Issue description |
| status.name | status | Mapped to: todo, in_progress, done |
| priority.name | priority | Mapped to: low, medium, high, critical |
| assignee | assignee_id | User email |
| reporter | reporter_id | User email |
| issuetype.name | item_type | story, task, bug, epic, subtask |
| labels | labels | Array of strings |
| customfield_* | story_points | Story points from custom fields |

## 🔍 Monitoring & Logging

### Log Levels
- **INFO**: Normal operations, connection status
- **WARNING**: Non-critical issues, missing data
- **ERROR**: Failed operations, API errors

### Common Log Messages
- `Connected to Jira as {user}` - Successful connection
- `Failed to sync project {key}` - Sync operation failed
- `Credential encoded successfully` - Credential storage completed

## 🐛 Troubleshooting

### Common Issues

**Connection Failed: Invalid credentials**
- Verify Jira URL format: `https://yourcompany.atlassian.net`
- Check API token validity
- Ensure email matches Jira account

**Foreign Key Constraint Error**
- Verify workspace ID exists in teams table
- Check user is associated with workspace

**Rate Limit Exceeded**
- Built-in rate limiting prevents this
- If occurs, wait and retry

### Debug Mode
Set environment variable for detailed logging:
```bash
export DEBUG=true
```

## 🔒 Security Considerations

- **API Tokens**: Never commit API tokens to version control
- **Environment Variables**: Use `.env` file for sensitive data
- **Database**: Use connection pooling and prepared statements
- **Rate Limiting**: Implemented to prevent API abuse

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## 📄 License

This project is part of the CogniSim AI platform.

## 👥 Team

- **Developer**: Hammad Ahmed
- **Email**: hammadahhmed06@gmail.com

## 🎯 Roadmap

- [ ] Webhook support for real-time sync
- [ ] Advanced field mapping customization
- [ ] Multiple Jira instance support
- [ ] Bulk operations optimization
- [ ] Integration with other project management tools

---

**Successfully tested with real Jira data** ✅

Last updated: July 20, 2025
