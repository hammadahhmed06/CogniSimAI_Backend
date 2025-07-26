# CogniSim AI Backend

A secure FastAPI backend service for Jira integration with advanced encryption capabilities.

## 🚀 Quick Start

### Prerequisites
- Python 3.13+
- PostgreSQL database (Supabase)
- Git

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/hammadahhmed06/cognisim_ai_backend.git
   cd cognisim_ai_backend
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # Linux/Mac
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Run the server**
   ```bash
   python run_server.py
   ```

## 🔧 Environment Configuration

Required environment variables in `.env`:

```bash
# Database
SUPABASE_URL=your-supabase-url
SUPABASE_ANON_KEY=your-supabase-anon-key

# Security
JWT_SECRET_KEY=your-jwt-secret-key
ENCRYPTION_SECRET_KEY=your-32-byte-encryption-key

# Optional
RATE_LIMIT_ENABLED=true
LOG_LEVEL=INFO
```

### Generating Encryption Key
```bash
python -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

## 🏗️ Project Structure

```
cognisim_ai_backend/
├── app/                    # Main application code
│   ├── api/               # API routes and endpoints
│   ├── core/              # Core configuration and settings
│   ├── models/            # Data models and schemas
│   └── services/          # Business logic and integrations
├── tests/                 # Test suite
├── migrate_credentials.py # Production migration script
├── run_server.py         # Application entry point
└── requirements.txt      # Python dependencies
```

## 🔒 Security Features

- **JWT Authentication**: Secure user authentication
- **AES-256-GCM Encryption**: Military-grade encryption for API tokens
- **Rate Limiting**: Protection against abuse
- **Input Validation**: Comprehensive request validation
- **Secure Headers**: Security-first HTTP headers

## 🧪 Testing

Run the complete test suite:
```bash
# All tests
python -m pytest

# With coverage
python -m pytest --cov=app

# Specific test files
python -m pytest tests/test_token_encryption.py -v
```

**Test Coverage**: 28/28 tests passing across all components

## 📚 Documentation

- **[Jira Services](./app/services/jira/README.md)** - Jira integration and encryption
- **[Encryption Services](./app/services/encryption/README.md)** - Token encryption details
- **[API Documentation](./app/api/README.md)** - REST API endpoints
- **[Testing Guide](./tests/README.md)** - Testing framework and practices

## 🚀 Deployment

### Development
```bash
python run_server.py
```

### Production Migration
For migrating existing credentials to encryption:
```bash
# Preview migration
python migrate_credentials.py --dry-run

# Execute migration
python migrate_credentials.py

# Validate results
python migrate_credentials.py --validate
```

### Docker (Optional)
```bash
docker build -t cognisim-backend .
docker run -p 8000:8000 --env-file .env cognisim-backend
```

## 📋 API Endpoints

- **Health Check**: `GET /health`
- **Authentication**: `POST /auth/*`
- **Jira Integration**: `POST /api/integrations/jira/*`
- **Rate Limited**: All endpoints protected

## 🔧 Development

### Adding New Features
1. Create feature branch: `git checkout -b feature/your-feature`
2. Add tests first (TDD approach)
3. Implement functionality
4. Update documentation
5. Submit pull request

### Code Quality
- Follow PEP 8 style guidelines
- Add type hints where possible
- Write comprehensive tests
- Update documentation

## 🐛 Troubleshooting

### Common Issues

**"Import slowapi could not be resolved"**
```bash
pip install slowapi
```

**"Encryption key not configured"**
- Ensure `ENCRYPTION_SECRET_KEY` is set in `.env`
- Key must be 32 bytes (256 bits)

**Database connection issues**
- Verify Supabase URL and key
- Check network connectivity
- Ensure database tables exist

### Logs
Application logs are written to console with configurable levels via `LOG_LEVEL` environment variable.

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License.

## 🆘 Support

For issues and support:
- Check existing documentation in folder-specific README files
- Review test cases for usage examples
- Create an issue on GitHub with detailed information
